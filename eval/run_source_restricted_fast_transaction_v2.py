"""Source-verified, transactional private fast-digest experiment launcher."""
import argparse
import ast
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
VERSION = 'wildfire-restricted-fast-transaction-v2'
NAMES = ('launcher', 'bootstrap', 'transaction', 'digest-launcher', 'prototype',
         'driver', 'runner', 'adapter', 'containment', 'host', 'scorer')


@contextmanager
def _bindings(args, pins):
    from eval import run_source_restricted_fast_digest_v1 as helper
    from eval import incident_core_digest_fast_v1 as prototype
    from eval import run_source_restricted_private_v1 as base
    from eval import qualify_source_restricted_day_v1 as runner
    from envs.wildfire_response.incident_day import environment
    from wildfire_containment_v1 import incident_day as containment
    import adapter_fire_v1 as adapter
    import wildfire_verified_source as loader
    helper._require_child()
    original = environment.implementation_digest
    fast = prototype.implementation_digest
    old_save = runner._save
    helper._function(original, environment.__name__, 'implementation_digest', Path(environment.__file__))
    helper._function(fast, prototype.__name__, 'implementation_digest', ROOT/'eval/incident_core_digest_fast_v1.py')
    helper._function(runner.qualify, runner.__name__, 'qualify', ROOT/'eval/qualify_source_restricted_day_v1.py')
    helper._function(old_save, runner.__name__, '_save', ROOT/'eval/qualify_source_restricted_day_v1.py')
    for module, path in ((runner,'eval/qualify_source_restricted_day_v1.py'),
                         (base,'eval/run_source_restricted_private_v1.py'),
                         (prototype,'eval/incident_core_digest_fast_v1.py'),
                         (adapter,'envs/wildfire_extended_inputs_v1/adapter_fire_v1.py'),
                         (containment,'envs/wildfire_containment_v1/src/wildfire_containment_v1/incident_day.py'),
                         (environment,'envs/wildfire_response/core/src/envs/wildfire_response/incident_day/environment.py')):
        helper._source_path(module,path)
    helper._function(adapter._source,adapter.__name__,'_source',Path(adapter.__file__))
    helper._function(containment._check_runtime,containment.__name__,'_check_runtime',Path(containment.__file__))
    helper._function(base.run,base.__name__,'run',Path(base.__file__))
    if (base.VERSION != 'wildfire-restricted-private-source-driver-v1'
            or adapter.implementation_digest is not original or containment.implementation_digest is not original
            or original() != containment.CORE or fast() != containment.CORE):
        raise RuntimeError('runtime binding or digest parity differs')
    functions = (original, fast, prototype._relative_paths, old_save, runner.qualify, base.run,
                 adapter._source, containment._check_runtime, containment._source_digest)
    codes = tuple(f.__code__ for f in functions)
    expected_files = _files(args.mode)
    version = VERSION + ':' + args.mode
    from eval import restricted_fast_transaction_v2 as transaction
    helper._source_path(transaction,'eval/restricted_fast_transaction_v2.py')
    hourly = None
    if args.mode == 'hourly':
        from eval import run_source_restricted_passive_v2 as hourly
        helper._source_path(hourly,'eval/run_source_restricted_passive_v2.py')
        helper._function(hourly.run,hourly.__name__,'run',Path(hourly.__file__))
        helper._function(hourly.PassiveWait60.act,hourly.__name__,'PassiveWait60.act',Path(hourly.__file__))
        if (hourly.base is not base or hourly.RUNNER.resolve() != Path(runner.__file__).resolve()
                or hourly.VERSION != 'wildfire-restricted-private-passive-driver-v2'
                or hourly.POLICY_VERSION != 'passive-wait-60-minutes-v2'):
            raise RuntimeError('hourly policy identity differs')
        hourly_functions=(hourly.run,hourly.PassiveWait60,hourly.PassiveWait60.act,hourly.PassiveWait60.__init__)
        hourly_codes=(hourly_functions[0].__code__,hourly_functions[2].__code__,hourly_functions[3].__code__)

    def verify():
        loader.verify()
        if (any(_sha(path) != pins[name] for name, path in expected_files.items())
                or environment.implementation_digest is not original
                or prototype.implementation_digest is not fast or prototype._relative_paths is not functions[2]
                or runner._save is not save or runner.qualify is not functions[4] or base.run is not functions[5]
                or adapter._source is not functions[6] or containment._check_runtime is not functions[7]
                or containment._source_digest is not functions[8]
                or adapter.implementation_digest is not checked or containment.implementation_digest is not checked
                or tuple(f.__code__ for f in functions) != codes):
            raise RuntimeError('runtime source or function binding changed')
        if hourly is not None and (hourly.run is not hourly_functions[0] or hourly.PassiveWait60 is not hourly_functions[1]
                or hourly.PassiveWait60.act is not hourly_functions[2] or hourly.PassiveWait60.__init__ is not hourly_functions[3]
                or (hourly.run.__code__,hourly.PassiveWait60.act.__code__,hourly.PassiveWait60.__init__.__code__) != hourly_codes
                or hourly.base is not base or hourly.RUNNER.resolve() != Path(runner.__file__).resolve()
                or hourly.POLICY_VERSION != 'passive-wait-60-minutes-v2'):
            raise RuntimeError('hourly runtime binding changed')

    def checked():
        verify()
        return fast()

    def guard():
        if checked() != containment.CORE:
            raise RuntimeError('core source changed')

    def save(path, report, continuations):
        transaction.saved(path, report, continuations, original=old_save, guard=guard, pins=pins, version=version)

    try:
        adapter.implementation_digest = checked
        containment.implementation_digest = checked
        runner._save = save
        guard()
        yield base, runner, transaction, guard, version
    finally:
        adapter.implementation_digest = original
        containment.implementation_digest = original
        runner._save = old_save


