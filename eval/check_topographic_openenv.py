"""Compare only the topographic localhost OpenEnv server against its local core."""
import argparse
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys

from eval.check_openenv_integration import (ROOT, UnsupportedRuntime,
    compare_exported_trajectories, local_environment, localhost_server, require_equal)


def replay_export(trace, create_environment):
    environment = create_environment()
    environment.reset(trace['task_id'], trace['seed'])
    for transition in trace['transitions']:
        environment.step(transition['action'])
    require_equal(environment.trajectory(), trace, trace['task_id'] + ' exported exact replay')
    verified = environment.verify_episode()
    if verified.get('passed') is not True:
        raise RuntimeError(trace['task_id'] + ': exported episode verification failed')
    return verified


def compare_episode(client, create_environment, identifier, trajectory_directory, *, diagnostic=False):
    from wildfire_core.prepared import ProtectionBaseline
    from wildfire_openenv.prepared import PreparedAction

    local = create_environment()
    observation, info = local.reset(identifier)
    remote = client.reset(task_id=identifier)
    require_equal(remote.observation.data, observation, identifier + ' reset observation')
    require_equal(remote.observation.metadata, info, identifier + ' reset metadata')
    require_equal(remote.reward, 0.0, identifier + ' reset reward')
    require_equal(remote.done, False, identifier + ' reset done')
    policy = ProtectionBaseline(prefer_shelter=False)
    total_reward = 0.0
    actions = []
    for decision in range(60):
        action = {} if diagnostic else policy(observation)
        wire_action = PreparedAction(**action)
        require_equal(wire_action.core_action(), action, identifier + ' public action')
        actions.append(json.loads(json.dumps(action, allow_nan=False)))
        check = local.check_orders(action)
        checked = client.step(PreparedAction(operation='check', **action))
        require_equal(checked.observation.metadata, {'action_check': check}, identifier + ' action check')
        require_equal(checked.observation.data, local.get_situation(), identifier + ' check observation')
        require_equal(checked.reward, 0.0, identifier + ' check reward')
        if not check['valid']:
            raise RuntimeError(f'{identifier}: scripted action was invalid: {check}')
        situation = client.step(PreparedAction(operation='situation'))
        require_equal(situation.observation.data, local.get_situation(), identifier + ' public situation')
        require_equal(situation.reward, 0.0, identifier + ' situation reward')
        observation, reward, terminated, truncated, info = local.step(action)
        remote = client.step(wire_action)
        label = f'{identifier} decision {decision + 1}'
        require_equal(remote.observation.data, observation, label + ' observation')
        require_equal(remote.observation.metadata, info, label + ' metadata')
        require_equal(remote.reward, reward, label + ' reward')
        require_equal(remote.done, terminated or truncated, label + ' done')
        require_equal(remote.observation.terminated, terminated, label + ' terminated')
        require_equal(remote.observation.truncated, truncated, label + ' truncated')
        require_equal(client.state().model_dump(),
                      dict(episode_id=identifier, step_count=local._core.decisions,
                           minute=local._core.sim.tick, terminated=terminated,
                           truncated=truncated), label + ' public state')
        total_reward += reward
        if remote.done:
            break
    if not remote.done:
        raise RuntimeError(f'{identifier}: did not finish within 60 decisions')
    trajectory_result = client.step(PreparedAction(operation='trajectory'))
    trace = trajectory_result.observation.trajectory
    require_equal(trajectory_result.observation.data, local.get_situation(), identifier + ' final read')
    require_equal(trajectory_result.reward, 0.0, identifier + ' trajectory read reward')
    require_equal(trace, local.trajectory(), identifier + ' complete public trajectory')
    require_equal([record['action'] for record in trace['transitions']], actions,
                  identifier + ' committed public actions')
    require_equal(sum(record['objective_reward'] for record in trace['transitions']), total_reward,
                  identifier + ' trajectory rewards')
    require_equal(total_reward, local.reward, identifier + ' total reward')
    exports = compare_exported_trajectories(local.trajectory(), trace, trajectory_directory, identifier)
    exported = json.loads(Path(exports['server']['path']).read_text(encoding='utf-8'))
    verified = local.verify_episode()
    if verified.get('passed') is not True:
        raise RuntimeError(f'{identifier}: local episode verification failed')
    replayed = replay_export(exported, create_environment)
    require_equal(replayed, verified, identifier + ' exact verify_episode result')
    require_equal(verified['objective_score'], total_reward, identifier + ' verified reward')
    if diagnostic:
        require_equal(decision + 1, 1, identifier + ' one-action boundary probe')
        require_equal(total_reward, 0.0, identifier + ' censored reward')
        require_equal(info.get('suitable_for_training'), False, identifier + ' public training exclusion')
        require_equal(verified.get('suitable_for_training'), False, identifier + ' verified training exclusion')
        require_equal(verified.get('boundary_censored'), True, identifier + ' verified boundary censoring')
        require_equal(info.get('boundary_contact_at'), 2, identifier + ' tick-2 boundary contact')
        require_equal(terminated, False, identifier + ' diagnostic censoring')
        require_equal(truncated, True, identifier + ' diagnostic truncation')
    result = dict(task_id=identifier, seed=trace['seed'], decisions=decision + 1,
                  total_reward=total_reward, full_trajectory_equal=True, exact_replay=True,
                  exact_verify_episode=True, exported_json_roundtrip=True, exports=exports)
    if diagnostic:
        result.update(suitable_for_training=False, diagnostic=True)
    print(f'{identifier}: complete local/server trace and exact verification match', file=sys.stderr)
    return result, trace['implementation_sha256']


