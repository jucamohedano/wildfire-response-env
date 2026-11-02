"""Derive an opt-in score/adverse view from FINISHED private revalidation evidence.

Usage after all 48 cells finish:
  python -m eval.derive_evaluation_scores --revalidation /path/to/revalidation \
    --expected-report-sha256 REVIEWED_FINAL_REPORT_SHA256 --output /new/private-view.json

This reporting tool is separate from the frozen observer-only revalidator. It
never resets an environment, invokes a policy, or resumes/repeats replay. It uses
only hashed saved evidence and the same detailed adverse-recovery proof function.
The original strict report and its withhold-policy lineage are preserved.
"""
import argparse
import copy
import json
import math
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'envs/wildfire_response/core/src'))
from eval import revalidate_evaluation as rv

VERSION = 'wildfire-evaluation-derived-score-view-v1'


def require(condition, detail):
    if not condition:
        raise ValueError(detail)


def projection(saved):
    """Recover immutable resource descriptors from the hashed initial observation."""
    resources = saved['trajectory']['initial_observation']['resources']
    require(isinstance(resources, list), 'Initial resource descriptors missing')
    specs = {u['id']: u for u in resources}
    audit = saved['audit']
    require(len(specs) == len(resources) and len(audit) == 1441,
            'Resource roster or minute audit incomplete')
    for minute, row in enumerate(audit):
        require(row['minute'] == minute and set(row['resources']) == set(specs),
                'Audit clock or resource roster differs')
        for identifier, unit in specs.items():
            require(unit.get('kind') in ('engine', 'crew', 'aircraft')
                    and type(unit.get('duty_end_at')) is int
                    and isinstance(unit.get('base_position'), list)
                    and len(unit['base_position']) == 2
                    and all(type(x) in (int, float) and math.isfinite(x) for x in unit['base_position'])
                    and row['resources'][identifier]['duty_end_at'] == unit['duty_end_at'],
                    'Missing/inconsistent base, kind or duty evidence')
    return SimpleNamespace(_scenario={'resources': resources}, _audit=audit,
                           bus=SimpleNamespace(reports=saved['reports']))



def terminal_assistance_proof(result, findings, env):
    """Prove a modeled stationary negative outcome only through minute 1440."""
    if (result['status'] != 'blocked' or not findings
            or any(f.get('category') != 'unsupported' or f.get('code') != 'terminal_assistance_after_shift'
                   or f.get('minute') != 1440 for f in findings)
            or rv.runner.uninspectable_coverage(result.get('operation_coverage', {}))):
        return []
    coverage = result.get('movement_evidence', {})
    if (coverage.get('ticks_observed') != 1440 or coverage.get('capacity_ticks_checked') != 1440
            or coverage.get('timed_positions_checked', 0) <= 0
            or any(coverage.get(k, 0) for k in ('journey_unverifiable', 'position_unverifiable',
                       'multi_minute_ticks_unobserved', 'road_geometry_unverifiable'))):
        return []
    rows = env._audit
    if len(rows) != 1441 or any(row.get('minute') != i for i, row in enumerate(rows)):
        return []
    specs = {u['id']: u for u in env._scenario['resources']}
    evidence, seen = [], set()
    for finding in findings:
        rid = finding.get('resource')
        unit = specs.get(rid)
        if rid in seen or unit is None or unit['kind'] not in ('engine', 'crew'):
            return []
        seen.add(rid)
        deadline = unit['duty_end_at']
        if not 0 <= deadline < 1440:
            return []
        matches = []
        for report in env.bus.reports:
            observed, payload = report.get('observed_at'), report.get('payload', {})
            position = payload.get('position')
            if (report.get('kind') != 'egress_unavailable' or payload.get('resource_id') != rid
                    or type(observed) is not int or not 0 <= observed <= deadline
                    or not isinstance(position, list) or len(position) != 2
                    or any(type(x) not in (int, float) or not math.isfinite(x) for x in position)):
                continue
            waiting = rows[observed:1441]
            if all(r['resources'][rid]['state'] == 'needs_assistance'
                   and math.dist(r['resources'][rid]['position'], position) <= 1e-6
                   and not any(w.get('resource_id') == rid for w in r.get('applied_work', []))
                   for r in waiting):
                matches.append(observed)
        if not matches:
            return []
        evidence.append(dict(resource_id=rid, egress_unavailable_at=min(matches), duty_end_at=deadline,
            observed_through_minute=1440, terminal_state='needs_assistance',
            recovery_status='unresolved_at_horizon'))
    return evidence


