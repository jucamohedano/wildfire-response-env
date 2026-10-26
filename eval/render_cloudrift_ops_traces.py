import argparse
import hashlib
import html
import json
from pathlib import Path


START = '<!-- saved-trajectories:start -->'
END = '<!-- saved-trajectories:end -->'
OBSERVATION_FIELDS = (
    'minute', 'decision', 'wind', 'daylight', 'aircraft_operable', 'reports',
    'resources', 'districts', 'offers', 'readbacks', 'report_assessments',
    'protection_orders', 'acknowledgements', 'sensor_memory',
)
STYLE = '''
.trace-nav{display:flex;flex-wrap:wrap;gap:10px;margin:20px 0}
.trace-nav a,.trace-download{border:1px solid #bfc8ba;border-radius:6px;padding:8px 13px;background:white;text-decoration:none}
.trace-run{border-top:1px solid #bfc8ba;margin-top:28px;padding-top:22px;scroll-margin-top:20px}
.trace-layout{display:grid;grid-template-columns:235px minmax(0,1fr);gap:25px;align-items:start}
.trace-sidebar{position:sticky;top:18px;background:#eeeee5;border:1px solid #cdd0c0;border-radius:8px;padding:18px}
.trace-sidebar h3{font-size:20px;margin:0 0 5px}.trace-score{font-size:30px;font-weight:700;margin:14px 0 0}
.trace-sidebar dl{font-size:14px;margin-bottom:0}.trace-sidebar dt{color:#52644f;margin-top:12px}.trace-sidebar dd{margin:0;overflow-wrap:anywhere}
.trace-timeline{list-style:none;padding:0 0 0 23px;margin:0;border-left:2px solid #cdd0c0}
.trace-event{position:relative;margin:0 0 18px}.trace-event:before{content:'';position:absolute;left:-30px;top:18px;width:10px;height:10px;border:2px solid #f3f5f0;border-radius:50%;background:#487059}
.trace-event.error:before{background:#a74232}.trace-event.timeout:before{background:#a36c25}
.trace-event>details,.trace-boundary{border:1px solid #ccd6c5;border-radius:7px;background:white;overflow:hidden}
.trace-event.error>details{border-color:#cda69b}.trace-event.timeout>details{border-color:#d0bb95}
.trace-event summary{cursor:pointer;padding:12px 15px}.trace-event summary:hover{background:#f0f2eb}
.trace-event summary:focus-visible{outline:3px solid #487059;outline-offset:-3px}
.trace-event summary strong{font-size:15px}.trace-meta{display:block;font:12px/1.6 ui-monospace,monospace;color:#52644f;margin-top:4px}
.trace-badge{font-size:11px;letter-spacing:.04em;text-transform:uppercase;border:1px solid #bcc9b7;border-radius:3px;padding:2px 5px;margin-right:7px;white-space:nowrap}
.trace-body{padding:0 15px 15px}.trace-body p{margin:10px 0;font-size:14px}.trace-body details{margin-top:10px}
.trace-body details summary{padding:6px 0;font-size:14px}.trace-body details summary:hover{background:transparent}
.trace-body pre{margin:7px 0 0;background:#222c25;color:#ecf0e6;padding:13px;border-radius:5px;font:12px/1.6 ui-monospace,monospace;white-space:pre-wrap;overflow-wrap:anywhere;max-height:330px;overflow:auto}
.trace-note{border-left:3px solid #b16b29;padding-left:10px;color:#58604e}
.trace-boundary{padding:12px 15px;background:#eeeee5;font-size:14px}.trace-boundary p{margin:5px 0}
.trace-event .trace-output{font-weight:600}.trace-help{font-size:14px;color:#52644f}
@media(max-width:760px){.trace-layout{grid-template-columns:1fr;gap:18px}.trace-sidebar{position:static}.trace-sidebar dl{display:grid;grid-template-columns:1fr 1fr;gap:0 14px}.trace-sidebar dt{grid-column:1}.trace-sidebar dd{grid-column:2;align-self:end}.trace-timeline{margin-left:6px}body{padding:16px}}
@media print{.trace-sidebar{position:static}.trace-body pre{max-height:none}.trace-layout{grid-template-columns:190px minmax(0,1fr)}}
'''


def public_observation(observation):
    return {key: observation[key] for key in OBSERVATION_FIELDS if key in observation}


def read_source(path, sources):
    payload = path.read_bytes()
    sources.append({'path': path.as_posix(), 'sha256': hashlib.sha256(payload).hexdigest()})
    return json.loads(payload)