def _sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _preverified_manifest(files, pins):
    """Pin import bytes and check the frozen core before any repository import."""
    manifest = {}
    for folder in (ROOT/'envs', ROOT/'eval'):
        for directory, directories, names in os.walk(folder, followlinks=False):
            directories[:] = [name for name in directories if name != 'tests']
            for name in names:
                if name.endswith('.py'):
                    path = Path(directory)/name
                    manifest[str(path.absolute())] = _sha(path)
    for name, path in files.items():
        if manifest.get(str(path.absolute())) != pins[name]:
            raise RuntimeError('source changed while constructing manifest')
    containment_source = files['containment'].read_bytes()
    if hashlib.sha256(containment_source).hexdigest() != pins['containment']:
        raise RuntimeError('containment source changed while constructing manifest')
    tree = ast.parse(containment_source, filename=str(files['containment']))
    constants = [ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                 and any(isinstance(target,ast.Name) and target.id=='CORE' for target in node.targets)]
    core = ROOT/'envs/wildfire_response/core/src'
    folders=(core/'envs/wildfire_response',core/'wildfire_core',core/'simulator')
    paths=[path for folder in folders for path in folder.rglob('*.py') if 'tests' not in path.parts]
    paths.append(core/'envs/wildfire_response/data/huelva_regional_v2.json')
    rows={str(path.relative_to(core)):_sha(path) for path in sorted(paths)}
    digest=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',', ':'),allow_nan=False).encode()).hexdigest()
    if len(constants)!=1 or constants[0]!=digest:
        raise RuntimeError('frozen core source differs')
    for path in paths:
        if path.suffix=='.py' and manifest.get(str(path.absolute())) != rows[str(path.relative_to(core))]:
            raise RuntimeError('core changed while constructing manifest')
    return manifest