def verify_cell(corrected, original, source_cell, row, freeze):
    result = corrected['result']
    rv.validate_saved_cell(source_cell, original, row, freeze)
    require(rv.runner._identity(result) == rv.runner._identity(source_cell), 'Cell identity differs')
    rv.same(result['pins'], source_cell['pins'], 'Corrected/source scenario pins')
    rv.same(result['outcome'], original['outcome'], 'Unchanged raw outcome')
    require(result.get('source_status') == source_cell['status']
            and result.get('source_score') == source_cell.get('score'), 'Source status/score lineage differs')
    require(result.get('adverse_outcome_policy') == 'withhold', 'Source cell policy is not withhold')
    require(result.get('minute') == 1440 and result.get('split') == 'evaluation'
            and result.get('training_eligible') is False, 'Evaluation completion identity missing')
    proof = result['recorded_call_revalidation']
    require(proof.get('passed') is True and proof.get('policy_executed') is False,
            'Exact recorded-call replay proof missing')
    for label, content in (('trajectory', original['trajectory']), ('audit', original['audit']),
                           ('reports', original['reports']), ('orders', original['orders']),
                           ('outcome', original['outcome'])):
        rv.same(proof[label + '_sha256'], rv.checksum(content), 'Replay ' + label + ' hash')
    trace = original['trajectory']
    require(proof['calls'] == len(original['calls']) and proof['transitions'] == len(trace['transitions'])
            and proof['queries'] == len(trace['queries']), 'Replay counts differ')
    require(trace['transitions'] and trace['transitions'][-1]['terminated'] is True
            and trace['transitions'][-1]['truncated'] is False, 'Terminal transition missing')
    outcome = result['outcome']
    require(outcome.get('score_eligible') is True and outcome.get('completed_minutes') == 1440
            and outcome.get('invalid_abandonment') is False and outcome.get('boundary_censored') is False,
            'Outcome is unfinished, abandoned or censored')
    independent = rv.recompute(original['audit'], terminal=True, abandoned=False, censored=False)
    rv.same(independent, original['verification']['outcomes'], 'Saved independent reward')
    rv.same(rv.checksum(independent), proof['independent_reward_sha256'], 'Independent reward replay hash')
    rv.reward_agreement(outcome, independent)
    rv.same(result['resume_checks'], original['resume_checks'], 'Inherited policy continuation checks')
    rv.same(result['query_independence'], source_cell['query_independence'], 'Inherited query checks')
    inherited = result['inherited_policy_checks']
    require(inherited.get('reexecuted') is False, 'Inherited checks mislabeled')
    rv.same(inherited['resume_checks_sha256'], rv.checksum(original['resume_checks']), 'Continuation evidence hash')
    rv.same(inherited['query_independence_sha256'], rv.checksum(source_cell['query_independence']), 'Query evidence hash')
    require(all(result.get('verification', {}).get(k) is True for k in
                ('passed', 'exact_replay', 'independent_reward_agreement')), 'Corrected verification missing')
    rv.same(result['movement_evidence'], corrected['corrected_observer']['coverage'], 'Corrected observer coverage')
    rv.same(result['findings'], rv.runner.aggregate_findings(corrected['corrected_findings']), 'Corrected finding aggregation')
    rv.same(corrected['original_findings'], original['findings'], 'Original findings')
    require(not corrected['corrected_observer']['findings'] or result['status'] == 'failed',
            'Observer contradictions cannot receive an accepted score')
    # The identical detailed proof runs against a projection of immutable saved
    # descriptors, audit and reports, rather than a live simulator instance.
    env = projection(original)
    derived = copy.deepcopy(result)
    rv.apply_reporting(derived, corrected['corrected_findings'], env, source_cell, 'score')
    rv.same(derived['adverse_recovery_evidence'], result['adverse_recovery_evidence'],
            'Recomputed adverse proof versus revalidation proof')
    if result['status'] == 'complete':
        require(not rv.runner.uninspectable_coverage(result.get('operation_coverage', {})),
                'Strict complete cell lacks required coverage')
        raw_score = outcome.get('graded_score')
        require(type(raw_score) in (int, float) and math.isfinite(raw_score)
                and result.get('score') == raw_score and result.get('reported_score') == raw_score,
                'Strict complete score differs from raw reward')
    elif result['status'] == 'blocked' and derived['adverse_recovery_evidence']:
        require(result.get('score') is None and result.get('reported_score') is None
                and rv.reportable_adverse(derived), 'Adverse score proof is incomplete')
    else:
        require(result.get('score') is None and result.get('reported_score') is None,
                'Unaccepted cell has a source score')
        derived['reported_score'] = None
        derived['operational_status'] = 'unresolved'
        terminal = terminal_assistance_proof(result, corrected['corrected_findings'], env)
        derived['terminal_assistance_evidence'] = terminal
        if terminal:
            raw_score = outcome.get('graded_score')
            require(type(raw_score) in (int, float) and math.isfinite(raw_score), 'Invalid raw reward')
            derived['reported_score'] = raw_score
            derived['operational_status'] = 'unresolved_at_horizon'
    derived['adverse'] = derived['operational_status'] in ('adverse', 'unresolved_at_horizon')
    derived.setdefault('terminal_assistance_evidence', [])
    return derived


