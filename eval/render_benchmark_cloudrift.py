"""Publish a compact evaluation report without copying maps or private audits."""
import argparse
from collections import Counter
import hashlib
import html
from itertools import groupby
import json
from pathlib import Path

from eval.run_ops_model import dump


def render(source, destination, comparison=None):
    source, destination = Path(source), Path(destination)
    summary = json.loads((source/'summary.json').read_text())
    artifact = dict(summary=summary, requests={}, source_sha256={}, failure_counts={})
    if (source/'actor_manifest.json').exists():
        raw = (source/'actor_manifest.json').read_bytes()
        artifact['actor_manifest'] = json.loads(raw)
        artifact['source_sha256']['actor_manifest.json'] = hashlib.sha256(raw).hexdigest()
    counts = Counter()
    for episode in summary['episodes']:
        relative = Path(episode['task_id'])/'model'/'requests.json'
        raw = (source/relative).read_bytes()
        requests = json.loads(raw)
        artifact['requests'][episode['task_id']] = requests
        artifact['source_sha256'][relative.as_posix()] = hashlib.sha256(raw).hexdigest()
        for request in requests:
            if not request.get('protocol_error'):
                continue
            diagnostic = request['response_diagnostics']
            choice = diagnostic['choices'][0]
            calls = choice.get('message', {}).get('tool_calls', [])
            content = choice.get('message', {}).get('content') or ''
            label = ('tool_markup_in_plain_text' if not calls and ('<invoke' in content or 'DSML' in content)
                     else 'multiple_tool_calls' if len(calls) > 1 else 'invalid_action_arguments')
            counts[label] += 1
    artifact['failure_counts'] = dict(counts)
    comparison_html = ''
    if comparison is not None:
        previous_path = Path(comparison)
        previous = json.loads(previous_path.read_text())['summary']
        invariants = ('task_ids', 'max_decisions', 'ticks_per_action', 'catalog_sha256',
                      'environment_implementation_sha256', 'profile_sha256')
        if any(summary['experiment'][key] != previous['experiment'][key] for key in invariants):
            raise ValueError('Cannot compare runs with different scenario, clock, physics or outcome pins')
        previous_cases = {episode['task_id']: episode for episode in previous['episodes']}
        comparison_rows = []
        for episode in summary['episodes']:
            other = previous_cases.get(episode['task_id'])
            if other is None:
                continue
            comparison_rows.append(dict(task_id=episode['task_id'], family=episode['family'],
                current_score=episode.get('outcomes', {}).get('graded_score') if episode.get('scored') else None,
                previous_score=other.get('outcomes', {}).get('graded_score') if other.get('scored') else None,
                current_minutes=episode.get('minute'), previous_minutes=other.get('minute'),
                current_invalid=episode.get('invalid_actions'), previous_invalid=other.get('invalid_actions')))
        artifact['comparison'] = dict(model=previous['model'], source_sha256=hashlib.sha256(previous_path.read_bytes()).hexdigest(),
                                      matched_invariants=list(invariants), rows=comparison_rows,
                                      model_only_comparison=False)
        comparison_html = '<h2>Same scenarios, different agent setups</h2><div style="overflow:auto"><table><tr><th>Scenario</th><th>Current score / minutes / invalid</th><th>DeepSeek score / minutes / invalid</th></tr>'
        for row in comparison_rows:
            current = f"{row['current_score']} / {row['current_minutes']} / {row['current_invalid']}"
            prior = f"{row['previous_score']} / {row['previous_minutes']} / {row['previous_invalid']}"
            comparison_html += '<tr><td>'+html.escape(row['family'])+'</td><td>'+html.escape(current)+'</td><td>'+html.escape(prior)+'</td></tr>'
        comparison_html += '</table></div><p>Scenario, clock, simulator and outcome pins match. Memory, serialization and request budgets differ. Early abandonment scores are not full-horizon physical outcomes.</p>'
    for relative in ('plan.json', 'summary.json'):
        artifact['source_sha256'][relative] = hashlib.sha256((source/relative).read_bytes()).hexdigest()
    episodes = summary['episodes']
    all_requests = [request for records in artifact['requests'].values() for request in records]
    abandoned = sum(bool(episode.get('outcomes', {}).get('invalid_abandonment')) for episode in episodes)
    horizon = sum(episode.get('minute') == 120 and episode.get('status') == 'complete' for episode in episodes)
    valid_commits = sum(not event['invalid'] for episode in episodes for event in episode['timeline'])
    reads = sum(request.get('tool') in ('get_reports', 'get_events') for request in all_requests)
    transport_errors = sum(bool(request.get('transport_error_type')) for request in all_requests)
    artifact['interpretation'] = (
        f'{abandoned} of {len(episodes)} attempted episodes abandoned after three consecutive invalid commits; '
        f'{horizon} reached the full120-minute horizon. Abandonment scores are zero by '
        'the existing protocol-failure rule, not an observed full-horizon firefighting outcome. '
        'Tool-protocol failures identify an end-to-end model/serving/adapter problem; '
        'the evidence does not isolate which layer introduced malformed serialization. '
        'No retries, repair, fallback, training, or additional provider calls were performed.')
    artifact['next_step'] = (
        'Before another simulator trial, validate the serving tool protocol with a tiny standalone '
        'contract probe, including one-call enforcement and nested argument schemas. The current '
        'CloudRift transport does not forward parallel_tool_calls=False. Investigate support rather '
        'than assuming it fixes plain-text DSML. Preserve invalid responses; do not silently execute '
        'markup or relabel this run as successful. Expose specific schema errors to the model in a '
        'separately versioned adapter experiment before considering environment-policy training.')
    if summary['mode'] == 'subagent_trial':
        artifact['next_step'] = (
            'Inspect independently verified outcomes and action traces before attributing performance '
            'to model quality. This Codex actor retains context across decisions/cases and submits '
            'JSON through a shell wrapper, not the CloudRift serialization path. Its240-request ceiling '
            'differs from the DeepSeek trial. No clean model-only head-to-head claim is supported. '
            'The actor received public observations by instruction; filesystem isolation is not a '
            'security boundary. No policy training or external inference API was used.')
        artifact['reward_caveat'] = (
            'A high scalar score does not mean every fire is extinguished. Population protection '
            'carries80% weight and unburned fraction20%; the latter is normalized over the full '
            'initially burnable grid. Always inspect additional hectares, active fire and matched '
            'reference outcomes separately. No reward weights or outcome definitions were changed '
            'in response to this run.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    dump(destination.with_suffix('.json'), artifact)
    sections = []
    for episode in summary['episodes']:
        blocks = []
        transitions = {event['decision'] - 1: event for event in episode['timeline']}
        for request in artifact['requests'][episode['task_id']]:
            transition = transitions.get(request['decision'], {})
            validation_error = (transition.get('error') if request.get('tool') not in
                                ('get_reports', 'get_events', 'check_action') else None)
            bad = bool(request.get('protocol_error') or validation_error)
            heading = request.get('tool', 'Rejected tool response')
            detail = request.get('response_diagnostics', request.get('proposal', {}))
            if validation_error and not request.get('protocol_error'):
                detail = dict(proposal=detail, validation_error=validation_error)
            block = ('<li class="'+('bad' if bad else 'good')+'"><b>Request '+str(request['request'])
                +' · minute '+str(request['minute'])+' · '+html.escape(heading)+'</b><small>'
                +str(request['seconds'])+' seconds awaiting response</small><details><summary>'
                +('Captured rejection diagnostics' if bad else 'Accepted tool arguments')
                +'</summary><pre>'+html.escape(json.dumps(detail, indent=2, ensure_ascii=False))+'</pre></details></li>')
            blocks.append((request.get('tool') == 'advance_time' and not bad, block))
        timeline = []
        for waiting, group in groupby(blocks, key=lambda item: item[0]):
            entries = [item[1] for item in group]
            if waiting and len(entries) > 2:
                timeline.append('<li><details><summary>'+str(len(entries))+' consecutive wait actions · expand all</summary><ol>'
                                +''.join(entries)+'</ol></details></li>')
            else:
                timeline.extend(entries)
        baseline_rows = ''.join('<li>'+html.escape(name)+': '+str(values['protected_population_fraction']*100)
            +'% population unexposed; '+str(values['additional_burned_hectares'])+' additional hectares burned; score '
            +format(values['graded_score'], '.6f')+'</li>' for name, values in episode['baselines'].items())
        score = str(episode.get('outcomes', {}).get('graded_score')) if episode.get('scored') else 'Unscored'
        label = 'Protocol abandonment score' if episode.get('outcomes', {}).get('invalid_abandonment') else 'Episode outcome'
        sections.append('<section><h2>'+html.escape(episode['family'].replace('_', ' ').title())+'</h2>'
            '<div class="layout"><aside><strong>'+score+'</strong><p>'+label+'</p><dl><dt>Simulated time</dt><dd>'
            +str(episode['minute'])+' /120 minutes</dd><dt>Decisions</dt><dd>'+str(episode['decisions'])
            +'</dd><dt>Invalid commits</dt><dd>'+str(episode['invalid_actions'])+'</dd></dl>'
            +'<p>Unexposed population fraction: '+str(episode.get('outcomes', {}).get('protected_population_fraction', 'unscored'))
            +'<br>Additional burned hectares: '+str(episode.get('outcomes', {}).get('additional_burned_hectares', 'unscored'))
            +'<br>Active fire at end: '+str(episode.get('outcomes', {}).get('active_fire', 'unscored'))+'</p>'
            '<p>Only full-horizon physical outcomes are comparable to120-minute references.</p></aside><ol>'
            +''.join(timeline)+'</ol></div><details><summary>Complete matched CPU references</summary><ul>'
            +baseline_rows+'</ul></details></section>')
    budget = summary['provider_budget']
    page = '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    page += '<title>Model · delivered-evidence benchmark evaluation</title><style>'
    page += 'body{font:16px/1.6 system-ui;max-width:1150px;margin:auto;padding:32px;background:#f5f2ec;color:#27372e}'
    page += 'section{padding:24px;background:#fff;border:1px solid #d8d5cc;border-radius:10px;margin:24px 0}.layout{display:grid;grid-template-columns:230px minmax(0,1fr);gap:24px}table{border-collapse:collapse;width:100%}td,th{padding:12px;text-align:left;border-bottom:1px solid #d8d5cc}'
    page += 'aside{background:#f0eee6;padding:18px;border-radius:8px;align-self:start}aside strong{font-size:36px}dt,small{color:#647365}dd{margin:0 0 12px}'
    page += 'ol{list-style:none;margin:0;padding:0 0 0 20px;border-left:2px solid #d1d4c9}li{margin-bottom:16px}ol li{padding:14px;border:1px solid #d1d4c9;border-radius:6px}'
    page += 'li.bad{border-left:4px solid #ae6147}li.good{border-left:4px solid #54745b}small{display:block}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;max-height:420px;overflow:auto}'
    page += '.notice{border-left:4px solid #ae6147;padding:16px;background:#eee9dd}a{color:#325f51}@media(max-width:720px){.layout{grid-template-columns:1fr}body{padding:16px}}</style>'
    page += '<h1>Model · delivered-evidence benchmark</h1><p>'+html.escape(summary['model'])+'</p>'
    page += '<p>'+str(len(episodes))+' development cases · '+str(summary['requests'])+' request attempts · '+str(summary['elapsed_seconds'])+' seconds.</p>'
    if 'estimated_usd' in budget:
        page += '<p>$'+format(budget['estimated_usd'], '.6f')+' estimated of $'+str(budget['estimated_usd_cap'])+' cap.</p>'
    else:
        page += '<p>No external inference API calls. Codex usage is not metered by this harness; no dollar-cost claim.</p>'
    page += '<p class="notice">'+html.escape(artifact['interpretation'])+'</p><h2>What failed</h2><ul>'
    page += ''.join('<li>'+html.escape(name.replace('_', ' '))+': '+str(count)+'</li>' for name, count in counts.items())+'</ul>'
    page += '<p>'+str(valid_commits)+' valid action commits; '+str(reads)+' valid read calls; '+str(sum(counts.values()))+' protocol rejections; '+str(transport_errors)+' transport errors. Completed episodes are independently verified before scoring.</p>'
    page += comparison_html
    if artifact.get('reward_caveat'):
        page += '<p class="notice">'+html.escape(artifact['reward_caveat'])+'</p>'
    page += ''.join(sections)+'<h2>Recommended next experiment</h2><p>'+html.escape(artifact['next_step'])+'</p>'
    page += '<p>Development probes on one source-derived landscape; synthetic operations, not real incident records. '
    page += 'Where present, API cost estimates use uncached token prices, not a billing receipt. This is not a held-out benchmark or training result. '
    page += 'Full trajectories, maps and private scoring audits are retained locally, not copied into this public report.</p><p><a href="'
    page += html.escape(destination.with_suffix('.json').name)+'">Machine-readable report and captured tool diagnostics</a></p></html>'
    destination.write_text(page)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--comparison')
    args = parser.parse_args()
    render(args.source, args.output, args.comparison)
