"""Render aggregate evaluation evidence; never include private cell artifacts."""
import argparse
import copy
import json
import html
from collections import defaultdict
import os
from pathlib import Path
from eval.render_source_matrix_report import render
from eval.evaluate_packaged_runtime import refresh_report, VERSION


def render_score_view(view):
    if view.get('version')!='wildfire-evaluation-derived-score-view-v1':
        raise ValueError('Expected verified derived scoring view')
    cells=view['cells']; families=('Handover', 'Wind access', 'Crew access', 'Aircraft windows', 'Destinations', 'Centre shortages')
    means=defaultdict(list); rows=[]
    for c in cells:
        family=int(c['case_id'].rsplit('-',1)[1])//4
        score=c.get('reported_score')
        if score is not None: means[(family,c['policy'])].append(score)
        status=c['operational_status']
        rows.append('<tr>'+''.join('<td>'+html.escape(str(v))+'</td>' for v in (
            c['case_id'],c['policy'],'unscored' if score is None else f'{score:.4f}',
            f"{c['outcome']['protected_population_fraction']:.1%}",
            c['outcome']['additional_burned_hectares'],status,c['status']))+'</tr>')
    chart=[]
    for i,label in enumerate(families):
        y=30+i*55;chart.append(f'<text x="0" y="{y+12}">{html.escape(label)}</text>')
        for offset,policy,color in ((0,'passive','#738597'),(19,'dispatcher','#247c65')):
            values=means[(i,policy)];avg=sum(values)/len(values) if values else 0
            chart.append(f'<rect x="155" y="{y+offset}" width="{avg*430:.2f}" height="15" fill="{color}"/><text x="{165+avg*430:.2f}" y="{y+offset+12}">{avg:.3f}</text>')
    adverse=[c for c in cells if c['adverse']]
    flags=''.join('<li>'+html.escape(c['case_id']+' / '+c['policy']+': '+c['operational_status'])+'</li>' for c in adverse)
    return ('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Wildfire evaluation-only catalog</title><style>body{font:15px system-ui;max-width:1150px;margin:32px auto;padding:0 18px;color:#243b3d}table{border-collapse:collapse;width:100%;font-size:13px}td,th{padding:7px;text-align:left;border-bottom:1px solid #ddd}svg{max-width:100%;font:13px system-ui}.flag{background:#fff3d4;padding:14px;border-radius:8px}.scroll{overflow:auto}</style>'
        '<h1>Wildfire evaluation-only catalog</h1><p>24 synthetic configurations · 6 families · no training split</p>'
        f'<p><strong>{sum(c["reported_score"] is not None for c in cells)}/{len(cells)} scored baseline runs · {len(adverse)} adverse outcome flags</strong></p>'
        '<p>Every episode was replayed from its recorded journal and independently reproduced its reward. The reward formula is unchanged. Operational flags accompany the score; a high score is not a claim of complete operational safety.</p>'
        '<h2>Mean reward by family</h2><p>Grey: passive · Green: dispatcher. Shared terrain and parent fire seeds; no independence or real-world calibration claim.</p>'
        '<svg viewBox="0 0 690 380" role="img" aria-label="Mean reward of passive and dispatcher policies by scenario family">'+''.join(chart)+'</svg>'
        '<div class="flag"><strong>Adverse outcomes retained explicitly</strong><ul>'+flags+'</ul><p>Unresolved at horizon means assistance was still needed at minute 1440. No later recovery is inferred. Strict validation statuses remain visible.</p></div>'
        '<h2>Per-task results</h2><div class="scroll"><table><tr><th>Task</th><th>Policy</th><th>Score</th><th>Population protected</th><th>Additional burn ha</th><th>Operational flag</th><th>Strict status</th></tr>'+''.join(rows)+'</table></div>'
        '<p>Reporting policy: retain the verified reward and flag specifically evidenced adverse outcomes. Broken invariants and missing evidence remain unscored.</p></html>')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--report',type=Path)
    source.add_argument('--score-view',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.score_view:
        page=render_score_view(json.loads(args.score_view.read_text()))
    else:
        report=json.loads(args.report.read_text())
        if report.get('version')!=VERSION or report.get('split')!='evaluation':
            raise ValueError('Expected evaluation-only report')
        refresh_report(report)
        shown=copy.deepcopy(report)
        shown.update(report['pins'])
        page=render(shown).replace('Source matrix review','Evaluation catalog validation').replace(
            'Source-map development matrix','Evaluation-only catalog validation').replace(
            'Private development evidence for synthetic exercises.',
            'Private evaluation-only evidence. Shared landscape and parent fire seeds; no training split.')
    fd=os.open(args.output,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as stream: stream.write(page)


if __name__=='__main__': main()
