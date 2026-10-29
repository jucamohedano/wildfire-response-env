"""Render a public, standalone report from a bounded Qwen incident-day run."""
import argparse
from collections import Counter
import html
import json
from pathlib import Path
import statistics

CASES = [f'incident-day-realmap-v2-{i:03}' for i in range(6)]
PINS = ('manifest_sha256', 'engine_implementation_sha256', 'lifecycle_sha256', 'inputs_sha256', 'network_sha256')

def read_json(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}

def esc(value):
    return html.escape(str(value), quote=True)

def fmt(value, digits=3):
    if value is None or isinstance(value, bool):
        return '—' if value is None else ('yes' if value else 'no')
    if isinstance(value, float):
        return f'{value:.{digits}f}'
    return str(value)

def verified(row):
    v = row.get('verification') or {}
    o = row.get('outcomes') or v.get('outcomes') or {}
    replay = row.get('request_replay') or {}
    return (replay.get('passed') is True and replay.get('score_eligible') is True
            and row.get('completed') is True and row.get('minute') == 1440
            and not row.get('truncated') and o.get('score_eligible') is True
            and not o.get('boundary_censored') and not o.get('invalid_abandonment')
            and all(v.get(k) is True for k in ('passed', 'exact_replay', 'independent_reward_agreement')))

def pin_value(obj, key):
    for container in (obj, obj.get('pins', {}), obj.get('source_pins', {}), obj.get('runtime', {})):
        if isinstance(container, dict) and container.get(key):
            return container[key]
    return None

def matching(model, baselines):
    return all(pin_value(model, key) is not None and pin_value(model, key) == pin_value(baselines, key) for key in PINS)

def requests(path):
    records, malformed = [], 0
    try:
        with path.open(encoding='utf-8') as stream:
            for line in stream:
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        records.append(obj)
                    else:
                        malformed += 1
                except ValueError:
                    malformed += 1
    except OSError:
        return records, None
    return records, malformed

def recorded_error(rec):
    if rec.get('executed') is False:
        return None
    result = rec.get('result')
    info = result.get('info') if isinstance(result, dict) else None
    return rec.get('error') or (result.get('error') if isinstance(result, dict) else None) or (info.get('error') if isinstance(info, dict) else None)


def error_breakdown(records):
    counts = Counter()
    for rec in records:
        if not recorded_error(rec):
            continue
        if rec.get('error'):
            counts['malformed_response'] += 1
        elif isinstance(rec.get('result'),dict) and rec['result'].get('committed') is False:
            counts['tool_contract_error'] += 1
        elif rec.get('tool') == 'check_action':
            counts['draft_rejection'] += 1
        elif rec.get('tool') == 'commit_orders':
            counts['invalid_commit'] += 1
        else:
            counts['other_tool_error'] += 1
    return dict(counts)


def has_stale_completed_evacuation(record):
    """Identify conflicting public views for the same population group."""
    result = record.get('result')
    if record.get('tool') != 'get_situation' or not isinstance(result, dict):
        return False
    completed = {order.get('group_id') for order in result.get('protection_orders', [])
                 if isinstance(order, dict) and order.get('group_id') is not None
                 and order.get('state') == 'completed' and order.get('action') == 'evacuate'
                 and order.get('received_at') is not None}
    return any(group.get('id') in completed and group.get('status') == 'unwarned'
               and group.get('observed_at') is None
               for group in result.get('districts', []) if isinstance(group, dict))


def replay_outcome_summary(diagnostics):
    """Describe evaluator outcomes at the recorded stopping time, not actor knowledge."""
    status = diagnostics.get('population_by_status') or {}
    parts = []
    if isinstance(status, dict) and isinstance(status.get('arrived'), int):
        parts.append(f'{status["arrived"]} simulated people had arrived at a destination')
    work = diagnostics.get('resources_with_applied_work')
    if isinstance(work, int) and not isinstance(work, bool):
        parts.append(f'{work} resource(s) had performed suppression work')
    fires = diagnostics.get('active_fire_cells')
    if isinstance(fires, int) and not isinstance(fires, bool):
        parts.append(f'{fires} fire cells remained active')
    return '; '.join(parts)