def topographic_worker(prepared_inputs, prepared_sha256, topographic_inputs, topographic_sha256,
                       slope_enabled, url, trajectory_directory, diagnostic_probe):
    from wildfire_core.topographic import BOUNDARY_CASE_ID, CASE_IDS, TopographicEnv
    from wildfire_openenv.prepared import PreparedClient

    def create_environment():
        return TopographicEnv(prepared_inputs, topographic_inputs,
            expected_prepared_sha256=prepared_sha256,
            expected_topographic_sha256=topographic_sha256,
            slope_enabled=slope_enabled, allow_synthetic_conditions=True)

    results = []
    implementation_sha256 = None
    with PreparedClient(base_url=url, mode='simulation', message_timeout_s=30).sync() as client:
        for identifier in CASE_IDS:
            result, pin = compare_episode(client, create_environment, identifier, trajectory_directory)
            if implementation_sha256 is None:
                implementation_sha256 = pin
            require_equal(pin, implementation_sha256, identifier + ' unchanged implementation')
            results.append(result)
        diagnostic = None
        if diagnostic_probe:
            diagnostic, pin = compare_episode(client, create_environment, BOUNDARY_CASE_ID,
                                               trajectory_directory, diagnostic=True)
            require_equal(pin, implementation_sha256, 'diagnostic unchanged implementation')
    print(json.dumps(dict(status='pass', prepared_inputs_sha256=prepared_sha256,
        topographic_inputs_sha256=topographic_sha256, slope_enabled=slope_enabled,
        implementation_sha256=implementation_sha256, cases=results, diagnostic=diagnostic,
        split='development', allow_synthetic_conditions=True,
        scope='Pinned topographic synthetic exercises; exact transport and reward replay')))
    return 0


