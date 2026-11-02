"""Render a private source-matrix JSON snapshot as a standalone HTML report.

Example: python -m eval.render_source_matrix_report --report /private/report.json --output /private/review.html
The output must not already exist. No checkpoints or trajectory files are read.
"""
import argparse
from collections import Counter
import html
import json
import math
import os
from pathlib import Path


def esc(value):
    return html.escape(str(value), quote=True)


def value(item):
    if item is None or isinstance(item, (dict, list)):
        return '—'
    if isinstance(item, bool):
        return 'yes' if item else 'no'
    if isinstance(item, (int, float)):
        if not math.isfinite(item):
            return '—'
        return f'{item:.6g}'
    return esc(item)


def identity(cell):
    return tuple(cell.get(key) for key in ('runtime_id', 'case_id', 'policy'))


def display_rows(rows, headings):
    header = ''.join(f'<th scope="col">{esc(heading)}</th>' for heading in headings)
    body = ''.join('<tr>' + ''.join(f'<td>{item}</td>' for item in row) + '</tr>' for row in rows)
    return f'<div class="scroll"><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></div>'


def findings(items):
    if not items:
        return 'None reported'
    return '<ul>' + ''.join('<li>' + ' · '.join(value(item.get(k)) for k in
        ('category', 'code', 'count')) + '</li>' for item in items) + '</ul>'