def build_view(revalidation, expected_report_sha256):
    root = Path(revalidation).resolve(strict=True)
    ledger = {}
    def read(relative):
        path = root / relative
        require(path.resolve().is_relative_to(root), 'Evidence path escapes source directory')
        raw = rv.read_bytes(path)
        ledger[relative] = rv.sha(raw)
        return raw
    raw_report = read('report.json')
    require(rv.sha(raw_report) == expected_report_sha256, 'Final revalidation report SHA256 differs')
    strict = rv._json(raw_report)
    manifest = rv._json(read('source_manifest.json'))
    require(strict.get('version') == manifest.get('version') == rv.VERSION and strict.get('revalidation') is True
            and strict.get('adverse_outcome_policy') == manifest.get('adverse_outcome_policy') == 'withhold',
            'Expected an original withhold-policy revalidation')
    rv.same(manifest['sha256'], rv.checksum({k: v for k, v in manifest.items() if k != 'sha256'}), 'Source manifest checksum')
    rv.same(strict['source_snapshot_sha256'], manifest['sha256'], 'Source snapshot lineage')
    rv.same(strict['code'], manifest['code'], 'Validator lineage')
    code = manifest['code']
    require(rv.sha(Path(rv.__file__).read_bytes()) == code['revalidator_sha256']
            and rv.sha((rv.ROOT / rv.OBSERVER).read_bytes()) == code['reviewed_observer_sha256']
            and rv.runner.validator_digest() == code['corrected_validator_sha256'],
            'Detailed-proof, observer or corrected-validator code differs')
    source = rv._json(read('source/report.json'))
    freeze = rv._json(read('source/freeze.json'))
    require(freeze.get('version') == 'evaluation-freeze-v1' and freeze.get('training') is False,
            'Source freeze identity missing')
    rv.same(strict['source_pins'], rv.source_pins(freeze), 'Frozen source pins')
    rv.same(source['pins'], strict['source_pins'], 'Original report pins')
    rv.same(strict['pins'], dict(strict['source_pins'], validator_sha256=code['corrected_validator_sha256']),
            'Corrected report pins')
    require(code['source_git_commit'] == freeze['code_commit']
            and code['source_validator_sha256'] == freeze['validator_sha256']
            and rv.implementation_digest() == freeze['implementation_sha256']
            and rv.registry()['sha256'] == freeze['lifecycle_sha256']
            and rv.REWARD_VERSION == freeze['reward_identity'], 'Frozen engine/reward/code identity differs')
    expected = strict['expected_cells']
    rv.same(expected, manifest['expected_cells'], 'Manifest domain')
    rv.same(expected, source['expected_cells'], 'Original domain')
    rv.same(expected, freeze['cells'], 'Frozen domain')
    rv.same([rv.runner._identity(c) for c in strict['cells']], [rv.runner._identity(c) for c in expected], 'Corrected cell domain')
    rv.same([rv.runner._identity(c) for c in source['cells']], [rv.runner._identity(c) for c in expected], 'Original cell domain')
    require(len(expected) == 48 and manifest['snapshot_finished_cells'] == 48
            and strict.get('source_snapshot_finished_cells') == 48
            and all(c.get('status') in rv.FINISHED for c in strict['cells'] + source['cells']),
            'All 48 source and corrected cells must be finished before deriving scores')
    refreshed = copy.deepcopy(strict)
    rv.refresh_revalidation_report(refreshed)
    rv.same(refreshed['status'], strict['status'], 'Strict aggregate status')
    rv.same(refreshed['baseline_coverage_contract'], strict['baseline_coverage_contract'], 'Strict coverage contract')
    require(strict.get('reporting_status') == strict['status'], 'Withhold reporting status differs')
    rows = {(r['runtime_id'], r['task_id']): r for r in freeze['tasks']}
    require(len(rows) == len(freeze['tasks']) == 24, 'Frozen evaluation rows missing or duplicated')
    source_files = {'report.json', 'freeze.json'} | {'private/' + rv.filename(c) for c in expected}
    require(set(manifest['source_hashes']) == source_files, 'Source evidence hash domain incomplete')
    cell_files = {rv.filename(c) + '.json' for c in expected}
    require(set(strict['cell_artifact_sha256']) == cell_files, 'Corrected cell hash domain incomplete')
    derived_cells = []
    for result, source_cell in zip(strict['cells'], source['cells']):
        name = rv.filename(result)
        corrected_raw = read('cells/' + name + '.json')
        require(rv.sha(corrected_raw) == strict['cell_artifact_sha256'][name + '.json'], 'Corrected cell file hash differs')
        corrected = rv._json(corrected_raw)
        rv.same(corrected['sha256'], rv.checksum({k: v for k, v in corrected.items() if k != 'sha256'}), 'Corrected cell checksum')
        rv.same(corrected['result'], result, 'Corrected cell/report result')
        rv.same(corrected['source_snapshot_sha256'], manifest['sha256'], 'Cell snapshot reference')
        raw_source = read('source/private/' + name)
        wanted = manifest['source_hashes']['private/' + name]
        require(rv.sha(raw_source) == wanted == corrected['source_cell_sha256'], 'Original cell evidence hash differs')
        original = rv.evidence(raw_source)
        derived_cells.append(verify_cell(corrected, original, source_cell,
                                         rows[(result['runtime_id'], result['case_id'])], freeze))
    for name, wanted in manifest['source_hashes'].items():
        require(ledger['source/' + name] == wanted, 'Original snapshot evidence hash differs')
    score_report = copy.deepcopy(strict)
    score_report['adverse_outcome_policy'] = 'score'
    score_report['cells'] = derived_cells
    rv.refresh_revalidation_report(score_report)
    eligible = all(c['status'] == 'complete' or
        (c['status'] == 'blocked' and c['adverse'] and c['reported_score'] is not None
         and (c['adverse_recovery_evidence'] or c['terminal_assistance_evidence']))
        for c in derived_cells)
    if (score_report['status'] == 'blocked' and eligible
            and not score_report['baseline_coverage_contract']['unobserved_required']):
        score_report['reporting_status'] = 'complete_with_adverse_outcomes'
    # No live simulator, generator, replay, policy or source mutation occurs.
    for relative, wanted in ledger.items():
        require(rv.sha(rv.read_bytes(root / relative)) == wanted, 'Evidence changed during derivation')
    keys = ('runtime_id', 'case_id', 'policy', 'status', 'score', 'reported_score', 'operational_status',
            'outcome', 'findings', 'adverse_recovery_evidence', 'terminal_assistance_evidence',
            'adverse', 'source_status', 'source_score')
    return dict(version=VERSION, private=True, split='evaluation', training_eligible=False,
        strict_status=strict['status'], reporting_status=score_report['reporting_status'],
        policy_lineage={'original_revalidation_policy': 'withhold', 'user_selected_derived_policy': 'score',
                        'reward_formula_changed': False},
        source_revalidation=str(root), source_report_sha256=expected_report_sha256,
        source_snapshot_sha256=manifest['sha256'], source_code=code,
        derived_script_sha256=rv.sha(Path(__file__).read_bytes()), evidence_sha256=ledger,
        pins=strict['pins'], source_pins=strict['source_pins'],
        cells=[{k: copy.deepcopy(c[k]) for k in keys} for c in derived_cells],
        note='Evidence-only score view. Strict statuses/scores/findings and original artifacts are preserved; '
             'only fully proven named adverse egress or terminal stationary assistance can retain unchanged raw reward. '
             'Unresolved_at_horizon infers no later recovery or resource safe_success; raw outcome fields remain unchanged.')


def write_view(revalidation, output, expected_report_sha256):
    root = Path(revalidation).resolve(strict=True)
    output = Path(output).absolute()
    require(not output.resolve().is_relative_to(root), 'Output must be separate from original revalidation')
    view = build_view(root, expected_report_sha256)
    rv.exclusive(output, json.dumps(view, sort_keys=True, indent=2, allow_nan=False).encode() + b'\n')
    return view


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--revalidation', type=Path, required=True)
    parser.add_argument('--expected-report-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    view = write_view(args.revalidation, args.output, args.expected_report_sha256)
    print(json.dumps({'strict_status': view['strict_status'], 'reporting_status': view['reporting_status'],
                      'cells': len(view['cells'])}))
    return 0 if view['reporting_status'] in ('complete', 'complete_with_adverse_outcomes') else 2


if __name__ == '__main__':
    raise SystemExit(main())