def build_case(runtime_root, outcome):
    case_id = outcome['task_id']
    sources = []
    resumed = runtime_root / 'cloudrift-ops-eval-20261026-resumed' / case_id
    filename = 'trajectory.json' if outcome['scored'] else 'partial-trajectory.json'
    trajectory = read_source(resumed / filename, sources)
    phases = []
    if case_id == 'topo-dev-001':
        initial = runtime_root / 'cloudrift-ops-eval-20261026' / case_id
        prefix = read_source(initial / 'partial-trajectory.json', sources)
        if (prefix['initial_observation'] != trajectory['initial_observation']
                or prefix['transitions'] != trajectory['transitions'][:len(prefix['transitions'])]):
            raise ValueError('Continuation does not preserve the canonical prefix')
        phases.append(('Initial attempt', read_source(initial / 'requests.json', sources)))
    phases.append(('Continuation' if phases else 'Initial attempt',
                   read_source(resumed / 'requests.json', sources)))
    events = []
    committed = 0
    for phase, requests in phases:
        for request in requests:
            observation = (trajectory['initial_observation'] if committed == 0
                           else trajectory['transitions'][committed - 1]['observation'])
            if request['decision'] != committed or request['minute'] != observation['minute']:
                raise ValueError('Request log does not align with the canonical decision clock')
            event = {key: request[key] for key in (
                'request', 'decision', 'minute', 'tool', 'seconds', 'usage', 'proposal',
                'protocol_error', 'transport_error_type', 'action_check',
            ) if key in request}
            event.update(phase=phase, sequence=len(events) + 1)
            if request.get('transport_error_type'):
                event['kind'] = 'timeout'
            elif request.get('tool') == 'check_action':
                event['kind'] = 'check'
            else:
                step = trajectory['transitions'][committed]
                invalid = bool(request.get('protocol_error'))
                expected = ({'_invalid_ops_action': True} if invalid else
                            {key: value for key, value in request['proposal'].items()
                             if key != 'explanation'})
                if step['action'] != expected or step['action_valid'] == invalid:
                    raise ValueError('Request action does not match its canonical transition')
                event.update(
                    kind='invalid' if invalid else 'submit',
                    action=step['action'], action_valid=step['action_valid'],
                    objective_reward=step['objective_reward'],
                    terminated=step['terminated'], truncated=step['truncated'],
                    observation_after=public_observation(step['observation']),
                )
                committed += 1
            events.append(event)
    if committed != len(trajectory['transitions']):
        raise ValueError('Canonical transitions are missing request records')
    return {
        'task_id': case_id, 'family': outcome['family'], 'seed': trajectory['seed'],
        'score': outcome.get('score'), 'scored': outcome['scored'],
        'status': outcome['status'], 'decisions': committed,
        'reward_version': trajectory['reward_version'],
        'contract_version': 'wildfire-ops-model-tools-v1',
        'initial_observation': public_observation(trajectory['initial_observation']),
        'events': events, 'sources': sources,
    }


def build_export(runtime_root, validation):
    return {
        'version': 'wildfire-ops-public-trace-excerpts-v1',
        'model': validation['requested_model'],
        'scope': 'Curated public trace excerpts, not full replay files or expert demonstrations. '
                 'Static terrain arrays, private evaluator artifacts and credentials are excluded. '
                 'Brief logged explanations are not hidden reasoning traces. Malformed raw responses '
                 'were not retained; their exact parser failure cannot be reconstructed.',
        'cases': [build_case(runtime_root, outcome) for outcome in validation['outcomes']
                  if outcome['status'] != 'not_started'],
    }


def escape(value):
    return html.escape(str(value), quote=True)


def json_panel(title, payload):
    return (f'<details><summary>{escape(title)}</summary><pre>'
            f'{escape(json.dumps(payload, indent=2, ensure_ascii=False))}</pre></details>')


def observation_summary(observation):
    districts = observation.get('districts', [])
    exposed = sum(item['exposed'] for item in districts)
    smoke = sum(bool(item['smoke_present']) for item in districts)
    resources = ', '.join(f"{item['id']}: {item['state']}" for item in observation.get('resources', [])
                          if item['state'] != 'available') or 'all available'
    return (f"{len(observation.get('reports', []))} received report(s); {smoke} district(s) with smoke; "
            f'{exposed} people marked exposed by the synthetic smoke-dose rule. Resources: {resources}.')


