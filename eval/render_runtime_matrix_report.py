"""Render aggregate operation-wide evidence; never loads private trajectory files."""
import argparse
import html
import json
from pathlib import Path

POLICIES=('passive','dispatcher','conservative','stress')
OPERATIONS=(('get_situation','Read situation'),('get_reports','Read reports'),('route_options','Inspect approaches'),
    ('evacuation_options','Inspect destinations'),('check_action','Check a draft'),('requests','Request resources'),
    ('authorizations','Authorize allocation'),('releases','Release resources'),('shelter','Shelter'),
    ('evacuate','Evacuate / relocate'),('request_status','Ask another centre'),('advance_time','Advance time'))

CSS='''
.runtime-review{color:#26343b}.runtime-review h2{font-size:21px}.runtime-review h3{font-size:15px;margin:0 0 7px}
.runtime-review p{font-size:12px;color:#776d83;margin:6px 0 12px}.runtime-metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:18px 0}
.runtime-metrics>div{border-top:3px solid #66a994;background:#f3faf7;border-radius:6px;padding:12px}.runtime-metrics>div:nth-child(2){border-color:#8a76bf;background:#f7f3fc}.runtime-metrics>div:nth-child(3){border-color:#649cc3;background:#f2f8fc}.runtime-metrics>div:nth-child(4){border-color:#d4a657;background:#fff9ef}
.runtime-metrics b{display:block;font-size:25px;color:#467a69}.runtime-metrics small{font-size:10px;color:#83768b}.runtime-columns{display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-top:18px}
.runtime-grid{display:grid;grid-template-columns:minmax(100px,1.2fr) repeat(4,minmax(35px,1fr));gap:4px;font-size:10px;align-items:center;margin:12px 0}
.runtime-grid .label{color:#776780}.runtime-grid .head{text-align:center;color:#85778d;font-size:9px}.runtime-grid .cell{text-align:center;border-radius:4px;padding:5px 3px;background:#f1eff4;color:#8e8597}.runtime-grid .pass{background:#dcf1e7;color:#26765d}.runtime-grid .fail{background:#fce6e5;color:#ad514c}.runtime-grid .wait{background:#fff2d5;color:#a57b30}
.runtime-note{background:#f4f2f8;border-radius:6px;padding:10px 12px;font-size:11px!important}.runtime-durations{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:15px}.runtime-durations>div{padding:10px;border:1px solid #ded8e7;border-radius:6px}.runtime-durations b{display:block;color:#638aa1;font-size:18px}.runtime-durations span{display:block;font-size:11px;color:#73627f;margin:4px 0}.runtime-durations small{font-size:9px;color:#97869e}
.runtime-footer{border-top:1px solid #e6dfed;padding-top:10px;margin-top:16px;font-size:11px;color:#84708e}
@media(max-width:900px){.runtime-columns{grid-template-columns:1fr}.runtime-grid{grid-template-columns:minmax(100px,1.2fr) repeat(4,minmax(40px,1fr))}}
@media(max-width:600px){.runtime-metrics{grid-template-columns:1fr 1fr}.runtime-durations{grid-template-columns:1fr 1fr}.runtime-grid{font-size:9px;grid-template-columns:minmax(90px,1.2fr) repeat(4,minmax(31px,1fr))}.runtime-grid .head{font-size:8px}}
'''


