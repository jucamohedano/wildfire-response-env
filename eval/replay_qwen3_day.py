"""CPU replay of recorded model requests; never completes an unfinished trial."""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import sys
import time

from eval.hub_runtime import make_environment
from eval.qwen3_protocol import parse_call
from wildfire_core.incident_day import IncidentDayTools

VERSION = 'qwen3-day-request-replay-v1'
MAX_REQUESTS = 10000
MAX_LINE_BYTES = 4 * 1024 * 1024
MAX_DOCUMENT_BYTES = 256 * 1024 * 1024


class ReplayError(ValueError):
    pass


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReplayError('Duplicate recorded JSON key')
        result[key] = value
    return result


def _decode(raw):
    def invalid(value):
        raise ReplayError('Nonfinite recorded JSON')
    try:
        value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=invalid)
        canonical_hash(value)  # Also rejects overflowed exponents.
        return value
    except (ValueError, TypeError, RecursionError) as error:
        raise ReplayError('Invalid recorded JSON') from error


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _document(path):
    with path.open('rb') as stream:
        raw = stream.read(MAX_DOCUMENT_BYTES + 1)
    if len(raw) > MAX_DOCUMENT_BYTES:
        raise ReplayError('Recorded document exceeds replay byte bound')
    value = _decode(raw)
    if not isinstance(value, dict):
        raise ReplayError('Recorded document must be an object')
    return value


def _summary(case_dir, case_id):
    """Both names occur in collected artifacts; conflicting completion fails closed."""
    rows = []
    for name in ('summary.json', 'modelsummary.json'):
        path = case_dir.parent / name
        if path.exists():
            summary = _document(path)
            matching = [row for row in summary.get('cases', [])
                        if isinstance(row, dict) and row.get('case') == case_id]
            if len(matching) > 1:
                raise ReplayError('Duplicate case in recorded summary')
            rows.extend(matching)
    return rows