def render(report):
    expected = report.get('expected_cells', [])
    cells = report.get('cells', [])
    if not isinstance(expected, list) or not isinstance(cells, list):
        raise ValueError('Expected cells and cells must be lists')
    by_id = {}
    for cell in cells:
        key = identity(cell)
        if None in key or key in by_id:
            raise ValueError('Missing or duplicate cell identity')
        by_id[key] = cell
    expected_ids = [identity(item) for item in expected]
    if any(None in key for key in expected_ids) or len(set(expected_ids)) != len(expected_ids):
        raise ValueError('Missing or duplicate expected cell identity')
    # Preserve unexpected cells rather than hiding evidence in an inconsistent report.
    keys = expected_ids + [key for key in by_id if key not in set(expected_ids)]
    statuses = Counter(by_id.get(key, {}).get('status', 'missing') for key in keys)
    status_text = ' · '.join(f'{esc(k)}: {v}' for k, v in sorted(statuses.items())) or 'No cells recorded'
    rows, details = [], []
    for index, key in enumerate(keys, 1):
        cell = by_id.get(key, {})
        status = cell.get('status', 'missing')
        outcome = cell.get('outcome') or {}
        verification = cell.get('verification') or {}
        eligible = (status == 'complete' and cell.get('minute') == 1440
                    and outcome.get('completed_minutes') == 1440
                    and outcome.get('score_eligible') is True
                    and all(verification.get(k) is True for k in
                            ('passed', 'exact_replay', 'independent_reward_agreement')))
        score = value(outcome.get('graded_score')) if eligible else 'unscored'
        if score == '—':
            score = 'unscored'
        # Incomplete and ineligible outcomes cannot be presented as scored components.
        def component(name):
            result = value(outcome.get(name)) if eligible else '—'
            return 'unscored' if result == '—' else result
        label = f'{key[1]} / {key[2]}'
        rows.append([f'<a href="#cell-{index}">{esc(label)}</a>', value(status), value(cell.get('minute')),
                     score, component('protected_population_fraction'), component('new_exposed_population'),
                     component('inherited_exposed_population'), component('additional_burned_hectares'),
                     component('inherited_burned_hectares'), value(cell.get('elapsed_seconds'))])
        coverage = cell.get('operation_coverage') or {}
        coverage_rows = [[esc(name), value(entry.get('status')), value(entry.get('observed')),
                          value(entry.get('limitation'))] for name, entry in sorted(coverage.items())]
        durations = cell.get('durations') or {}
        duration_rows = [[esc(name), value(entry.get('count')), value(entry.get('min_minutes')),
                          value(entry.get('median_minutes')), value(entry.get('max_minutes'))]
                         for name, entry in sorted(durations.items())]
        pins = cell.get('pins') or {}
        pin_rows = [[esc(name), value(pins.get(name))] for name in
                    ('inputs_sha256', 'network_sha256', 'scenario_sha256')]
        verification_rows = [[esc(name), value(verification.get(name))] for name in
                             ('passed', 'exact_replay', 'independent_reward_agreement')]
        checks = cell.get('resume_checks') or []
        check_rows = [[value(item.get('boundary')), value(item.get('passed'))] for item in checks]
        details.append(f'<details id="cell-{index}"><summary>{esc(label)} · {value(status)}</summary>'
            f'<p>Runtime: {value(key[0])} · Reason: {value(cell.get("reason"))} · '
            f'Contradictions: {value(cell.get("runtime_contradictions"))} · '
            f'Commands: {value(cell.get("command_count"))} · Tool calls: {value(cell.get("tool_call_count"))}</p>'
            f'<p>Score eligible: {value(outcome.get("score_eligible"))} · '
            f'Reward version: {value(outcome.get("reward_version"))} · '
            f'Boundary censored: {value(outcome.get("boundary_censored"))} · '
            f'Query independence: {value((cell.get("query_independence") or {}).get("passed"))}</p>'
            f'<h3>Findings</h3>{findings(cell.get("findings") or [])}'
            f'<h3>Verification</h3>{display_rows(verification_rows, ("Check", "Passed"))}'
            f'<h3>Continuation checks</h3>{display_rows(check_rows, ("Boundary", "Passed")) if check_rows else "<p>Not recorded</p>"}'
            f'<h3>Coverage</h3>{display_rows(coverage_rows, ("Operation", "Status", "Observed", "Limitation")) if coverage_rows else "<p>Not recorded</p>"}'
            f'<h3>Durations (simulated minutes)</h3>{display_rows(duration_rows, ("Operation", "Count", "Min", "Median", "Max")) if duration_rows else "<p>Not recorded</p>"}'
            f'<h3>Cell pins</h3>{display_rows(pin_rows, ("Pin", "Value"))}</details>')
    top_pins = [[esc(key), value(report.get(key))] for key in
                ('version', 'implementation_sha256', 'validator_sha256', 'lifecycle_sha256', 'package_sha256')]
    static_rows = [[value(c.get('runtime_id')), value(c.get('case_id')),
                    value(c.get('counterfactual_status')), findings(c.get('findings') or [])]
                   for c in report.get('static_cases', [])]
    runtime_rows = [[value(f.get('runtime_id')), value(f.get('case_id')), value(f.get('policy')),
                     value(f.get('category')), value(f.get('code')), value(f.get('count'))]
                    for f in report.get('runtime_findings', [])]
    limitations = ''.join('<li>' + value(item) + '</li>' for item in report.get('limitations', []))
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Source matrix review</title>
<style>body{{font:14px system-ui,sans-serif;color:#20333a;max-width:1500px;margin:2rem auto;padding:0 1rem}}
h1,h2,h3{{color:#254e5b}}.banner{{background:#edf5f2;border-left:4px solid #508b79;padding:1rem}}
.scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{text-align:left;
vertical-align:top;padding:.45rem;border-bottom:1px solid #d9e3e3}}th{{background:#eff4f4}}details{{border:1px solid #d9e3e3;
border-radius:5px;margin:.4rem 0;padding:.7rem}}summary{{cursor:pointer;font-weight:600}}td,li{{overflow-wrap:anywhere}}
code{{overflow-wrap:anywhere}}ul{{margin:.3rem 0;padding-left:1.2rem}}</style></head><body>
<h1>Source-map development matrix</h1><div class="banner"><strong>Status: {value(report.get('status'))}</strong>
<p>{status_text} · Expected: {len(expected)} · Recorded: {len(cells)} · Workers: {value(report.get('workers'))}</p>
<p>Elapsed: {value(report.get('elapsed_seconds'))} seconds · Peak RSS: {value(report.get('peak_rss_bytes'))} bytes</p></div>
<p>Private development evidence for synthetic exercises. Scores and components below are shown only for complete,
eligible, verified 1,440-minute cells. A dash means a field was not recorded. This report does not establish physical calibration.</p>
<h2>Results by case and policy</h2><p>Population fraction is the recorded protected fraction; exposure is simulated population.
Burn values are hectares. Wall time is per-cell elapsed seconds. Open a case for coverage and pins.</p>
{display_rows(rows, ('Case / policy', 'Status', 'Minute', 'Score', 'Protected fraction', 'New exposure', 'Inherited exposure', 'Additional burn ha', 'Inherited burn ha', 'Wall seconds'))}
<h2>Cell details</h2>{''.join(details) if details else '<p>No cells recorded</p>'}
<h2>Runtime findings</h2>{display_rows(runtime_rows, ('Runtime', 'Case', 'Policy', 'Category', 'Code', 'Count')) if runtime_rows else '<p>None reported</p>'}
<h2>Static findings</h2>{display_rows(static_rows, ('Runtime', 'Case', 'Counterfactual', 'Findings (category · code · count)')) if static_rows else '<p>Not recorded</p>'}
<h2>Reproducibility pins</h2>{display_rows(top_pins, ('Field', 'Value'))}
<h2>Recorded limitations</h2>{'<ul>' + limitations + '</ul>' if limitations else '<p>Not recorded</p>'}
</body></html>'''


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    report = json.loads(args.report.read_text(encoding='utf-8'))
    document = render(report)
    # Exclusive creation prevents overwriting the live JSON or its runner HTML.
    fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as output:
        output.write(document)


if __name__ == '__main__':
    main()
