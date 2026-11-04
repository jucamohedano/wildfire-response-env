"""Render a public, standalone report from a bounded Qwen incident-day run."""
import argparse
from collections import Counter
import html
import json
from pathlib import Path
import statistics
try:
    from eval.qwen3_report_charts import charts
except ModuleNotFoundError:
    from qwen3_report_charts import charts

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


def verified_abandonment_penalty(row):
    """Return the simulator's verified penalty, separate from full-day scores."""
    verification = row.get('verification') or {}
    outcome = row.get('outcomes') or verification.get('outcomes') or {}
    replay = row.get('request_replay') or {}
    if (row.get('truncated') is True and outcome.get('invalid_abandonment') is True
            and outcome.get('score_eligible') is True and replay.get('passed') is True
            and replay.get('trajectory_match') is True
            and all(verification.get(k) is True for k in ('passed', 'exact_replay', 'independent_reward_agreement'))):
        return outcome.get('graded_score')
    return None

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


def rejected_reused_authorizations(records):
    """Count rejections of offer IDs accepted by an earlier committed action."""
    accepted = set()
    repeated = 0
    for record in records:
        if record.get('tool') != 'commit_orders' or record.get('executed') is False:
            continue
        result = record.get('result')
        if not isinstance(result, dict):
            continue
        info = result.get('info')
        arguments = record.get('arguments') or {}
        action = arguments.get('action') if isinstance(arguments, dict) else None
        if not isinstance(action, dict):
            continue
        offers = action.get('authorizations') or []
        if not isinstance(offers, list):
            continue
        offer_ids = {offer for offer in offers if isinstance(offer, str)}
        if isinstance(info, dict) and 'error' in info and info['error'] is None:
            accepted.update(offer_ids)
        elif recorded_error(record) == 'Authorize a live offered allocation' and offer_ids & accepted:
            repeated += 1
    return repeated


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
    replayed = sum((r.get('request_replay') or {}).get('passed') is True for r in model_rows.values())
    trimmed_cases = sum((r.get('pruned_history_pairs') or 0) > 0 for r in model_rows.values())
    minutes = [r.get('minute') for r in model_rows.values()
               if isinstance(r.get('minute'), int) and not isinstance(r.get('minute'), bool)]
    overview = (f'{len(model_rows)} of {len(CASES)} bounded cases have stopped; '
                f'{completed} reached a verified, score-eligible end of day. '
                f'The furthest recorded stopping point is minute {max(minutes) if minutes else "unknown"} of 1440. '
                'Unfinished runs have no full-day score and cannot be ranked against completed baselines.')
    sections = []
    chart_rows = []
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
        executed = sum(r.get('executed') is not False for r in records)
        reason = row.get('reason') if row else None
        stop_note = {'generation_deadline': 'time limit', 'episode_truncated': 'truncated'}.get(reason, '')
        chart_rows.append({'label': 'Case ' + case[-3:], 'minute': row.get('minute') if row else None,
                           'errors': errors, 'executed': executed, 'input_peak': max(inputs) if inputs else None,
                           'trimmed': row.get('pruned_history_pairs') if row else None, 'stop_note': stop_note})
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
        penalty = verified_abandonment_penalty(row) if row and not pilot else None
        if penalty is not None:
            details += '<p class="note">The simulator assigned a verified invalid-abandonment penalty of ' + esc(fmt(penalty)) + ' when the episode truncated. This is not a completed-day score.</p>'
        if row:
            details += '<p>History trimming operations: ' + esc(fmt(row.get('pruned_history_pairs'))) + '; the latest call/result pair was retained.</p>'
        if any(recorded_error(record) == "'resource_id'" for record in records):
            details += '<p class="note">A rejected cross-kind approach produced a terse internal-field error. The environment feedback issue is tracked in <a href="https://github.com/jucamohedano/wildfire-response-env/issues/57">#57</a>.</p>'
        if any(has_stale_completed_evacuation(record) for record in records):
            details += '<p class="note">A delivered evacuation-completion report coexisted with an untimestamped unwarned population view. See environment feedback issue <a href="https://github.com/jucamohedano/wildfire-response-env/issues/59">#59</a>; this does not establish why the model repeated its order.</p>'
        reused = rejected_reused_authorizations(records)
        if reused:
            details += f'<p class="note">{reused} committed order(s) attempted to authorize an offer already accepted in an earlier order and were rejected. See the public action timeline for the recorded sequence.</p>'
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
        sections.append(f'<details class="case"><summary>{esc(case)} · {esc(status)}</summary><section>{details}</section></details>')
    missing_pins = [k for k in PINS if not pin_value(model,k)]
    pin_note = 'All required pins match.' if pins_match else 'Matching not established. Missing model pins: ' + (', '.join(missing_pins) or 'none; at least one pin differs') + '.'
    detail = f"""<details class="method"><summary>Method, limits and reproduction</summary>
    <p>Qwen/Qwen3-8B revision b968826d9c46dd6066d109eabc6255188de91218; FP16 Tesla V100 32 GB; thinking disabled; greedy decoding; maximum {esc(fmt(model.get("max_new_tokens")))} output tokens. The six cases share one synthetic development landscape, not six independent real incidents.</p>
    <p>Read-only tools and draft checks do not advance simulated time. Orders and status requests cost one minute; waiting can advance up to 60 minutes and wake earlier. Each case was bounded to {esc(fmt(model.get("case_wall_seconds")))} seconds and {esc(fmt(model.get("max_model_calls")))} calls. Incomplete cases do not have full-day scores. One truncated case received a separately verified invalid-abandonment penalty.</p>
    <p>Four setup pilots are excluded: v1 used the wrong feedback roles; v2 discarded the latest tool result during trimming; v3 could not fit a single long tool response in its 12k input limit; v4 stopped before the reviewed guards were added. The plotted main run uses a 32k input threshold, native tool roles, and preserves the latest call/result pair during history trimming. Input charts count prompts, not output tokens.</p>
    <p>Baseline matching: {esc(pin_note)} Scores are comparable only for eligible completed episodes with matching pins. All six recorded request prefixes replayed exactly. Reward and field calibration remain open.</p>
    <p>Use evaluation runner commit <code>0217aef</code> with private dataset revision <code>d7e1e1c82eb249e35315bd19d1e0fea730fc0da7</code>, PyTorch 2.5.1+cu121, Transformers 4.51.3 and Accelerate 1.6.0. The output directory must not exist. Runtime depends on hardware and load; keep raw logs and checkpoints private.</p>
    <pre><code>PYTHONPATH=.:envs/wildfire_response/core/src python -m eval.run_qwen3_day \\
  --data /path/to/wildfire-response-spain --output /path/to/new-output-directory \\
  --context 32768 --case-seconds 900 --seconds 7200 --max-calls 1200</code></pre></details>"""
    context_stops = sum(str(r.get('reason', '')).startswith('context_capacity') for r in model_rows.values())
    context_note = f"The recorded run trimmed history in {trimmed_cases} case(s); the first trim replaced the initial situation, and later trims removed older message pairs. {context_stops} stopped because of a context-capacity error. An API may offer a larger window or server-side compaction, but those depend on the provider and configuration and can lose details. <a href=\"https://platform.claude.com/docs/en/build-with-claude/compaction-threshold\">Example: Claude compaction</a>."
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Qwen3-8B incident-day evaluation</title>
    <style>body{{font:16px/1.5 system-ui,sans-serif;color:#172b31;background:#f2f5f3;margin:0}}main{{max-width:1100px;margin:auto;padding:1.5rem}}header,.chart,.method,.case{{background:#fff;padding:1.3rem 1.6rem;margin:1rem 0;border-radius:12px;box-shadow:0 2px 12px #18382b18}}h1,h2{{line-height:1.2;margin:.1rem 0 .4rem}}h2{{font-size:1.15rem}}p{{margin:.5rem 0}}.lede{{font-size:1.1rem}}.note{{background:#fff3d5;padding:.8rem;border-radius:8px}}.stats{{display:flex;gap:1rem;flex-wrap:wrap}}.stat{{background:#eff5f1;border-radius:8px;padding:.6rem 1rem}}.stat strong{{display:block;font-size:1.4rem}}.scroll-hint{{display:none}}.chart{{overflow-x:auto}}.chart svg{{display:block;width:100%;min-width:640px;height:auto}}.chart-label,.chart-value,.axis{{font:13px system-ui,sans-serif;fill:#263b3d}}.axis{{font-size:11px;fill:#6c7e7b}}summary{{cursor:pointer;font-weight:700}}.case section{{padding:.8rem 0 0}}.case details{{border-top:1px solid #d7e2dc;padding:.6rem 0}}code{{overflow-wrap:anywhere;font-size:.88em}}pre{{white-space:pre-wrap}}li{{margin:.35rem 0}}a{{color:#316a58}}@media(max-width:600px){{.scroll-hint{{display:block;font-size:.85em;color:#586c69}}main{{padding:.6rem}}header,.chart,.case,.method{{padding:1rem}}}}</style></head><body><main>
    <header><h1>Qwen3-8B · Wildfire Ops evaluation</h1><p class="lede">Six simulated incident-day exercises · 32 GB Tesla V100 · no model training</p>
    <div class="stats"><div class="stat"><strong>{completed} / 6</strong> completed full days</div><div class="stat"><strong>{esc(max(minutes) if minutes else '—')} min</strong> furthest simulated progress</div><div class="stat"><strong>{replayed} / 6</strong> recorded prefixes replayed*</div></div>
    <p class="note">{esc(overview)} *Replay status is shown per case below. Truncated episodes may have a separate verified penalty.</p>
    <p>{context_note}</p></header>
    {charts(chart_rows, model.get('context_tokens'))}
    <section class="chart"><h2>What to take away</h2><p>Draft rejections can repeat without advancing simulation time; the charts show this separately from prompt size. A stronger or better-prompted model may behave differently. The <a href="https://huggingface.co/Qwen/Qwen3-8B">Qwen3 model card</a> recommends sampling for non-thinking mode; this run used greedy decoding.</p></section>
    <h2>Case details</h2><p>Expand a case for the tool timeline, stop reason and verification.</p>{''.join(sections)}{detail}</main></body></html>"""
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
