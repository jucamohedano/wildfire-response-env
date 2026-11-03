"""Guarded serial controls: one explicit stress task, then conservative on 24 tasks.

This separate campaign reuses the frozen evaluator's environment binding,
observer, checkpoints, continuations and terminal validator. No frozen source
or old output is modified. Run the same command on an external Linux CPU host
with the pinned package and checkout; no model, OpenEnv or private API is used.
"""
import argparse
import copy
import gzip
import gc
import hashlib
import json
import math
import marshal
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

VERSION = 'wildfire-benchmark-v2-controls-v1'
ROOT = Path(__file__).resolve().parents[1]
GIB = 1024 ** 3
MAX_JSON = 4 * 1024 ** 2
MAX_EXPANDED = 128 * 1024 ** 2
FINISHED = ('complete', 'blocked', 'failed')


def capture_source(name, path, loader):
    try:
        source = Path(path).read_bytes()
        code = loader.get_code(name)
        compiled = compile(source, str(path), 'exec', dont_inherit=True, optimize=sys.flags.optimize)
        return hashlib.sha256(source).hexdigest(), code is not None and marshal.dumps(code) == marshal.dumps(compiled)
    except Exception:
        return None, False


_LOADED_SOURCE_SHA256, _LOADED_CODE_MATCH = capture_source(
    __spec__.name if __spec__ is not None else __name__, __file__, __loader__)


def own_source_identity():
    if not _LOADED_CODE_MATCH or hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != _LOADED_SOURCE_SHA256:
        raise ValueError('Campaign runner loaded code/source differs')
    return _LOADED_SOURCE_SHA256


def capture_policy_sources(runtime):
    captured = {}
    for name in ('policies', 'baselines'):
        module = getattr(runtime, name)
        source, matches = capture_source(module.__name__, module.__file__, module.__loader__)
        if not matches:
            raise ValueError('Policy loaded code/source differs: ' + name)
        captured[name] = source
    runtime.loaded_sources = captured


def check_policy_sources(runtime):
    for name in ('policies', 'baselines'):
        module = getattr(runtime, name)
        if hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest() != runtime.loaded_sources[name]:
            raise ValueError('Policy source changed after loading: ' + name)


def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def memory():
    values = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key, rest = line.split(':', 1)
        values[key] = int(rest.split()[0]) * 1024
    return values


def require_memory():
    """Same reserve as wildfire-test-memory-guard.py; there is no bypass flag."""
    values = memory()
    if values['MemAvailable'] < 4 * GIB or values['SwapFree'] < 256 * 1024 ** 2:
        raise MemoryError('Campaign not started/continued: require 4 GiB available RAM and 256 MiB free swap')


def limits():
    for name in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'ARROW_NUM_THREADS'):
        os.environ[name] = '1'
    os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    cap = min(3 * GIB, hard) if hard != resource.RLIM_INFINITY else 3 * GIB
    if soft != resource.RLIM_INFINITY:
        cap = min(cap, soft)
    resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
    available = os.sched_getaffinity(0)
    os.sched_setaffinity(0, {min(available)})


def load_runtime():
    # Deliberately after RAM/swap preflight, address limit and CPU affinity.
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / 'envs/wildfire_response/core/src'))
    sys.path.insert(0, str(ROOT / 'envs/wildfire_ops_v3/src'))
    from eval import evaluate_packaged_runtime as base
    from eval import incident_day_policies as policies
    from envs.wildfire_response.incident_day import baselines
    from wildfire_ops_v3 import observation_identity
    import pyarrow as pa
    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)
    runtime = SimpleNamespace(base=base, policies=policies, baselines=baselines,
                              observation_identity=observation_identity)
    capture_policy_sources(runtime)
    return runtime