def run_topographic(environment, prepared_inputs, prepared_sha256, topographic_inputs,
                    topographic_sha256, slope_enabled, trajectory_directory, diagnostic_probe):
    prepared_inputs, topographic_inputs = prepared_inputs.resolve(), topographic_inputs.resolve()
    slope = 'true' if slope_enabled else 'false'
    environment = dict(environment, WILDFIRE_PREPARED_INPUTS=str(prepared_inputs),
        WILDFIRE_PREPARED_SHA256=prepared_sha256,
        WILDFIRE_TOPOGRAPHIC_INPUTS=str(topographic_inputs),
        WILDFIRE_TOPOGRAPHIC_SHA256=topographic_sha256, WILDFIRE_TOPOGRAPHIC_SLOPE_ENABLED=slope)
    with localhost_server('wildfire_openenv.topographic:create_app', environment, factory=True) as url:
        command = [sys.executable, '-m', 'eval.check_topographic_openenv', '--_worker',
            '--prepared-inputs', str(prepared_inputs), '--prepared-sha256', prepared_sha256,
            '--topographic-inputs', str(topographic_inputs), '--topographic-sha256', topographic_sha256,
            '--slope-enabled', slope, '--_url', url, '--trajectory-dir', str(trajectory_directory)]
        if diagnostic_probe:
            command.append('--diagnostic-probe')
        completed = subprocess.run(command, cwd=ROOT, env=environment, text=True,
                                   capture_output=True, timeout=300)
        sys.stderr.write(completed.stderr)
        if completed.returncode:
            raise RuntimeError(f'Topographic worker failed (exit {completed.returncode}): '
                               + completed.stdout[-4000:])
        report = json.loads(completed.stdout)
        if report.get('status') != 'pass' or len(report.get('cases', [])) != 6:
            raise RuntimeError('All six topographic development cases must execute')
        if diagnostic_probe and not report.get('diagnostic', {}).get('exact_verify_episode'):
            raise RuntimeError('The boundary diagnostic must execute and replay exactly')
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--prepared-inputs', type=Path, required=True)
    parser.add_argument('--prepared-sha256', required=True)
    parser.add_argument('--topographic-inputs', type=Path, required=True)
    parser.add_argument('--topographic-sha256', required=True)
    parser.add_argument('--slope-enabled', choices=('true', 'false'), help='Omit to compare both settings')
    parser.add_argument('--diagnostic-probe', action='store_true')
    parser.add_argument('--report', type=Path)
    parser.add_argument('--trajectory-dir', type=Path,
                        default=ROOT / '.runtime/topographic-openenv-trajectories')
    parser.add_argument('--_worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--_url', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args._worker and (args.slope_enabled is None or args._url is None):
        parser.error('The worker requires an explicit slope setting and localhost URL')
    if args._worker:
        return topographic_worker(args.prepared_inputs, args.prepared_sha256,
            args.topographic_inputs, args.topographic_sha256, args.slope_enabled == 'true',
            args._url, args.trajectory_dir.resolve(), args.diagnostic_probe)
    report = dict(status='fail', python=sys.version.split()[0], settings=[])
    try:
        report['versions'] = {name: importlib.metadata.version(name)
                              for name in ('openenv', 'pydantic', 'fastapi', 'uvicorn', 'websockets', 'httpx')}
        if sys.version_info < (3, 12) or report['versions']['openenv'] != '0.7.0':
            raise UnsupportedRuntime('Use Python >=3.12 and the pinned openenv==0.7.0')
        settings = (False, True) if args.slope_enabled is None else (args.slope_enabled == 'true',)
        for slope_enabled in settings:
            directory = args.trajectory_dir.resolve() / ('slope-on' if slope_enabled else 'slope-off')
            result = run_topographic(local_environment(), args.prepared_inputs, args.prepared_sha256,
                args.topographic_inputs, args.topographic_sha256, slope_enabled, directory, args.diagnostic_probe)
            if report['settings']:
                require_equal(result['implementation_sha256'], report['settings'][0]['implementation_sha256'],
                              'unchanged implementation across slope settings')
            report['settings'].append(result)
        report['status'] = 'pass'
    except (ImportError, importlib.metadata.PackageNotFoundError, UnsupportedRuntime, PermissionError) as exception:
        report.update(status='unsupported_runtime', error=str(exception))
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exception:
        report.update(status='fail', error=str(exception))
    encoded = json.dumps(report, indent=2, sort_keys=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(encoded + '\n', encoding='utf-8')
    print(encoded)
    return {'pass': 0, 'fail': 1, 'unsupported_runtime': 2}[report['status']]


if __name__ == '__main__':
    raise SystemExit(main())