def action_title(action):
    active = [f'{key.replace("_", " ")}: {len(value)}' for key, value in action.items() if value]
    return ' · '.join(active) or 'Wait — all action lists empty'


def render_event(event):
    kind = event['kind']
    labels = {'timeout': 'Transport', 'invalid': 'Protocol error',
              'check': 'Draft check', 'submit': 'Submit action'}
    title = {'timeout': 'Response timed out — no action committed',
             'invalid': 'Invalid response — simulation time advanced',
             'check': 'Validate proposed action — no simulation time advanced'}.get(kind)
    if kind == 'submit':
        title = action_title(event['action'])
    minute = event['minute']
    clock = f'{minute} min'
    if 'observation_after' in event:
        clock += f" → {event['observation_after']['minute']} min"
    meta = (f"Request {event['sequence']} · {event['phase']} #{event['request']} · "
            f"decision index {event['decision']} · {clock} · {event['seconds']:.3f}s provider wait")
    tone = 'error' if kind == 'invalid' else 'timeout' if kind == 'timeout' else ''
    body = []
    if 'proposal' in event:
        body.append(f'<p><strong>Logged explanation</strong> (not scored): '
                    f"{escape(event['proposal'].get('explanation', ''))}</p>")
        body.append(json_panel('Proposed tool arguments', event['proposal']))
    if kind == 'invalid':
        body.append('<p class="trace-note">The adapter recorded '
                    '<code>missing_or_invalid_named_action</code>. Raw malformed arguments and the '
                    'specific parser error were not saved. The marker below is the engine action, '
                    'not a reconstruction of the model response.</p>')
    if kind == 'timeout':
        body.append('<p class="trace-note">No response content, token usage or decision was recorded '
                    'for this attempt. This is a transport interruption, not a terminal reward.</p>')
    if kind == 'check':
        body.append(json_panel('Contract-check result (not a forecast)', event['action_check']))
    if 'observation_after' in event:
        body.append(json_panel('Committed engine action', event['action']))
        body.append(f'<p class="trace-output">{escape(observation_summary(event["observation_after"]))}</p>')
        result = {key: event[key] for key in ('action_valid', 'objective_reward', 'terminated', 'truncated')}
        body.append(json_panel('Step result', result))
        body.append(json_panel('Public observation excerpt after this decision', event['observation_after']))
    if 'usage' in event:
        body.append(json_panel('Recorded token usage', event['usage']))
    opened = ' open' if event['sequence'] == 1 or kind in ('timeout', 'invalid') else ''
    return (f'<li class="trace-event {tone}"><details{opened}><summary>'
            f'<span class="trace-badge">{labels[kind]}</span><strong>{escape(title)}</strong>'
            f'<span class="trace-meta">{escape(meta)}</span></summary>'
            f'<div class="trace-body">{"".join(body)}</div></details></li>')


