#!/usr/bin/env python3
"""Private, unscored, single-CPU 60-minute passive source diagnostic.

This opt-in policy does not react to reports during a wait. Each completed wait
is checkpointed; interruption mid-wait rolls back to the preceding checkpoint.
No restricted or full-environment score can be produced by this driver.
"""
import os
for _name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
              'NUMEXPR_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
    os.environ[_name] = '1'
os.environ['PROJ_NETWORK'] = 'OFF'
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'

import argparse
import hashlib
import hmac
import json
import math
from pathlib import Path
import stat
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'envs'), str(ROOT / 'envs/wildfire_extended_inputs_v1'),
                str(ROOT / 'envs/wildfire_response/core/src'), str(ROOT / 'envs/wildfire_containment_v1/src'),
                str(ROOT / 'envs/wildfire_containment_score_v1/src')]
from eval import run_source_restricted_private_v1 as base

VERSION = 'wildfire-restricted-private-passive-driver-v2'
POLICY_VERSION = 'passive-wait-60-minutes-v2'
RUNNER = ROOT / 'eval/qualify_source_restricted_day_v1.py'


class PassiveWait60:
    """Advance up to 60 minutes without reading or reacting to intervening reports."""
    def __init__(self, guard=lambda: None, on_error=lambda _: None):
        self._guard = guard
        self._on_error = on_error

    def act(self, host, observation):
        try:
            self._guard()
            host.advance(60)
            self._guard()
        except Exception as exc:
            self._on_error(exc)
            raise


