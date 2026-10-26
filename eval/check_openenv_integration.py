"""Run optional OpenEnv checks against bounded, real localhost subprocesses."""
import argparse
from contextlib import contextmanager
import importlib.metadata
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
LEGACY_MODULES = (
    'envs.wildfire_response.tests.test_openenv',
    'envs.wildfire_response.tests.test_regional_openenv',
    'envs.wildfire_response.tests.test_regional_director_openenv',
)


class UnsupportedRuntime(RuntimeError):
    pass


def local_environment():
    environment = os.environ.copy()
    environment.update(ENABLE_WEB_INTERFACE='false', GRADIO_ANALYTICS_ENABLED='false',
                       HF_HUB_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', DO_NOT_TRACK='1',
                       OPENENV_MODE='simulation', NO_PROXY='127.0.0.1,localhost',
                       no_proxy='127.0.0.1,localhost', PYTHONUNBUFFERED='1')
    sources = [str(ROOT / 'envs/wildfire_response/core/src'),
               str(ROOT / 'envs/wildfire_response/openenv')]
    environment['PYTHONPATH'] = os.pathsep.join(sources)
    return environment


@contextmanager
def localhost_server(app_spec, environment, *, factory=False):
    process = None
    with socket.socket() as listener, tempfile.TemporaryFile(mode='w+t') as logs:
        try:
            listener.bind(('127.0.0.1', 0))
            listener.listen(16)
        except OSError as exception:
            raise UnsupportedRuntime(f'Cannot bind a localhost server: {exception}') from exception
        url = f'http://127.0.0.1:{listener.getsockname()[1]}'
        command = [sys.executable, '-m', 'uvicorn', app_spec, '--fd', str(listener.fileno()),
                   '--log-level', 'warning', '--no-access-log']
        if factory:
            command.append('--factory')
        try:
            process = subprocess.Popen(command, cwd=ROOT, env=environment,
                                       pass_fds=(listener.fileno(),), stdout=logs,
                                       stderr=subprocess.STDOUT)
            listener.close()
            deadline = time.monotonic() + 30
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                try:
                    with opener.open(url + '/health', timeout=0.5) as response:
                        if response.status == 200:
                            yield url
                            return
                except (urllib.error.URLError, TimeoutError, ConnectionError):
                    time.sleep(0.1)
            logs.seek(0)
            raise RuntimeError(f'{app_spec} failed readiness: {logs.read()[-8000:]}')
        finally:
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def legacy_worker():
    suite = unittest.defaultTestLoader.loadTestsFromNames(LEGACY_MODULES)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = dict(tests_run=result.testsRun, skipped=len(result.skipped),
                  failures=len(result.failures), errors=len(result.errors),
                  external_server_checks=3)
    passed = result.wasSuccessful() and result.testsRun == 9 and not result.skipped
    report['status'] = 'pass' if passed else 'fail'
    print(json.dumps(report))
    return 0 if passed else 1


def run_legacy(environment):
    with localhost_server('envs.wildfire_response.openenv.server:app', environment) as wildfire_url:
        with localhost_server('wildfire_openenv.server:app', environment) as regional_url:
            environment = dict(environment, WILDFIRE_TEST_URL=wildfire_url,
                               REGIONAL_TEST_URL=regional_url)
            command = [sys.executable, str(Path(__file__).resolve()), '--_legacy-worker']
            completed = subprocess.run(command, cwd=ROOT, env=environment, text=True,
                                       capture_output=True, timeout=180)
            sys.stderr.write(completed.stderr)
            try:
                report = json.loads(completed.stdout)
            except json.JSONDecodeError as exception:
                raise RuntimeError(f'Invalid legacy test output: {completed.stdout[-4000:]}') from exception
            if completed.returncode or report.get('status') != 'pass':
                raise RuntimeError(f'Legacy checks did not all execute successfully: {report}')
            return report


def require_equal(actual, expected, label):
    if actual != expected:
        raise RuntimeError(f'Local/server mismatch: {label}')


def compare_exported_trajectories(local_trace, server_trace, directory, identifier):
    directory.mkdir(parents=True, exist_ok=True)
    records = {}
    for label, trace in (('local', local_trace), ('server', server_trace)):
        path = directory / f'{identifier}-{label}.json'
        encoded = json.dumps(trace, sort_keys=True, separators=(',', ':'), allow_nan=False)
        path.write_text(encoded + '\n', encoding='utf-8')
        decoded = json.loads(path.read_text(encoding='utf-8'))
        require_equal(decoded, trace, identifier + ' ' + label + ' exported JSON roundtrip')
        records[label] = dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    require_equal(records['local']['sha256'], records['server']['sha256'],
                  identifier + ' exported trajectory bytes')
    return records