def render_case(case):
    events = case['events']
    invalid = sum(event['kind'] == 'invalid' for event in events)
    checks = sum(event['kind'] == 'check' for event in events)
    final = next(event['observation_after'] for event in reversed(events) if 'observation_after' in event)
    score = f"{case['score']:.2f} / 1.00" if case['scored'] else 'Not scored'
    status = 'Truncated · invalid-action abandonment' if case['scored'] else 'Partial · transport timeout'
    sidebar = (
        f'<aside class="trace-sidebar"><h3>{escape(case["family"].capitalize())}</h3>'
        f'<code>{escape(case["task_id"])}</code><p class="trace-score">{score}</p>'
        f'<p class="trace-help">{status}</p><dl>'
        f'<dt>Seed</dt><dd>{case["seed"]}</dd><dt>Model time</dt><dd>0–{final["minute"]} min</dd>'
        f'<dt>Committed decisions</dt><dd>{case["decisions"]}</dd>'
        f'<dt>HTTP attempts</dt><dd>{len(events)}</dd><dt>Invalid actions</dt><dd>{invalid}</dd>'
        f'<dt>Draft checks</dt><dd>{checks}</dd><dt>Reward</dt><dd>Population safety 80%; other objectives 20%</dd>'
        '</dl><p class="trace-help">Synthetic development case. Not an expert demonstration.</p></aside>'
    )
    start = ('<li class="trace-event"><details><summary><strong>Session start · 0 model min</strong>'
             '<span class="trace-meta">Public inputs available before the first request</span></summary>'
             f'<div class="trace-body"><p>{escape(observation_summary(case["initial_observation"]))}</p>'
             f'{json_panel("Initial public observation excerpt", case["initial_observation"])}</div></details></li>')
    rendered = [start]
    previous_phase = None
    for event in events:
        if previous_phase is not None and event['phase'] != previous_phase:
            rendered.append('<li class="trace-event"><div class="trace-boundary"><strong>Manual continuation</strong>'
                            '<p>The saved three-decision prefix was replayed exactly. Replay made no model calls '
                            'and is not counted again here. The timed-out request did not advance model time.</p></div></li>')
        rendered.append(render_event(event))
        previous_phase = event['phase']
    exposed = sum(item['exposed'] for item in final.get('districts', []))
    ending = ('Three consecutive invalid actions triggered truncation. The zero is abandonment credit, '
              'not evidence of physical harm.' if case['scored'] else
              'The final draft check passed, but the next response timed out. The case has no terminal score. '
              f'At the last committed observation, {exposed} people were marked exposed by the synthetic '
              'smoke-dose rule; this is not a real-world injury estimate.')
    rendered.append(f'<li class="trace-event"><div class="trace-boundary"><strong>Recording ends</strong>'
                    f'<p>{escape(ending)}</p><p>{escape(observation_summary(final))}</p></div></li>')
    return (f'<article id="trace-{escape(case["task_id"])}" class="trace-run">'
            f'<div class="trace-layout">{sidebar}<ol class="trace-timeline">{"".join(rendered)}</ol></div>'
            f'{json_panel("Trace provenance · source files and SHA-256", case["sources"])}</article>')


def render_section(export, export_filename):
    navigation = ''.join(f'<a href="#trace-{escape(case["task_id"])}">{escape(case["family"].capitalize())}</a>'
                         for case in export['cases'])
    return (f'{START}\n<section id="saved-trajectories"><style>{STYLE}</style>'
            '<h2>Saved run traces</h2><p>Follow the actual model requests and simulator responses. '
            'Expand a card for tool arguments, recorded explanations and public state. '
            '<strong>Model minutes</strong> are simulated time; <strong>provider wait</strong> is wall-clock latency. '
            'Draft checks and timeouts do not advance the simulator.</p>'
            f'<nav class="trace-nav" aria-label="Saved trajectories">{navigation}'
            f'<a class="trace-download" href="{escape(export_filename)}" download>Download trace excerpts · JSON</a></nav>'
            '<p class="trace-help">Read-only, self-contained HTML; no model calls or external assets. '
            f'{escape(export["scope"])}</p>'
            '<p class="trace-help">Regenerate this view from the saved excerpts: '
            '<code>python eval/render_cloudrift_ops_traces.py --from-export</code>.</p>'
            f'{"".join(render_case(case) for case in export["cases"])}</section>\n{END}')


def update_report(report, section):
    if START in report:
        before, remaining = report.split(START, 1)
        _, after = remaining.split(END, 1)
        return before + section + after
    anchor = '<h2>Audit and budget</h2>'
    if anchor not in report:
        raise ValueError('Report insertion point not found')
    return report.replace(anchor, section + '\n' + anchor, 1)


def main():
    parser = argparse.ArgumentParser(description='Render recorded CloudRift traces without inference or simulation')
    parser.add_argument('--runtime-root', type=Path, default=Path('.runtime'))
    parser.add_argument('--validation', type=Path, default=Path('data/ops_model_v1/cloudrift_validation.json'))
    parser.add_argument('--report', type=Path, default=Path('results/cloudrift-ops-20261026.html'))
    parser.add_argument('--export', type=Path, default=Path('results/cloudrift-ops-20261026-trajectories.json'))
    parser.add_argument('--from-export', action='store_true', help='Regenerate HTML using the committed public excerpts')
    arguments = parser.parse_args()
    if arguments.report.parent != arguments.export.parent:
        parser.error('Report and excerpt export must share a directory')
    if arguments.from_export:
        export = json.loads(arguments.export.read_text())
    else:
        export = build_export(arguments.runtime_root, json.loads(arguments.validation.read_text()))
        arguments.export.write_text(json.dumps(export, indent=2, ensure_ascii=False) + '\n')
    section = render_section(export, arguments.export.name)
    arguments.report.write_text(update_report(arguments.report.read_text(), section))
    print(f'Rendered {len(export["cases"])} saved trajectories in {arguments.report}; no inference calls')


if __name__ == '__main__':
    main()
