"""Policy-free quality audit over all registered source-grounded synthetic catalogs."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import resource
import subprocess
import time

from wildfire_core.scenario_quality import RULES, VERSION, audit_catalog, make_context, summarize
from envs.wildfire_response.regional.benchmark_v1 import catalog as benchmark
from envs.wildfire_response.regional.expansion import catalog as expansion
from envs.wildfire_response.regional import stress
from envs.wildfire_response.regional.prepared.data import load_inputs
from envs.wildfire_response.regional.topographic.data import load_domain


CATALOGS = ('benchmark_v1', 'synthetic_expansion_v1', 'topographic_stress_v1')


def catalog_source(name, prepared, domain, inputs):
    if name == 'benchmark_v1':
        catalog = benchmark.build_catalog(prepared, domain)
        return catalog, lambda identifier: benchmark.get_task(catalog, inputs, identifier)
    if name == 'synthetic_expansion_v1':
        catalog = expansion.build_catalog(prepared, domain)
        return catalog, lambda identifier: expansion.get_task(catalog, inputs, identifier)
    if name == 'topographic_stress_v1':
        catalog = stress.build_catalog(prepared, domain)
        return catalog, lambda identifier: stress.get_task(inputs, catalog, identifier)
    raise ValueError('Unknown registered catalog')


def write_json(path, value, *, private=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n')
    if private:
        path.chmod(0o600)
        path.parent.chmod(0o700)


def audit_all(output, prepared, domain, *, catalogs=CATALOGS):
    if not catalogs or len(set(catalogs)) != len(catalogs) or set(catalogs) - set(CATALOGS):
        raise ValueError('Choose unique registered catalogs')
    output = Path(output)
    if output.exists():
        raise ValueError('Choose a fresh audit directory')
    started = time.monotonic()
    context = make_context(prepared, domain)
    summaries = []
    for name in catalogs:
        catalog, resolve = catalog_source(name, prepared, domain, context['inputs'])
        report = audit_catalog(catalog, resolve, context)
        write_json(output / 'private' / name / 'findings.json', report, private=True)
        summaries.append(dict(catalog=name, **summarize(report)))
    root = Path(__file__).resolve().parents[1]
    result = dict(version=VERSION, catalogs=summaries, tasks_checked=sum(row['tasks_checked'] for row in summaries),
        errors=sum(row['errors'] for row in summaries), warnings=sum(row['warnings'] for row in summaries),
        tasks_with_warnings=sum(row['tasks_with_warnings'] for row in summaries),
        note='Shared input warnings appear once per catalog; warning instances are not counts of distinct defective scenarios',
        contract_passed=all(row['contract_passed'] for row in summaries),
        review_required=any(row['review_required'] for row in summaries), empirically_validated=False,
        prepared_inputs_sha256=prepared['sha256'], topographic_inputs_sha256=domain['sha256'],
        source_revision=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
        runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        rules={code: dict(severity=value[0], meaning=value[1]) for code, value in RULES.items()},
        policy_rollouts=0, held_out_outcomes_inspected=False, workers=1,
        elapsed_seconds=round(time.monotonic() - started, 3),
        peak_rss_mib=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2),
        scope='All concrete variants in the three registered catalogs, canonical profile. Archived API development modes and action trajectories are not audited here.',
        limitations=['Synthetic normalized wind, spread/suppression and smoke-dose effects lack empirical calibration',
                     'Off-map bases and assumed ground transit do not establish road/speed realism',
                     'Coastal weather is context, not observed fire-front weather or coupled physical forcing',
                     'No policy solvability/difficulty claims; static approval is not a realistic-release certification'])
    write_json(output / 'public/summary.json', result)
    rows = ''.join('<li>' + html.escape(f"{row['catalog']}: {row['tasks_checked']} tasks, {row['errors']} errors, {row['warnings']} warnings") + '</li>' for row in summaries)
    (output / 'public/report.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>Scenario quality audit</title>'
        '<style>body{font:16px/1.6 system-ui;background:#f4f5ef;color:#20342c;max-width:900px;margin:30px auto;padding:20px}li{margin:12px 0}</style>'
        '<h1>Synthetic scenario quality audit</h1><p>Static consistency, not firefighting certification. '
        'No policy rollouts or held-out outcomes inspected.</p><ul>' + rows + '</ul><h2>Unresolved fidelity limits</h2><ul>'
        + ''.join('<li>' + html.escape(value) + '</li>' for value in result['limitations'])
        + '</ul><p><a href="summary.json">Rules, aggregate findings, source pins and measurements</a></p>'
        '<p>Per-task findings are evaluator-private. Warnings require review, not automatic deletion or relabelling.</p></html>')
    return result


def main():
    parser = argparse.ArgumentParser(__doc__)
    for name in ('prepared-inputs', 'prepared-sha256', 'topographic-inputs', 'topographic-sha256', 'output'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--catalog', choices=(*CATALOGS, 'all'), default='all')
    parser.add_argument('--fail-on-warnings', action='store_true')
    args = parser.parse_args()
    prepared = load_inputs(args.prepared_inputs, expected_sha256=args.prepared_sha256)
    domain = load_domain(args.topographic_inputs, prepared, args.topographic_sha256)
    result = audit_all(args.output, prepared, domain,
        catalogs=CATALOGS if args.catalog == 'all' else (args.catalog,))
    print(json.dumps({key: result[key] for key in ('tasks_checked', 'errors', 'warnings', 'elapsed_seconds', 'peak_rss_mib')}))
    raise SystemExit(0 if result['contract_passed'] and not (args.fail_on_warnings and result['warnings']) else 1)


if __name__ == '__main__':
    main()