def document(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Symlink protocol/evidence is not accepted')
    with path.open('rb') as stream:
        raw = stream.read(MAX_JSON + 1)
    if len(raw) > MAX_JSON:
        raise ValueError('JSON evidence exceeds limit')
    return json.loads(raw)


def policy_receipts(protocol, runtime):
    check_policy_sources(runtime)
    registration = copy.deepcopy(protocol['weak_active_registration'])
    if (registration['name'] != 'conservative'
            or registration['factory'] != 'eval.incident_day_policies:make_policy'
            or registration['configuration'] != {'name': 'conservative'}):
        raise ValueError('Expected the preregistered conservative control')
    if (registration['source_sha256'] != runtime.loaded_sources['policies']
            or registration['dependency_sha256'] != {'baselines.py': runtime.loaded_sources['baselines']}
            or registration['implementation_sha256'] != runtime.base.implementation_digest()
            or registration['observation_sha256'] != runtime.observation_identity()
            or registration['interface_version'] != 'wildfire-incident-day-ops-v3'):
        raise ValueError('Registered policy source/core/observation identity differs')
    if 'stress' not in protocol.get('reference_candidates', []):
        raise ValueError('Protocol must preregister stress as a reference candidate')
    stress = copy.deepcopy(registration)
    stress.update(name='stress', configuration={'name': 'stress'})
    return {'conservative': registration, 'stress': stress}


def prepare(data_dir, protocol_path, expected_protocol_sha256, expected_package_sha256, reference_task):
    own_source_identity()
    protocol = document(protocol_path)
    if protocol.get('version') != 'wildfire-benchmark-protocol-v2' or checksum(protocol) != expected_protocol_sha256:
        raise ValueError('Frozen protocol identity differs')
    runtime = load_runtime()
    base = runtime.base
    manifest = base.verify_package(data_dir)
    if checksum(manifest) != expected_package_sha256:
        raise ValueError('Frozen package identity differs')
    receipts = policy_receipts(protocol, runtime)
    # Use the original domain validator, without changing its two-policy matrix.
    original = base.expected_cells(manifest, 'es-ma-2021')
    tasks = sorted({c['case_id'] for c in original})
    if len(tasks) != 24 or type(reference_task) is not str or reference_task not in tasks:
        raise ValueError('Campaign requires the complete pinned 24-task evaluation catalog')
    expected = [dict(runtime_id='es-ma-2021', case_id=reference_task, policy='stress')]
    expected += [dict(runtime_id='es-ma-2021', case_id=task, policy='conservative') for task in tasks]
    rows = base.evaluation_rows(data_dir, manifest)
    pins = dict(implementation_sha256=base.implementation_digest(), observation_version='v3',
        observation_sha256=runtime.observation_identity(), lifecycle_sha256=base.registry()['sha256'],
        package_sha256=checksum(manifest), protocol_sha256=checksum(protocol),
        validator_sha256=base.validator_digest(), campaign_runner_sha256=own_source_identity(),
        policy_registrations_sha256=checksum(receipts), campaign_domain_sha256=checksum(expected))
    return SimpleNamespace(runtime=runtime, base=base, manifest=manifest, protocol=protocol,
                           expected=expected, rows=rows, receipts=receipts, pins=pins)


def refresh(report, base):
    expected, cells = report['expected_cells'], report['cells']
    if (len(expected) != 25 or [base._identity(c) for c in cells] != [base._identity(c) for c in expected]
            or len(set(map(base._identity, expected))) != 25):
        raise ValueError('Campaign domain differs')
    statuses = {c['status'] for c in cells}
    if not statuses <= {'not_run', 'incomplete', *FINISHED}:
        raise ValueError('Unknown campaign status')
    for cell in cells:
        if cell['status'] != 'complete':
            continue
        if (cell.get('minute') != 1440 or not cell.get('resume_checks')
                or any(c.get('passed') is not True for c in cell['resume_checks'])
                or cell.get('query_independence', {}).get('passed') is not True
                or any(cell.get('verification', {}).get(k) is not True for k in
                       ('passed', 'exact_replay', 'independent_reward_agreement'))
                or any(f['category'] in ('blocking', 'contradiction', 'error') for f in cell.get('findings', []))):
            statuses.add('failed')
        elif (base.uninspectable_findings(cell.get('findings', []))
              or base.uninspectable_coverage(cell.get('operation_coverage', {}))):
            statuses.add('blocked')
    report['status'] = ('failed' if 'failed' in statuses else 'blocked' if 'blocked' in statuses else
                        'incomplete' if statuses & {'incomplete', 'not_run'} else 'complete')
    report['summary'] = dict(expected=25, finished=sum(c['status'] in FINISHED for c in cells),
        statuses={s: sum(c['status'] == s for c in cells) for s in sorted(statuses)})


def load_gzip(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_EXPANDED:
        raise ValueError('Invalid campaign evidence path/size')
    with gzip.open(path, 'rb') as stream:
        raw = stream.read(MAX_EXPANDED + 1)
    if len(raw) > MAX_EXPANDED:
        raise ValueError('Expanded checkpoint exceeds limit')
    value = json.loads(raw)
    if value.get('sha256') != checksum({k: v for k, v in value.items() if k != 'sha256'}):
        raise ValueError('Campaign evidence integrity differs')
    return value


def publish_cell(path, value, base):
    """Atomically publish a completed immutable cell without replacing evidence."""
    fd, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.pending', dir=path.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        base.write_gzip(temporary, value)
        os.link(temporary, path, follow_symlinks=False)
    finally:
        temporary.unlink(missing_ok=True)


def run(data_dir, protocol_path, output, *, expected_protocol_sha256, expected_package_sha256,
        reference_task, wall_seconds=1800, max_commands=None, max_cells=None, resume=False):
    if type(wall_seconds) not in (int, float) or not math.isfinite(wall_seconds) or wall_seconds <= 0:
        raise ValueError('Positive finite wall budget required')
    for value in (max_commands, max_cells):
        if value is not None and (type(value) is not int or value < 1):
            raise ValueError('Positive integer command/cell budgets required')
    require_memory()
    limits()
    ctx = prepare(data_dir, protocol_path, expected_protocol_sha256, expected_package_sha256, reference_task)
    base, expected, pins = ctx.base, ctx.expected, ctx.pins
    output = Path(output).absolute()
    for protected in (Path(data_dir).resolve(), Path(protocol_path).resolve().parent):
        if output.resolve() == protected or protected in output.resolve().parents or output.resolve() in protected.parents:
            raise ValueError('Output must be separate from package and protocol directories')
    if output.is_symlink():
        raise ValueError('Output must not be a symlink')
    def freeze():
        if (base.implementation_digest() != pins['implementation_sha256']
                or ctx.runtime.observation_identity() != pins['observation_sha256']
                or base.registry()['sha256'] != pins['lifecycle_sha256']
                or base.validator_digest() != pins['validator_sha256']
                or checksum(base.verify_package(data_dir)) != pins['package_sha256']
                or checksum(document(protocol_path)) != pins['protocol_sha256']
                or checksum(policy_receipts(ctx.protocol, ctx.runtime)) != pins['policy_registrations_sha256']
                or own_source_identity() != pins['campaign_runner_sha256']):
            raise ValueError('Campaign source/data/protocol freeze changed')
    if resume:
        frozen = document(output / 'freeze.json')
        if frozen.get('version') != VERSION or frozen['pins'] != pins or frozen['expected_cells'] != expected:
            raise ValueError('Campaign resume identity differs')
        saved = load_gzip(output / 'private/checkpoint.json.gz')
        report, index, live = saved['report'], saved['next_index'], saved['live']
        if (saved.get('version') != VERSION or saved['pins'] != pins or report['pins'] != pins
                or report.get('version') != VERSION or report.get('split') != 'evaluation'
                or report.get('training_eligible') is not False
                or report['expected_cells'] != expected or type(index) is not int or not 0 <= index <= len(expected)):
            raise ValueError('Campaign checkpoint identity differs')
        if any(c['status'] not in FINISHED for c in report['cells'][:index]):
            raise ValueError('Resume would skip unfinished cells')
        if any(c['status'] != 'not_run' for c in report['cells'][index + (live is not None):]):
            raise ValueError('Resume would overwrite evaluated cells')
        if live is not None and (index >= len(expected) or live['identity'] != expected[index]
                or live['policy']['policy'] != expected[index]['policy']
                or base.checkpoint_state(live['environment'])['_scenario']['id'] != expected[index]['case_id']
                or report['cells'][index]['status'] != 'incomplete'):
            raise ValueError('Live checkpoint case/policy differs')
    else:
        output.mkdir(mode=0o700, parents=True, exist_ok=False)
        (output / 'private').mkdir(mode=0o700)
        frozen = dict(version=VERSION, pins=pins, expected_cells=expected, protocol=ctx.protocol,
                      policy_registrations=ctx.receipts, workers=1)
        fd = os.open(output / 'freeze.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(frozen, stream, sort_keys=True, allow_nan=False)
        report = dict(version=VERSION, split='evaluation', training_eligible=False, pins=pins,
            expected_cells=expected, cells=[dict(c, status='not_run') for c in expected], workers=1,
            policies=['stress', 'conservative'], policy_registrations=ctx.receipts,
            validation_scope='Frozen per-cell validator; this is not the original four-policy all-operation matrix',
            elapsed_seconds=0, peak_rss_bytes=base.peak_memory())
        index, live = 0, None
    refresh(report, base)
    start, prior, commands, finished = time.perf_counter(), report['elapsed_seconds'], 0, 0
    interrupted = False
    def stop(signum, frame):
        nonlocal interrupted
        interrupted = True
    handlers = {s: signal.signal(s, stop) for s in (signal.SIGINT, signal.SIGTERM)}
    def persist(state):
        freeze()
        refresh(report, base)
        report['elapsed_seconds'] = prior + time.perf_counter() - start
        report['peak_rss_bytes'] = base.peak_memory()
        payload = dict(version=VERSION, pins=pins, report=report, next_index=index, live=state)
        payload['sha256'] = checksum(payload)
        base.write_gzip(output / 'private/checkpoint.json.gz', payload)
        base.write_json(output / 'report.json', report)
    def budget():
        require_memory()
        return interrupted or time.perf_counter() - start >= wall_seconds or (max_commands is not None and commands >= max_commands)
    def cell_path(identity):
        return output / 'private' / ('-'.join(base._identity(identity)) + '.json.gz')
    try:
        # A completed immutable cell may precede the last checkpoint after a
        # crash. Recover it rather than overwrite its evidence on replay.
        while index < len(expected) and cell_path(expected[index]).exists():
            completed = load_gzip(cell_path(expected[index]))
            result = completed['campaign_result']
            if (completed['campaign_pins'] != pins or base._identity(result) != base._identity(expected[index])
                    or result.get('status') not in FINISHED):
                raise ValueError('Recovered cell identity differs')
            report['cells'][index] = result
            index += 1; live = None
        persist(live)
        while index < len(expected):
            if budget() or (max_cells is not None and finished >= max_cells):
                persist(live); return report
            freeze()
            identity = expected[index]
            env, entry = base.make_environment(data_dir, identity['runtime_id'], allow_unapproved_data=True, observation_version='v3')
            if entry != ctx.manifest['runtimes'][identity['runtime_id']]:
                raise ValueError('Runtime changed')
            policy = base.make_policy(identity['policy'])
            cell_start = time.perf_counter()
            carried = live.get('cell_elapsed_seconds', 0) if live else 0
            if live is None:
                observation, _ = env.reset(identity['case_id'])
                base.bind_scenario(env, identity, ctx.rows[(identity['runtime_id'], identity['case_id'])])
                calls, checked, checks = [], set(), []
                query = base.versioned_query_independence(env)
                observer = base.MovementObserver(env)
                public = base.PublicFacade(env, calls)
                public.get_situation(); public.get_reports(limit=20)
            else:
                observation = env.restore(live['environment'])
                base.bind_scenario(env, identity, ctx.rows[(identity['runtime_id'], identity['case_id'])])
                policy.restore(live['policy'])
                observer = base.MovementObserver(env); observer.restore(live['movement_observer'])
                calls, checked, checks, query = live['calls'], set(live['checked']), live['resume_checks'], live['query_independence']
            public = base.PublicFacade(env, calls)
            def snapshot():
                return base._live(identity, env, policy, observer, calls, checked, checks, query,
                                  carried + time.perf_counter() - cell_start)
            report['cells'][index] = dict(identity, status='incomplete', minute=env.minute)
            live = snapshot(); persist(live)
            hour = env.minute // 60
            while not (env.terminated or env.truncated):
                if budget():
                    report['cells'][index].update(minute=env.minute, reason='signal_or_budget')
                    live = snapshot(); persist(live); return report
                phases = {unit['state'] for unit in env.ops.resources.values()}
                labels = sorted((phases & {'travel', 'on_scene', 'working', 'returning', 'refilling', 'resting'}) - checked)
                label = labels[0] if labels else 'initial' if not checked else None
                continuation = base.versioned_continuation(env, policy) if label else None
                action = policy.action(public, observation)
                result = base.commit(public, action)
                if 'observation' not in result:
                    raise ValueError('Control policy call was rejected')
                observation = result['observation']; commands += 1
                if continuation:
                    passed = (action == continuation['action'] and checksum(result) == continuation['result']
                        and checksum(env._audit) == continuation['audit'] and checksum(env.fire.checkpoint()) == continuation['fire']
                        and checksum(policy.checkpoint()) == continuation['policy'])
                    checks.append(dict(boundary=label, minute=env.minute, passed=passed)); checked.add(label)
                    if not passed:
                        raise ValueError('Control continuation differs')
                if env.minute // 60 > hour:
                    hour = env.minute // 60
                    report['cells'][index]['minute'] = env.minute
                    live = snapshot(); persist(live)
            require_memory(); freeze()
            result, private = base.finish_cell(identity, env, observer, calls, checks, query)
            receipt = copy.deepcopy(ctx.receipts[identity['policy']])
            result.update(policy_registration=receipt, elapsed_seconds=carried + time.perf_counter() - cell_start)
            result['pins'].update(implementation_sha256=pins['implementation_sha256'],
                observation_sha256=pins['observation_sha256'], policy_registration_sha256=checksum(receipt))
            private.update(policy_registration=copy.deepcopy(receipt), campaign_result=result, campaign_pins=pins)
            private['sha256'] = checksum(private)
            freeze()
            destination = cell_path(identity)
            if destination.exists():
                raise ValueError('Completed cell evidence already exists')
            publish_cell(destination, private, base)
            report['cells'][index] = result
            index += 1; finished += 1; live = None
            persist(None)
            print(json.dumps(dict(identity, status=result['status'], minute=env.minute)), flush=True)
            del env, observer, public, private, policy
            gc.collect()
        return report
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


def group_rss(group):
    total = 0
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            text = (path / 'stat').read_text(); fields = text[text.rfind(')') + 2:].split()
            if int(fields[2]) == group:
                total += int(fields[21]) * os.sysconf('SC_PAGE_SIZE')
        except (OSError, ValueError, IndexError):
            pass
    return total


def supervise(argv):
    """Watch only this runner's child group, using the unchanged reserve gates."""
    require_memory()
    env = dict(os.environ, OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1',
               ARROW_NUM_THREADS='1', PYTHONDONTWRITEBYTECODE='1')
    child = subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()), *argv, '--_worker'],
                             env=env, start_new_session=True)
    stopped = False
    def stop(signum, frame):
        nonlocal stopped
        stopped = True
    handlers = {s: signal.signal(s, stop) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        while child.poll() is None:
            try:
                require_memory()
                if stopped or group_rss(child.pid) > int(1.5 * GIB):
                    raise MemoryError('Owned campaign group stopped for signal/RSS reserve')
            except MemoryError as error:
                print(str(error), file=sys.stderr, flush=True)
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL); child.wait()
                return 2
            time.sleep(.25)
        return child.returncode
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--expected-protocol-sha256', required=True)
    parser.add_argument('--expected-package-sha256', required=True)
    parser.add_argument('--reference-task', required=True, help='Explicit evaluator-selected stress task ID')
    parser.add_argument('--wall-seconds', type=float, default=1800)
    parser.add_argument('--max-commands', type=int); parser.add_argument('--max-cells', type=int)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--_worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if not args._worker:
            return supervise(argv)
        result = run(args.data_dir, args.protocol, args.output,
            expected_protocol_sha256=args.expected_protocol_sha256, expected_package_sha256=args.expected_package_sha256,
            reference_task=args.reference_task, wall_seconds=args.wall_seconds,
            max_commands=args.max_commands, max_cells=args.max_cells, resume=args.resume)
        print(json.dumps(dict(status=result['status'], summary=result['summary'], pins=result['pins'])))
        return 0 if result['status'] == 'complete' else 2
    except (MemoryError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