def public_feedback(rec):
    """Compact allowlist of what the policy actually received, not evaluator state."""
    result=rec.get('result')
    if not isinstance(result,dict):
        return {'records':len(result)} if rec.get('tool')=='get_reports' and isinstance(result,list) else {}
    name=rec.get('tool')
    if name=='get_situation':
        fields=('id','count','status','observed_at','received_at')
        groups=[{k:g.get(k) for k in fields} for g in result.get('districts',[]) if isinstance(g,dict)]
        orders=[{k:o.get(k) for k in ('group_id','state','observed_at','received_at')} for o in result.get('protection_orders',[]) if isinstance(o,dict)]
        offers=[{k:o.get(k) for k in ('id','resource_id','expires_at')} for o in result.get('offers',[]) if isinstance(o,dict)]
        destinations=[{k:d.get(k) for k in ('id','name','status')} for d in result.get('destinations',[]) if isinstance(d,dict)]
        return {'groups':groups,'destinations':destinations,'orders':orders,'offer_count':len(offers),'recent_offers':offers[-6:]}
    if name in ('route_options','evacuation_options'):
        return {'status':result.get('status'),'option_ids':[o.get('id') for o in result.get('options',[]) if isinstance(o,dict)]}
    if name=='check_action':return {'valid':result.get('valid')}
    if name in ('advance_time','commit_orders','request_status'):
        obs=result.get('observation') or {};info=result.get('info') or {}
        return {'next_minute':obs.get('minute'),'wake_reason':info.get('wake_reason')}
    return {}


def public_timeline(records, baseline=False):
    # Only selected public call fields and validator messages; never raw replies/results.
    groups=[]
    for rec in records[:100]:
        if baseline:
            name,args=(rec.get('tool'),rec.get('arguments',{})) if rec.get('tool') else ('action',rec.get('action',{}))
        else:
            name,args=rec.get('tool'),rec.get('arguments',{})
        if rec.get('executed') is False:
            name,args='harness stop',{'reason':rec.get('harness_stop')}
        elif not isinstance(name,str):
            if rec.get('error'):name,args='malformed response',{}
            else:continue
        text=json.dumps(args,ensure_ascii=False,sort_keys=True,default=str)
        error=str(recorded_error(rec) or '')
        feedback=json.dumps(public_feedback(rec),ensure_ascii=False,sort_keys=True)
        key=(rec.get('minute'),name,text,error,feedback)
        if groups and groups[-1][0]==key:groups[-1][1]+=1
        else:groups.append([key,1])
    rows=[]
    for (minute,name,text,error,feedback),count in groups:
        text=text[:300]+('…' if len(text)>300 else '')
        repeated=f' <strong>×{count} repeated</strong>' if count>1 else ''
        detail='<br><small>Rejected: '+esc(error[:240])+'</small>' if error else ''
        if feedback!='{}':detail+='<br><small>Received: '+esc(feedback[:600])+('…' if len(feedback)>600 else '')+'</small>'
        rows.append(f'<li><b>Minute {esc(fmt(minute))}</b> · {esc(name)}{repeated} <code>{esc(text)}</code>{detail}</li>')
    omitted=max(0,len(records)-100)
    return '<details><summary>Public action timeline (up to100 records; identical consecutive calls grouped)</summary><ol>'+''.join(rows)+'</ol>'+ (f'<p>{omitted} further entries omitted.</p>' if omitted else '')+'</details>'