def run(args):
    from eval.qualify_source_restricted_day_v1 import qualify, BlockedCase, SCOPE, REWARD_VERSION
    from wildfire_source_restricted_v1 import RestrictedSourceIncidentV1

    sources = {path: base.digest_file(path) for path in (Path(__file__), Path(base.__file__), RUNNER)}
    sources[base.HOST] = args.host_sha256
    sources[base.SCORER] = args.scorer_sha256
    def guard():
        if any(base.digest_file(path) != expected for path, expected in sources.items()):
            raise RuntimeError('pinned implementation changed')
    guard()
    if args.key_file == args.checkpoint:
        raise ValueError('continuation key and checkpoint must differ')
    error_path = args.checkpoint.with_name(args.checkpoint.name + '.errors.jsonl')
    if not args.resume and os.path.lexists(error_path):
        raise FileExistsError('error journal already exists')
    def record_error(stage, case, exc):
        fd = os.open(error_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_mode & 0o077 or info.st_nlink != 1):
                raise ValueError('invalid private error journal')
            entry = dict(stage=stage, case=case, error_type=type(exc).__name__, detail=str(exc))
            os.write(fd, (json.dumps(entry, ensure_ascii=True) + '\n').encode())
            os.fsync(fd)
        finally:
            os.close(fd)
    cache = {'loaded': False, 'error': False}
    def setup():
        if not cache['loaded']:
            cache['loaded'] = True
            try:
                guard()
                cache['inputs'], cache['selector'], cache['parent'] = base.prepare_inputs()
                ids = cache['selector'].case_ids()
                if len(ids) != 12 or len(set(ids)) != 12:
                    raise ValueError('source case count differs')
                cache['mapping'] = dict(zip(base.PUBLIC_IDS, ids))
                guard()
            except Exception as exc:
                record_error('setup', None, exc)
                cache['error'] = True
        if cache['error']:
            raise BlockedCase('source setup failed')
    class DiagnosticHost(base.UnscoredHost):
        def __init__(self, host, public_id):
            super().__init__(host)
            self._public_id = public_id
        def advance(self, minutes):
            super().advance(minutes)
            if self._host.situation()['status'] == 'unsupported':
                record_error('unsupported_event', self._public_id, RuntimeError('unsupported scheduled event'))
    def factory(public_id):
        try:
            setup(); guard()
            return DiagnosticHost(RestrictedSourceIncidentV1(
                cache['inputs'], cache['selector'], cache['parent'], case_id=cache['mapping'][public_id],
                containment_config=base.CONFIG, pressure_scale=1, opt_in=True), public_id)
        except BlockedCase:
            raise
        except Exception as exc:
            record_error('factory', public_id, exc)
            raise
    def policy_for(case):
        return PassiveWait60(guard, lambda exc: record_error('policy', case, exc))
    class Continuation:
        def __init__(self, key):
            self._key = key
        def capture(self, host, policy):
            guard()
            if policy is not None and type(policy) is not PassiveWait60:
                raise ValueError('unsupported policy state')
            state = dict(version=VERSION, policy='none' if policy is None else POLICY_VERSION,
                         host=host._host.checkpoint())
            return json.dumps(state, allow_nan=False, separators=(',', ':')).encode()
        def seal(self, body):
            return hmac.digest(self._key, body, 'sha256') + body
        def open(self, sealed):
            if len(sealed) < 32 or not hmac.compare_digest(sealed[:32], hmac.digest(self._key, sealed[32:], 'sha256')):
                raise ValueError('continuation authentication failed')
            return sealed[32:]
        def restore(self, public_id, state):
            import adapter_v1 as api
            value = json.loads(state)
            if (type(value) is not dict or set(value) != {'version', 'policy', 'host'}
                    or value['version'] != VERSION or value['policy'] not in ('none', POLICY_VERSION)):
                raise ValueError('policy continuation differs')
            host = factory(public_id)
            host._host.restore(value['host'], expected_checkpoint_sha256=api._hash(value['host']))
            guard()
            return host, None if value['policy'] == 'none' else policy_for(public_id)
    def verify(_case, _host):
        guard()
        return dict(status='unapproved', scope=SCOPE, reward_version=REWARD_VERSION,
                    verified=False, score_eligible=False, score=None)
    identities = [VERSION, POLICY_VERSION] + [sources[path] for path in
                    (Path(__file__), Path(base.__file__), RUNNER, base.HOST, base.SCORER)]
    run_id = hashlib.sha256(json.dumps(identities, separators=(',', ':')).encode()).hexdigest()
    if not args.resume and os.path.lexists(args.checkpoint):
        raise FileExistsError('checkpoint already exists')
    key = base.load_key(args.key_file, resume=args.resume)
    return qualify(base.PUBLIC_IDS, checkpoint=args.checkpoint, run_id=run_id, make_environment=factory,
                   make_policy=policy_for, verify_outcome=verify, state_adapter=Continuation(key),
                   wall_seconds=args.wall_seconds, max_decisions=args.max_decisions, resume=args.resume)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--key-file', type=Path, required=True)
    parser.add_argument('--host-sha256', required=True)
    parser.add_argument('--scorer-sha256', required=True)
    parser.add_argument('--wall-seconds', type=float, default=600)
    parser.add_argument('--max-decisions', type=int, default=1)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--opt-in-passive-60', action='store_true')
    args = parser.parse_args()
    if not args.opt_in_passive_60:
        parser.error('explicit --opt-in-passive-60 is required')
    if (not math.isfinite(args.wall_seconds) or not 0 < args.wall_seconds <= 1800
            or not 1 <= args.max_decisions <= 1440):
        parser.error('invalid bounded run limits')
    for pin in (args.host_sha256, args.scorer_sha256):
        if len(pin) != 64 or any(c not in '0123456789abcdef' for c in pin):
            parser.error('invalid SHA-256')
    if hasattr(os, 'sched_getaffinity') and hasattr(os, 'sched_setaffinity'):
        allowed = os.sched_getaffinity(0)
        os.sched_setaffinity(0, {min(allowed)})
    started = time.monotonic()
    try:
        result = run(args)
    except Exception:
        print('Private passive run failed; existing checkpoint was retained.', file=sys.stderr)
        return 1
    counts = {key: sum(row['status'] == key for row in result['cases'])
              for key in ('restricted_scored', 'censored', 'blocked', 'incomplete')}
    print(json.dumps(dict(version=VERSION, mode='unscored_passive_60_minute',
                          scope='restricted_no_ground_no_evac', counts=counts,
                          elapsed_seconds=round(time.monotonic()-started, 2))))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