def section(report, json_ref):
    cells=report['cells'];complete=[c for c in cells if c['status']=='complete']
    cases=sorted({(c['runtime_id'],c['case_id']) for c in report['expected_cells']})
    by_id={(c['runtime_id'],c['case_id'],c['policy']):c for c in cells}
    check_count=sum(len(c.get('resume_checks',[])) for c in complete)
    ticks=sum(c.get('movement_evidence',{}).get('ticks_observed',0) for c in complete)
    contradictions=sum(c.get('runtime_contradictions',0) for c in cells)
    grid='<div class="runtime-grid"><span></span>'+''.join('<span class="head">'+p.title()+'</span>' for p in POLICIES)
    for runtime,case in cases:
        label=('Map' if runtime=='es-ma-2021' else 'Fixture')+' · '+case.rsplit('-',1)[-1]
        grid+='<span class="label" title="'+html.escape(case)+'">'+label+'</span>'
        for policy in POLICIES:
            c=by_id.get((runtime,case,policy),{})
            status=c.get('status','not_run');klass='pass' if status=='complete' else 'fail' if status=='failed' else 'wait'
            mark='✓' if status=='complete' else '×' if status=='failed' else '—'
            grid+='<span class="cell '+klass+'" title="'+html.escape(case+' / '+policy+': '+status)+'">'+mark+'</span>'
    grid+='</div>'
    operations='<div class="runtime-grid"><span></span>'+''.join('<span class="head">'+p.title()+'</span>' for p in POLICIES)
    for name,label in OPERATIONS:
        operations+='<span class="label">'+label+'</span>'
        for policy in POLICIES:
            count=sum(c.get('operation_coverage',{}).get(name,{}).get('observed',0)>0 for c in complete if c['policy']==policy)
            operations+='<span class="cell '+('pass' if count else '')+'">'+str(count)+'/12</span>'
    operations+='</div>'
    durations=[]
    for key,label in (('commit_orders','Commit orders'),('request_status','Status request'),('advance_time','Wait / early wake'),
                      ('resource_mobilizing','Mobilization'),('resource_on_scene','Setup'),('resource_refilling','Refill / refuel'),
                      ('resource_travel','Outbound movement'),('resource_returning','Return movement'),('status_response','Status reply')):
        values=[c.get('durations',{}).get(key) for c in complete if c.get('durations',{}).get(key)]
        if not values:continue
        low=min(v['min_minutes'] for v in values);high=max(v['max_minutes'] for v in values)
        total=sum(v['count'] for v in values)
        value=f'{low:g}' if low==high else f'{low:g}–{high:g}'
        durations.append('<div><b>'+value+' min</b><span>'+label+'</span><small>'+str(total)+' completed observations</small></div>')
    return '''<section class="card evidence runtime-review" aria-labelledby="evidence-title">
<h2 id="evidence-title">Every active configuration, every scripted policy</h2>
<p>Recorded operation-wide CPU acceptance. Twelve related development configurations; no model-quality or field-calibration claim.</p>
<div class="runtime-metrics"><div><b>'''+str(len(complete))+' / '+str(len(report['expected_cells']))+'''</b><small>Full-day replay + reward checks</small></div><div><b>'''+str(check_count)+'''</b><small>Exact checkpoint continuation checks</small></div><div><b>'''+f'{ticks:,}'+'''</b><small>One-minute operational ticks observed</small></div><div><b>'''+str(contradictions)+'''</b><small>Blocking runtime contradictions</small></div></div>
<div class="runtime-columns"><div><h3>Configuration × policy</h3><p>✓ = 1,440 minutes, replay, independent reward and applicable runtime checks. Missing or blocked runs never count as passes.</p>'''+grid+'''</div><div><h3>Which operations did each policy exercise?</h3><p>Number of configurations with an observed call or commit. Zero means unexercised by that policy; counts do not earn reward or prove successful effects.</p>'''+operations+'''</div></div>
<h3>Observed duration ranges</h3><p>Simulated minutes. Reads and draft checks take zero. Blocked/interrupted and inherited resource spans are excluded from completed-phase ranges.</p><div class="runtime-durations">'''+''.join(durations)+'''</div>
<p class="runtime-note">The observer checks actual timed paths, directed road geometry, modeled aircraft clearance/windows and destination capacity. Other checks cover deliveries, finite stock, setup, replenishment, population accounting and action clocks. These rules remain exercise assumptions; they do not establish real-world access, aviation authorization or calibrated fire behavior.</p>
<div class="runtime-footer">One CPU worker · '''+f'{report["elapsed_seconds"]:.1f}'+''' seconds · '''+f'{report["peak_rss_bytes"]/1024**2:.1f}'+''' MiB process high-water RSS. Scripted policies, not expert demonstrations. <a href="'''+html.escape(json_ref)+'''">All values, pins and limitations ↗</a></div>
</section>'''


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--report',required=True)
    p.add_argument('--output',required=True);p.add_argument('--project-report');a=p.parse_args()
    report=json.loads(Path(a.report).read_text());body=section(report,Path(a.report).name)
    Path(a.output).write_text('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Wildfire operations runtime validation</title><style>body{font:15px system-ui;max-width:1200px;margin:24px auto;padding:0 18px}'+CSS+'</style>'+body+'</html>')
    if a.project_report:
        project=Path(a.project_report);source=project.read_text()
        start=source.index('<section class="card evidence')
        end=source.index('\n<div class="two">',start)
        source=source[:start]+section(report,'../results/'+Path(a.report).name)+source[end:]
        source=source.replace('</style>',CSS+'\n</style>',1)
        project.write_text(source)


if __name__=='__main__':main()