def make_report(run_dir, baseline_path):
    summary_path = run_dir / 'modelsummary.json'
    if not summary_path.exists():
        summary_path = run_dir / 'summary.json'
    model = read_json(summary_path)
    baseline = read_json(baseline_path)
    pilot = any(name in (run_dir.name + ' ' + str(model.get('run_id', '')) + ' ' + str(model.get('run', ''))).lower() for name in ('run-v1','run-v2','run-v3','run-v4'))
    model_rows = {r.get('case'): r for r in model.get('cases', []) if isinstance(r, dict) and isinstance(r.get('case'), str)}
    baseline_rows = {(r.get('case'), r.get('policy')): r for r in baseline.get('cases', []) if isinstance(r, dict)}
    pins_match = matching(model, baseline)
    completed = sum(verified(r) for r in model_rows.values()) if not pilot else 0
    minutes = [r.get('minute') for r in model_rows.values()
               if isinstance(r.get('minute'), int) and not isinstance(r.get('minute'), bool)]
    overview = (f'{len(model_rows)} of {len(CASES)} bounded cases have stopped; '
                f'{completed} reached a verified, score-eligible end of day. '
                f'The furthest recorded stopping point is minute {max(minutes) if minutes else "unknown"} of 1440. '
                'Unfinished runs have no full-day score and cannot be ranked against completed baselines.')
    sections = []
    for case in CASES:
        row = model_rows.get(case)
        records, bad = requests(run_dir / case / 'requests.jsonl')
        progress = read_json(run_dir / case / 'progress.json')
        v = row.get('verification') or {} if row else {}
        outcome = (row.get('outcomes') or v.get('outcomes') or {}) if row else {}
        eligible = bool(row and verified(row) and not pilot)
        if row:
            status = ('Unscored: excluded harness pilot' if pilot else ('Verified and eligible' if eligible else 'Unscored: incomplete, censored, abandoned, or verification/eligibility not established'))
        else:
            status = ('Running; partial request records available' if records else 'Queued; not yet attempted') if model.get('status') == 'running' else 'Not present in terminal model summary'
        counts = Counter(r['tool'] for r in records if isinstance(r.get('tool'), str) and r.get('executed') is not False)
        latencies = [r['seconds'] for r in records if isinstance(r.get('seconds'), (int, float)) and not isinstance(r['seconds'], bool) and r['seconds'] >= 0]
        inputs = [r['input_tokens'] for r in records if isinstance(r.get('input_tokens'), int) and not isinstance(r.get('input_tokens'), bool)]
        outputs = [r['output_tokens'] for r in records if isinstance(r.get('output_tokens'), int) and not isinstance(r.get('output_tokens'), bool)]
        breakdown = error_breakdown(records)
        errors = sum(breakdown.values()) if records else None
        metric = lambda k: fmt(outcome.get(k)) if eligible else 'Unscored'
        peak = row.get('gpu_peak_bytes') if row else None
        details = f'<p>Minute: {esc(fmt(row.get("minute") if row else progress.get("minute")))} / 1440 · Calls: {esc(fmt(row.get("calls") if row else len(records)))} · Errors: {esc(fmt(errors))} · Elapsed: {esc(fmt(row.get("seconds") if row else progress.get("elapsed_seconds")))} s · GPU peak allocated: {esc(fmt(peak / (1024**3) if isinstance(peak, (int, float)) else None))} GiB</p>'
        details += f'<p>Eligibility: {esc(fmt(outcome.get("score_eligible")))} · Boundary censored: {esc(fmt(outcome.get("boundary_censored")))} · Invalid abandonment: {esc(fmt(outcome.get("invalid_abandonment")))} · Replay: {esc(fmt(v.get("exact_replay")))} · Independent reward agreement: {esc(fmt(v.get("independent_reward_agreement")))} · Stop reason: {esc(fmt(row.get("reason") if row else None))}</p>'
        details += f'<p>Reward: {esc(metric("graded_score"))} · Protected population fraction: {esc(metric("protected_population_fraction"))} · Additional burned area: {esc(metric("additional_burned_hectares"))} ha</p>'
        details += f'<p>Tool calls: {esc(", ".join(f"{k}: {n}" for k, n in sorted(counts.items())) or "none recorded")} · Input tokens total: {sum(inputs) if inputs else "—"} · Output tokens total: {sum(outputs) if outputs else "—"} · Generation latency mean / median / max: {esc(fmt(statistics.mean(latencies) if latencies else None))} / {esc(fmt(statistics.median(latencies) if latencies else None))} / {esc(fmt(max(latencies) if latencies else None))} s · Malformed request lines: {esc(fmt(bad))}</p>'
        details += '<p>Recorded error categories: ' + esc(json.dumps(breakdown, sort_keys=True)) + '. Tool-contract errors and draft rejections consume no time; engine-invalid commits do. Null error fields are not failures.</p>'
        replay = row.get('request_replay') or {} if row else {}
        if replay:
            details += '<p>Independent recorded-request replay: ' + esc(fmt(replay.get('passed'))) + '; final trace match: ' + esc(fmt(replay.get('trajectory_match'))) + '. This verifies the recorded prefix, not full-horizon eligibility.</p>'
            if replay.get('passed') is True:
                diagnostics = replay.get('diagnostics') or {}
                summary = replay_outcome_summary(diagnostics)
                if summary:
                    details += '<p>At the stopping time, evaluator records show: ' + esc(summary) + '. These outcomes do not establish a full-day result or what the model knew.</p>'
                selected = {key: diagnostics.get(key) for key in ('active_fire_cells','additional_burned_hectares','new_exposed_population','unresolved_evacuations','population_by_status','resource_by_kind_and_state','suppression_work_events','resources_with_applied_work','received_reports','mission_operations_submitted')}
                qualifier = 'Evaluator-only effects at day end; the model may not have received every update: ' if eligible else 'Evaluator-only diagnostics at the stopping clock; the model may not have received these updates yet. Not comparable with full-day reference outcomes: '
                details += '<p class="note">' + qualifier + esc(json.dumps(selected, sort_keys=True)) + '</p>'
        if row:
            details += '<p>History trimming operations: ' + esc(fmt(row.get('pruned_history_pairs'))) + '; the latest call/result pair was retained.</p>'
        if any(recorded_error(record) == "'resource_id'" for record in records):
            details += '<p class="note">A rejected cross-kind approach produced a terse internal-field error. The environment feedback issue is tracked in <a href="https://github.com/jucamohedano/wildfire-response-env/issues/57">#57</a>.</p>'
        if any(has_stale_completed_evacuation(record) for record in records):
            details += '<p class="note">A delivered evacuation-completion report coexisted with an untimestamped unwarned population view. See environment feedback issue <a href="https://github.com/jucamohedano/wildfire-response-env/issues/59">#59</a>; this does not establish why the model repeated its order.</p>'
        details += public_timeline(records)
        for policy in ('passive', 'dispatcher'):
            b = baseline_rows.get((case, policy))
            if not b:
                details += f'<p><strong>{policy.title()}:</strong> baseline missing.</p>'; continue
            bo = b.get('outcomes') or {}
            bv = b.get('verification') or {}
            baseline_ok = (b.get('status') == 'complete' and b.get('minute') == 1440 and bo.get('score_eligible') is True and not bo.get('boundary_censored') and not bo.get('invalid_abandonment') and all(bv.get(k) is True for k in ('passed','exact_replay','independent_reward_agreement')))
            if not pins_match:
                comparison = 'Comparison unavailable: model source pins absent or different.'
            elif not eligible or not baseline_ok:
                comparison = 'Comparison unavailable: model or baseline is unscored.'
            else:
                comparison = (f'Reward {fmt(bo.get("graded_score"))}; protected fraction {fmt(bo.get("protected_population_fraction"))}; additional burned {fmt(bo.get("additional_burned_hectares"))} ha.')
            details += f'<p><strong>{policy.title()}:</strong> {esc(b.get("status", "unknown"))}, minute {esc(fmt(b.get("minute")))}, commands {esc(fmt(b.get("commands")))}, errors {esc(fmt(b.get("errors")))}, public route/evacuation queries {esc(fmt(b.get("public_queries")))}. {esc(comparison)} {esc(b.get("reason", ""))}</p>'
            details += public_timeline(b.get('timeline') or [], baseline=True)
        sections.append(f'<section><h2>{esc(case)}</h2><p class="status">{esc(status)}</p>{details}</section>')
    missing_pins = [k for k in PINS if not pin_value(model,k)]
    pin_note = 'All required pins match.' if pins_match else 'Matching not established. Missing model pins: ' + (', '.join(missing_pins) or 'none; at least one pin differs') + '.'
    page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Qwen3-8B incident-day evaluation</title><style>body{{font:16px/1.55 system-ui,sans-serif;color:#172b31;background:#f2f5f3;margin:0}}main{{max-width:1100px;margin:auto;padding:2rem}}header,section{{background:white;padding:1.4rem 1.8rem;margin:1rem 0;border-radius:12px;box-shadow:0 2px 12px #18382b18}}h1,h2{{line-height:1.2}}h2{{font-size:1.2rem}}.status{{color:#285b46;font-weight:700}}code{{overflow-wrap:anywhere;font-size:.88em}}details{{border-top:1px solid #d7e2dc;padding:.6rem 0}}summary{{cursor:pointer;font-weight:600}}li{{margin:.35rem 0}}.note{{background:#fff3d5;padding:1rem;border-radius:8px}}</style></head><body><main><header><h1>Qwen3-8B · incident-day evaluation</h1><p>Verified, eligible model cases: <strong>{completed} / 6</strong> · Run status: {esc(model.get('status','summary missing or unreadable'))}</p><p>Qwen/Qwen3-8B revision b968826d9c46dd6066d109eabc6255188de91218 · FP16 Tesla V100 32 GB · thinking disabled · greedy decoding · context {esc(fmt(model.get("context_tokens")))} tokens · maximum {esc(fmt(model.get("max_new_tokens")))} new tokens.</p><p>Simulation time is virtual: read-only inspection and draft checks do not advance it. Orders/status requests cost one minute; explicit waits advance up to 60 minutes and may wake early.</p><p>Budget scope: {esc(model.get('budget_scope','not recorded'))}</p><p>Main run bounds: {esc(fmt(model.get("case_wall_seconds")))} seconds per case, {esc(fmt(model.get("global_wall_seconds")))} seconds overall and {esc(fmt(model.get("max_model_calls")))} calls per case. A limit or interrupted run is not a completed result. GPU peak is allocated memory as reported by the runner, and may be cumulative across cases.</p><p>Four setup pilots are preserved and excluded. The current run uses the reviewed native-tool protocol, context handling and deadline guards.</p><p class="note">These six cases are development exercises, not independent real incidents. Field and reward calibration remain open; this is not a safety certification. Protected fraction and burned area are simulated outcomes.</p><p>Baseline matching: {esc(pin_note)} Comparison requires identical case ID and all five manifest, engine, lifecycle, input and network pins.</p></header>{''.join(sections)}</main></body></html>'''
    note='<p class="note">Protocol audit: run-v1 used ordinary-user tool feedback; run-v2 context trimming discarded the latest tool response. Both pilots are excluded from model conclusions. Run-v3 then exposed a single tool response exceeding the12k context limit. Run-v4 was superseded before completion by the reviewed strict-parser and deadline guards. Main run-v5 uses a hardware-tested32k context with strict single-call parsing and pre-commit deadline checks and preserves native tool roles and the latest tool-call/result pair. Recorded error totals are recomputed from actual draft and commit feedback, excluding null error fields.</p>'
    reproduction = '''<details><summary>Reproduce this configuration</summary>
<p>Use the evaluation runner at commit <code>0217aef</code> and the private Wildfire Response Spain dataset at revision <code>d7e1e1c82eb249e35315bd19d1e0fea730fc0da7</code>. This run used Python, PyTorch 2.5.1+cu121, Transformers 4.51.3 and Accelerate 1.6.0 on a Tesla V100 with 32 GiB. The model weights download from Hugging Face; the prepared dataset requires access. Start from a compatible project checkout with its simulator dependencies installed.</p>
<pre><code>PYTHONPATH=.:envs/wildfire_response/core/src python -m eval.run_qwen3_day \\
  --data /path/to/wildfire-response-spain --output /path/to/new-output-directory \\
  --context 32768 --case-seconds 900 --seconds 7200 --max-calls 1200</code></pre>
<p>Output must be a new directory. The wall-clock limits depend on hardware and load, so a repeat need not stop at the same model call. Raw logs and checkpoints may contain private evaluator state; keep them private.</p></details>'''
    page=page.replace('</header>','<p class="note">'+esc(overview)+'</p>'+note+reproduction+'</header>',1)
    return page

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--baselines', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(make_report(args.run_dir, args.baselines), encoding='utf-8')

if __name__ == '__main__':
    main()