def replay_case(data_dir, case_dir, case_id, *, wall_seconds=300):
    """Replay exactly the recorded prefix, returning aggregate diagnostics only.

    Missing final trajectories cannot establish exact replay. Results are scored
    only with verified full completion and an explicit eligible completion summary.
    """
    if type(wall_seconds) not in (int, float) or not math.isfinite(wall_seconds) or wall_seconds <= 0:
        raise ReplayError('Positive finite CPU replay budget required')
    started = time.monotonic()
    case_dir = Path(case_dir)
    env, entry = make_environment(data_dir, 'es-ma-2021', allow_unapproved_data=True)
    if case_id not in entry['scenario_ids']:
        raise ReplayError('Case does not belong to the active source-map runtime')
    env.reset(case_id)
    bridge = IncidentDayTools(env)
    calls, missions = Counter(), Counter()
    count = expired = malformed = results_checked = 0
    budget_stop = False
    with (case_dir / 'requests.jsonl').open('rb') as stream:
        while True:
            if time.monotonic() - started >= wall_seconds:
                raise ReplayError('CPU replay wall budget exceeded')
            raw = stream.readline(MAX_LINE_BYTES + 1)
            if not raw:
                break
            if len(raw) > MAX_LINE_BYTES:
                raise ReplayError('Request record exceeds replay byte bound')
            if count >= MAX_REQUESTS:
                raise ReplayError('Request count exceeds replay bound')
            rec = _decode(raw)
            if not isinstance(rec, dict) or type(rec.get('minute')) is not int or rec['minute'] != env.minute:
                raise ReplayError('Recorded request minute differs from replay')
            if type(rec.get('response')) is not str:
                raise ReplayError('Recorded response must be text')
            executed = rec.get('executed', True)
            if type(executed) is not bool:
                raise ReplayError('Recorded executed flag must be boolean')
            count += 1
            if not executed:
                # A stopped generation has no tool/action effect, even if it parses.
                if any(key in rec for key in ('tool', 'arguments', 'result', 'error')):
                    raise ReplayError('Unexecuted record contains execution evidence')
                expired += 1
                budget_stop = True
                continue
            if env.terminated or env.truncated:
                raise ReplayError('Executed request follows terminal world state')
            try:
                call = parse_call(rec['response'])
            except ValueError as error:
                if 'tool' in rec or 'arguments' in rec or not rec.get('error'):
                    raise ReplayError('Malformed response disagrees with recorded classification')
                observation, reward, terminated, truncated, info = env.step({'malformed_model_response': True})
                malformed += 1
                if 'result' in rec:
                    result = dict(error=str(error), observation=observation, reward=reward,
                                  terminated=terminated, truncated=truncated, info=info)
                    if canonical_hash(result) != canonical_hash(rec['result']):
                        raise ReplayError('Malformed-call result differs from replay')
                    results_checked += 1
            else:
                if (rec.get('error') or rec.get('tool') != call['name'] or 'arguments' not in rec
                        or canonical_hash(rec['arguments']) != canonical_hash(call['arguments'])):
                    raise ReplayError('Recorded tool or arguments differ from raw response')
                result = bridge.execute(call['name'], call['arguments'])
                calls[call['name']] += 1
                if 'result' in rec:
                    if canonical_hash(result) != canonical_hash(rec['result']):
                        raise ReplayError('Recorded public tool result differs from replay')
                    results_checked += 1
                if call['name'] == 'commit_orders':
                    action = call['arguments'].get('action', {})
                    if isinstance(action, dict):
                        for key in ('requests', 'authorizations', 'releases', 'protection_orders', 'status_requests'):
                            if isinstance(action.get(key), list):
                                missions[key] += len(action[key])
            if rec.get('harness_stop') or rec.get('action_within_budget') is False:
                budget_stop = True
    actual = env.trajectory()
    path = case_dir / 'trajectory.json'
    trajectory_match = None
    if path.exists():
        expected = _document(path)
        if canonical_hash(actual) != canonical_hash(expected):
            raise ReplayError('Final trajectory differs from recorded world trace')
        trajectory_match = True
    summary_rows = _summary(case_dir, case_id)
    if any(row.get('reason') for row in summary_rows):
        budget_stop = True
    verification = env.verify_episode() if env.terminated or env.truncated else None
    flags = {key: verification.get(key) for key in ('passed', 'exact_replay', 'independent_reward_agreement')} if verification else None
    outcome = env.outcomes()
    eligible = (trajectory_match is True and env.terminated and not env.truncated and env.minute == 1440
                and not budget_stop and bool(summary_rows)
                and all(row.get('completed') is True and row.get('minute') == 1440
                        and not row.get('truncated') and row.get('reason') is None for row in summary_rows)
                and verification is not None and all(value is True for value in flags.values())
                and outcome['score_eligible'] and not outcome['invalid_abandonment'] and not outcome['boundary_censored'])
    diagnostics = {key: outcome[key] for key in ('additional_burned_hectares', 'inherited_burned_hectares',
        'new_exposed_population', 'inherited_exposed_population', 'unresolved_evacuations')}
    population_status=Counter()
    for group in env.ops.groups.values():population_status[group['status']]+=group['count']
    resource_status=Counter(unit['kind']+':'+unit['state'] for unit in env.ops.resources.values())
    work=[event for record in env._audit for event in record.get('applied_work', [])]
    diagnostics.update(active_fire_cells=len(env._burning()), population_count=sum(g['count'] for g in env.ops.groups.values()),
                       population_by_status=dict(population_status), resource_by_kind_and_state=dict(resource_status),
                       suppression_work_events=len(work), resources_with_applied_work=len({event['resource_id'] for event in work}),
                       received_reports=len(env.bus.reports), resource_count=len(env.ops.resources), tool_call_counts=dict(calls),
                       mission_operations_submitted=dict(missions), public_route_queries=len(actual['queries']))
    return dict(version=VERSION, case_id=case_id, passed=trajectory_match is True,
                requests_replayed=count, results_compared=results_checked, expired_unexecuted=expired,
                malformed_penalties=malformed, minute=env.minute, terminated=env.terminated, truncated=env.truncated,
                trajectory_match=trajectory_match, trajectory_sha256=canonical_hash(actual),
                reason=None if trajectory_match else 'final_trajectory_missing', verification=flags,
                score_eligible=bool(eligible), score=outcome['graded_score'] if eligible else None,
                diagnostics=diagnostics, elapsed_seconds=round(time.monotonic()-started, 3),
                scope='Recorded-prefix consistency; partial diagnostics are not full-horizon policy outcomes.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--case-dir', type=Path, required=True)
    parser.add_argument('--case-id', required=True)
    parser.add_argument('--wall-seconds', type=float, default=300)
    args = parser.parse_args(argv)
    try:
        result = replay_case(args.data_dir, args.case_dir, args.case_id, wall_seconds=args.wall_seconds)
    except (ValueError, OSError, KeyError, RuntimeError) as error:
        parser.exit(2, f'Replay failed: {error}\n')
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