def prepared_worker(inputs, expected_sha256, url, trajectory_directory):
    from wildfire_core.prepared import (PreparedDataEnv, ProtectionBaseline, load_inputs,
                                        task_manifest, verify_prepared_trajectory)
    from wildfire_openenv.prepared import PreparedAction, PreparedClient

    value = load_inputs(inputs, expected_sha256=expected_sha256)
    cases = task_manifest(value)['cases']
    results = []
    implementation_sha256 = None
    with PreparedClient(base_url=url, mode='simulation', message_timeout_s=30).sync() as client:
        for case in cases:
            identifier, seed = case['id'], case['seed']
            local = PreparedDataEnv(inputs, expected_sha256=expected_sha256,
                                    allow_synthetic_conditions=True)
            observation, info = local.reset(identifier, seed)
            remote = client.reset(task_id=identifier, seed=seed)
            require_equal(remote.observation.data, observation, identifier + ' reset observation')
            require_equal(remote.observation.metadata, info, identifier + ' reset metadata')
            policy = ProtectionBaseline(prefer_shelter=False)
            total_reward = 0.0
            for decision in range(60):
                action = policy(observation)
                check = local.check_orders(action)
                checked = client.step(PreparedAction(operation='check', **action))
                require_equal(checked.observation.metadata, {'action_check': check},
                              identifier + ' public action check')
                require_equal(checked.observation.data, local.get_situation(),
                              identifier + ' non-advancing observation')
                if not check['valid']:
                    raise RuntimeError(f'{identifier}: scripted action was invalid: {check}')
                observation, reward, terminated, truncated, info = local.step(action)
                remote = client.step(PreparedAction(**action))
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
            trace = client.step(PreparedAction(operation='trajectory')).observation.trajectory
            require_equal(trace, local.trajectory(), identifier + ' complete public trajectory')
            if implementation_sha256 is None:
                implementation_sha256 = trace['implementation_sha256']
            require_equal(trace['implementation_sha256'], implementation_sha256,
                          identifier + ' unchanged implementation')
            exports = compare_exported_trajectories(local.trajectory(), trace,
                                                    trajectory_directory, identifier)
            require_equal(total_reward, local.reward, identifier + ' total reward')
            verified = local.verify_episode()
            replay = verify_prepared_trajectory(trace, value)
            if not verified['passed']:
                raise RuntimeError(f'{identifier}: local verifier failed')
            require_equal(replay['total_score'], total_reward, identifier + ' server trace replay')
            results.append(dict(task_id=identifier, seed=seed, decisions=decision + 1,
                                total_reward=total_reward, full_trajectory_equal=True,
                                exact_replay=True, exported_json_roundtrip=True, exports=exports))
            print(f'{identifier}: full local/server trajectory and reward replay match', file=sys.stderr)
    report = dict(status='pass', inputs_sha256=expected_sha256, cases=results,
                  implementation_sha256=implementation_sha256,
                  split='development', allow_synthetic_conditions=True,
                  scope='Prepared-data synthetic exercises; no inference or historical replay')
    print(json.dumps(report))
    return 0


def run_prepared(environment, inputs, expected_sha256, trajectory_directory):
    inputs = inputs.resolve()
    environment = dict(environment, WILDFIRE_PREPARED_INPUTS=str(inputs),
                       WILDFIRE_PREPARED_SHA256=expected_sha256)
    with localhost_server('wildfire_openenv.prepared:create_app', environment, factory=True) as url:
        command = [sys.executable, str(Path(__file__).resolve()), '--_prepared-worker',
                   '--prepared-inputs', str(inputs), '--prepared-sha256', expected_sha256,
                   '--_url', url, '--trajectory-dir', str(trajectory_directory)]
        completed = subprocess.run(command, cwd=ROOT, env=environment, text=True,
                                   capture_output=True, timeout=240)
        sys.stderr.write(completed.stderr)
        if completed.returncode:
            raise RuntimeError(f'Prepared worker failed (exit {completed.returncode}): '
                               + completed.stdout[-4000:])
        report = json.loads(completed.stdout)
        if report.get('status') != 'pass' or len(report.get('cases', [])) != 6:
            raise RuntimeError('All six prepared development cases must execute')
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--prepared-inputs', type=Path)
    parser.add_argument('--prepared-sha256')
    parser.add_argument('--report', type=Path)
    parser.add_argument('--trajectory-dir', type=Path,
                        default=ROOT / '.runtime/openenv-integration-trajectories')
    parser.add_argument('--_legacy-worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--_prepared-worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--_url', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args._legacy_worker:
        return legacy_worker()
    if (args.prepared_inputs is None) != (args.prepared_sha256 is None):
        parser.error('--prepared-inputs and --prepared-sha256 must be supplied together')
    if args._prepared_worker:
        return prepared_worker(args.prepared_inputs, args.prepared_sha256, args._url,
                               args.trajectory_dir.resolve())
    report = dict(status='fail', python=sys.version.split()[0],
                  prepared=dict(status='not_requested'))
    try:
        report['versions'] = {name: importlib.metadata.version(name)
                              for name in ('openenv', 'pydantic', 'fastapi', 'uvicorn',
                                           'websockets', 'httpx')}
        if sys.version_info < (3, 12) or report['versions']['openenv'] != '0.7.0':
            raise UnsupportedRuntime('Use Python >=3.12 and the pinned openenv==0.7.0')
        environment = local_environment()
        report['legacy'] = run_legacy(environment)
        if args.prepared_inputs is not None:
            report['prepared'] = run_prepared(environment, args.prepared_inputs, args.prepared_sha256,
                                              args.trajectory_dir.resolve())
        report['status'] = 'pass'
    except (ImportError, importlib.metadata.PackageNotFoundError, UnsupportedRuntime,
            PermissionError) as exception:
        report.update(status='unsupported_runtime', error=str(exception))
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exception:
        report.update(status='fail', error=str(exception))
    encoded = json.dumps(report, indent=2, sort_keys=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(encoded + '\n')
    print(encoded)
    return {'pass': 0, 'fail': 1, 'unsupported_runtime': 2}[report['status']]


if __name__ == '__main__':
    raise SystemExit(main())