def _files(mode):
    result = dict(launcher=ROOT/'eval/run_source_restricted_fast_transaction_v2.py',
        bootstrap=ROOT/'eval/restricted_fast_source_bootstrap_v1.py',
        transaction=ROOT/'eval/restricted_fast_transaction_v2.py',
        **{'digest-launcher':ROOT/'eval/run_source_restricted_fast_digest_v1.py'},
        prototype=ROOT/'eval/incident_core_digest_fast_v1.py',
        driver=ROOT/'eval/run_source_restricted_private_v1.py',
        runner=ROOT/'eval/qualify_source_restricted_day_v1.py',
        adapter=ROOT/'envs/wildfire_extended_inputs_v1/adapter_fire_v1.py',
        containment=ROOT/'envs/wildfire_containment_v1/src/wildfire_containment_v1/incident_day.py',
        host=ROOT/'envs/wildfire_source_restricted_v1/host.py',
        scorer=ROOT/'envs/wildfire_source_restricted_scored_v1/__init__.py')
    if mode == 'hourly':result['hourly-driver'] = ROOT/'eval/run_source_restricted_passive_v2.py'
    return result


def _worker(args, pins):
    from eval import run_source_restricted_fast_digest_v1 as helper
    import wildfire_verified_source as loader
    helper._require_child()
    os.nice(10)
    if hasattr(os, 'sched_getaffinity') and hasattr(os, 'sched_setaffinity'):
        os.sched_setaffinity(0, {max(os.sched_getaffinity(0))})
    helper._private_dir(args.run_dir, resume=args.resume)
    if any(_sha(path) != pins[name] for name, path in _files(args.mode).items()):
        raise RuntimeError('source pin differs')
    with _bindings(args, pins) as (base, runner, transaction, guard, version):
        if args.mode == 'hourly':
            from eval import run_source_restricted_passive_v2 as selected
            helper._function(selected.run, selected.__name__, 'run', ROOT/'eval/run_source_restricted_passive_v2.py')
            if (selected.base is not base or selected.RUNNER.resolve() != (ROOT/'eval/qualify_source_restricted_day_v1.py').resolve()
                    or selected.POLICY_VERSION != 'passive-wait-60-minutes-v2'):
                raise RuntimeError('hourly policy identity differs')
        else:selected = base
        loader.verify()
        with helper._run_lock(args.run_dir, resume=args.resume):
            fingerprint = hashlib.sha256(json.dumps(pins, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            prior = selected.VERSION
            try:
                selected.VERSION = version + ':' + fingerprint
                if args.resume:
                    transaction.restore(args.run_dir, pins, version)
                else:
                    base.load_key(args.run_dir/'continuation.key',resume=False)
                    if args.mode == 'minute':
                        identities = [selected.VERSION,pins['driver'],pins['host'],pins['scorer'],'unscored']
                    else:
                        identities = [selected.VERSION,selected.POLICY_VERSION,pins['hourly-driver'],
                                      pins['driver'],pins['runner'],pins['host'],pins['scorer']]
                    run_id = hashlib.sha256(json.dumps(identities,separators=(',', ':')).encode()).hexdigest()
                    initial = dict(version=runner.VERSION,run_id=run_id,scope=runner.SCOPE,
                                   cases=[runner._empty(identifier) for identifier in base.PUBLIC_IDS])
                    guard()
                    transaction.initialize(args.run_dir,pins,version,
                        json.dumps(initial,allow_nan=False,separators=(',', ':')).encode())
                run_args = argparse.Namespace(checkpoint=args.run_dir/'progress.json',
                    key_file=args.run_dir/'continuation.key',host_sha256=pins['host'],scorer_sha256=pins['scorer'],
                    wall_seconds=args.wall_seconds,max_decisions=args.max_decisions,resume=True,
                    approval=None,approval_sha256=None)
                report = selected.run(run_args)
                guard()
                transaction.commit(args.run_dir, pins, version)
                counts = {key:sum(row['status']==key for row in report['cases'])
                          for key in ('restricted_scored','censored','blocked','incomplete')}
                print(json.dumps(dict(version=version,run_identity=fingerprint,mode='unscored_experimental',counts=counts)))
            finally:selected.VERSION = prior


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--opt-in-fast-digest', action='store_true')
    parser.add_argument('--mode', choices=('minute','hourly'), required=True)
    parser.add_argument('--run-dir', type=Path, required=True)
    for name in (*NAMES, 'hourly-driver'):parser.add_argument('--'+name+'-sha256')
    parser.add_argument('--wall-seconds', type=float, default=300)
    parser.add_argument('--max-decisions', type=int, default=1)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args(argv)
    if (not args.opt_in_fast_digest or not args.run_dir.is_absolute()
            or not math.isfinite(args.wall_seconds) or not 0<args.wall_seconds<=1800
            or not 1<=args.max_decisions<=1440):parser.error('opt-in and bounded limits required')
    files = _files(args.mode)
    pins = {name:getattr(args,name.replace('-','_')+'_sha256') for name in files}
    if any(type(pin) is not str or len(pin)!=64 or any(c not in '0123456789abcdef' for c in pin)
           for pin in pins.values()):parser.error('all source pins are required')
    if args.mode == 'minute' and args.hourly_driver_sha256 is not None:parser.error('hourly pin is not applicable')
    if any(_sha(path)!=pins[name] for name,path in files.items()):parser.error('source pin differs')
    try:manifest = _preverified_manifest(files,pins)
    except Exception:parser.error('source manifest or frozen core differs')
    pins['source-manifest'] = hashlib.sha256(json.dumps(manifest,sort_keys=True,separators=(',', ':')).encode()).hexdigest()
    bootstrap = files['bootstrap'].read_bytes()
    if hashlib.sha256(bootstrap).hexdigest()!=pins['bootstrap']:parser.error('bootstrap pin differs')
    # Pass the exact verified bootstrap bytes as code; Python -I and the source
    # hook run before any repository module is imported in this child.
    code = ("import json,sys,types\n"
            "payload=json.load(sys.stdin)\n"
            "m=types.ModuleType('wildfire_verified_source')\n"
            "exec(compile("+repr(bootstrap)+","+repr(str(files['bootstrap']))+",'exec'),m.__dict__)\n"
            "sys.modules[m.__name__]=m\n"
            "root="+repr(str(ROOT))+"\n"
            "m.install(root,payload['manifest'])\n"
            "sys.path[:0]="+repr([str(p) for p in (ROOT,ROOT/'envs',ROOT/'envs/wildfire_extended_inputs_v1',ROOT/'envs/wildfire_response/core/src',ROOT/'envs/wildfire_containment_v1/src',ROOT/'envs/wildfire_containment_score_v1/src')])+"\n"
            "from eval.run_source_restricted_fast_transaction_v2 import _child\n"
            "_child(payload)\n")
    env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PROJ_NETWORK='OFF',
        WILDFIRE_FAST_DIGEST_PARENT_V1=str(os.getpid()),WILDFIRE_FAST_DIGEST_TOKEN_V1=secrets.token_hex(32))
    for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','VECLIB_MAXIMUM_THREADS'):env[key]='1'
    payload=json.dumps(dict(args=vars(args)|{'run_dir':str(args.run_dir)},pins=pins,manifest=manifest))
    return subprocess.run([sys.executable,'-I','-c',code],input=payload,text=True,cwd=ROOT,env=env,check=False).returncode


def _child(value):
    try:
        manifest_hash=hashlib.sha256(json.dumps(value['manifest'],sort_keys=True,separators=(',', ':')).encode()).hexdigest()
        if manifest_hash != value['pins'].get('source-manifest'):raise ValueError('source manifest identity differs')
        args=argparse.Namespace(**value['args']);args.run_dir=Path(args.run_dir)
        _worker(args,value['pins'])
    except Exception:
        print('Private transactional worker failed; no unauthenticated progress was accepted.',file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    if __spec__ is not None:
        raise SystemExit('Run this launcher by source path, not with -m.')
    raise SystemExit(main())
