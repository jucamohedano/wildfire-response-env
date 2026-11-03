"""Bounded, read-only qualification of recorded outcomes; never simulates a day.

All real anchor manifests and source scoring evidence remain private. Historical
scores are not rewritten: projections under this candidate are diagnostics only.
"""
import argparse
import copy
from collections import Counter
from dataclasses import asdict
import gc
import gzip
import hashlib
import html
import json
import os
from pathlib import Path
import re
import resource
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'envs/wildfire_benchmark_v2/src'))
from wildfire_benchmark_v2.grading import aggregate, cost, grade, qualify, REFERENCE_KIND
from wildfire_benchmark_v2.evidence import extract_verified_outcome
from wildfire_benchmark_v2.verifier import BenchmarkVerifier, grading_sources

MAX_JSON=4*1024**2
MAX_GZIP=8*1024**2
MAX_EXPANDED=32*1024**2
CONTROLS_VERSION='wildfire-benchmark-v2-controls-v1'


def reference_candidates(protocol):
    # Older frozen protocols declared only the original dispatcher reference.
    policies=protocol.get('reference_candidates',['dispatcher'])
    if (type(policies) is not list or not policies
            or any(type(policy) is not str or policy not in ('dispatcher','stress') for policy in policies)
            or len(set(policies))!=len(policies)
            or 'dispatcher' not in policies):
        raise ValueError('Unexpected preregistered reference candidate set')
    return policies[:]


def control_registration(protocol,policy):
    registration=copy.deepcopy(protocol['weak_active_registration'])
    if policy==registration['name']:
        return registration
    if policy=='stress' and policy in reference_candidates(protocol):
        registration.update(name='stress',configuration={'name':'stress'})
        return registration
    raise ValueError('Control policy is not preregistered')


def read_execution(report_path,report,row,cell,identity):
    headroom()
    task,policy=row['task_id'],cell['policy']
    if cell['case_id']!=task or cell['runtime_id']!=row['runtime_id']:
        raise ValueError('Control task/runtime differs from the frozen case')
    name=f"{row['runtime_id']}-{task}-{policy}.json.gz"
    path=report_path.parent/'private'/name
    if not path.resolve().is_relative_to(report_path.parent/'private'):
        raise ValueError('Evidence escapes private directory')
    compressed=read(path,MAX_GZIP)
    import io
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
        raw=stream.read(MAX_EXPANDED+1)
    if len(raw)>MAX_EXPANDED:
        raise ValueError('Decompressed evidence exceeds limit')
    saved=json.loads(raw)
    del raw
    source_cell=dict(cell,pins=dict(report['pins'],**cell['pins']))
    outcome,metadata=extract_verified_outcome(source_cell,saved,identity)
    source_hash=hashlib.sha256(compressed).hexdigest()
    del saved,compressed
    gc.collect()
    return outcome,metadata,name,source_hash


def collect_controls(path,expected_sha256,rows,baseline_report,protocol,collected):
    controls,actual_sha=document(path)
    if actual_sha!=expected_sha256:
        raise ValueError('Controls report differs from its externally expected SHA256')
    if (controls.get('version')!=CONTROLS_VERSION or controls.get('split')!='evaluation'
            or controls.get('training_eligible') is not False):
        raise ValueError('Controls must be evaluation-only')
    if controls['pins'].get('protocol_sha256')!=seal(protocol):
        raise ValueError('Controls were not collected under the frozen preregistration protocol')
    if controls.get('status') not in ('complete','blocked'):
        raise ValueError('Controls report is interrupted or not finalized')
    validator=baseline_report['pins'].get('validator_sha256')
    if (type(validator) is not str or re.fullmatch(r'[0-9a-f]{64}',validator) is None
            or controls['pins'].get('validator_sha256')!=validator):
        raise ValueError('Controls validator identity differs from the baseline')
    for key in ('implementation_sha256','observation_sha256','package_sha256','lifecycle_sha256','observation_version'):
        if key in baseline_report['pins'] and controls['pins'].get(key)!=baseline_report['pins'][key]:
            raise ValueError('Controls execution identity differs: '+key)
    cells=controls.get('cells')
    if type(cells) is not list or not cells:
        raise ValueError('Controls report has no completed cells')
    allowed={protocol['weak_active_registration']['name']} | (set(reference_candidates(protocol))-{'dispatcher'})
    registrations={policy:control_registration(protocol,policy) for policy in sorted(allowed)}
    receipt_map=controls.get('policy_registrations')
    if (type(receipt_map) is not dict or receipt_map!=registrations
            or controls['pins'].get('policy_registrations_sha256')!=seal(receipt_map)
            or seal(receipt_map)!=seal(registrations)):
        raise ValueError('Controls receipt map or registration-map pin differs from the declaration')
    declared=controls.get('expected_cells')
    if type(declared) is not list or len(declared)!=len(cells):
        raise ValueError('Controls declared domain differs from actual terminal cells')
    def identities(items):
        result=[]
        for item in items:
            if (type(item) is not dict or any(type(item.get(key)) is not str
                                            for key in ('runtime_id','case_id','policy'))):
                raise ValueError('Malformed controls domain identity')
            result.append(tuple(item[key] for key in ('runtime_id','case_id','policy')))
        return result
    expected_ids,actual_ids=identities(declared),identities(cells)
    if controls['pins'].get('campaign_domain_sha256')!=seal(declared):
        raise ValueError('Controls frozen campaign domain pin differs')
    if (len(set(expected_ids))!=len(declared) or len(set(actual_ids))!=len(cells)
            or set(expected_ids)!=set(actual_ids)):
        raise ValueError('Controls declared identities differ from actual terminal cells')
    statuses=Counter(cell.get('status') for cell in cells)
    if not set(statuses)<= {'complete','blocked'}:
        raise ValueError('Controls report contains interrupted or ineligible execution')
    summary=controls.get('summary',{})
    if (type(summary) is not dict or type(summary.get('expected')) is not int or summary['expected']!=len(declared)
            or type(summary.get('finished')) is not int or summary['finished']!=len(cells)
            or controls['status']!=('blocked' if statuses['blocked'] else 'complete')):
        raise ValueError('Controls completion summary differs from terminal evidence')
    if 'statuses' in summary and (summary['statuses']!=dict(statuses)
            or any(type(value) is not int for value in summary['statuses'].values())):
        raise ValueError('Controls status counts differ from terminal evidence')
    verified,hashes={},{}
    for cell in cells:
        task,policy=cell['case_id'],cell['policy']
        if task not in rows or policy not in allowed:
            raise ValueError('Unexpected controls case or policy')
        key=(task,policy)
        if key in verified:
            raise ValueError('Duplicate control execution')
        if cell.get('status') not in ('complete','blocked'):
            raise ValueError('Controls report contains interrupted or ineligible execution')
        passive,passive_meta=collected[(task,'passive')]
        outcome,metadata,name,source_hash=read_execution(path,controls,rows[task],cell,passive_meta['identity'])
        if (metadata['initial_audit_sha256']!=passive_meta['initial_audit_sha256']
                or metadata['identity']!=passive_meta['identity']):
            raise ValueError('Control and passive began from different worlds: '+task)
        registration=control_registration(protocol,policy)
        if (metadata.get('policy_registration')!=registration
                or metadata.get('policy_registration_sha256')!=seal(registration)):
            raise ValueError('Control receipt differs from its preregistered policy: '+policy)
        if policy==protocol['weak_active_registration']['name']:
            count=metadata.get('operational_order_count')
            if type(count) is not int or count<=0:
                raise ValueError('Conservative control lacks valid committed operational orders')
        # Validate outcome denominators against the frozen passive world too.
        qualify(passive,outcome)
        verified[key]=(outcome,metadata)
        hashes[name]=source_hash
    return verified,hashes,actual_sha


def select_reference(passive,candidates,declared):
    eligible=[policy for policy in declared if policy in candidates
              and candidates[policy][0].new_exposed<=passive.new_exposed]
    if not eligible:
        # Retain an explicit unqualified row rather than inventing or dropping it.
        return 'dispatcher','no_exposure_eligible_reference'
    return min(eligible,key=lambda policy:cost(candidates[policy][0])),'best_observed_eligible'


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def seal(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def read(path,maximum):
    if path.is_symlink():raise ValueError('Symlink evidence is not accepted')
    with path.open('rb') as stream:raw=stream.read(maximum+1)
    if len(raw)>maximum:raise ValueError('Evidence exceeds bounded size: '+str(path))
    return raw


def document(path,maximum=MAX_JSON):
    raw=read(path,maximum)
    return json.loads(raw),hashlib.sha256(raw).hexdigest()


def headroom():
    for line in Path('/proc/meminfo').read_text().splitlines():
        if line.startswith('MemAvailable:') and int(line.split()[1])*1024<1536*1024**2:
            raise MemoryError('Insufficient memory reserve for bounded evidence analysis')


def write(path,value):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:json.dump(value,stream,sort_keys=True,indent=2,allow_nan=False);stream.write('\n')


def render_report(result, diagnostics):
    """Small standalone private report; projected endpoints are not model results."""
    qualified=sum(row['qualification']['qualified'] for row in diagnostics.values())
    weak_count=sum('weak_active_outcome' in case for case in result['cases'].values())
    gate=result['qualification']
    released=gate['release_ready']
    badge=('Released protocol · verified qualification' if released else
           'Candidate only · official benchmark score unavailable')
    official=f"{gate['official_mean']:.4f}" if gate['official_mean'] is not None else 'Blocked'
    families=result['protocol']['families']
    bars=[]
    for index,family in enumerate(families):
        rows=[diagnostics[task] for task,case in result['cases'].items() if case['family']==family]
        y=40+index*62
        bars.append(f'<text x="0" y="{y+13}" font-size="13">{html.escape(family.replace("_"," "))}</text>')
        for offset,key,color in ((0,'legacy_passive_reward','#a35233'),(22,'historical_dispatcher_raw_reward','#22756b')):
            value=sum(row[key] for row in rows)/len(rows)
            bars.append(f'<rect x="190" y="{y+offset}" width="{value*420:.3f}" height="16" rx="3" fill="{color}"/>'
                        f'<text x="620" y="{y+offset+13}" font-size="12">{value:.4f}</text>')
    table=[]
    for task,row in diagnostics.items():
        check=row['qualification']
        case=result['cases'][task]
        values=(task.rsplit('-',1)[-1],case['family'].replace('_',' '),
            f"{row['legacy_passive_reward']:.4f}",
            f"{row['historical_dispatcher_raw_reward']:.4f}",
            case['reference_policy'],f"{row['selected_reference_raw_reward']:.4f}",
            '0.000' if check['qualified'] else 'Unqualified',
            '1.000' if check['qualified'] else 'Unqualified',
            str(check['exposure_improvement_people']),
            f"{check['burn_improvement_cells']*case['passive_outcome']['cell_area_hectares']:g}",
            ', '.join(case['reference_metadata']['adverse_flags']) or 'None')
        table.append('<tr>'+''.join('<td>'+html.escape(value)+'</td>' for value in values)+'</tr>')
    return '''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Wildfire Ops: reward qualification candidate</title>
<style>body{font:16px/1.6 system-ui,sans-serif;background:#f6f5f0;color:#172f2b;max-width:1120px;margin:36px auto;padding:0 22px}h1{line-height:1.2}h2{font-size:21px}p{max-width:88ch}.badge{background:#ffecbb;padding:8px 14px;border-radius:8px;display:inline-block}.cards{display:flex;gap:14px;flex-wrap:wrap}.card{flex:1;min-width:170px;background:white;border:1px solid #cfd9d0;border-radius:10px;padding:16px}.number{font-size:30px;font-weight:700}.muted{color:#566b63}.chart{background:white;padding:20px;border-radius:10px}svg{width:100%;max-width:760px;height:auto}table{border-collapse:collapse;width:100%;font-size:13px;background:white}td,th{padding:9px 10px;text-align:left;border-bottom:1px solid #d5ddd5}th{background:#e6ece4}code{background:#e5ece4;padding:2px 5px}.scroll{overflow:auto}a{color:#176e65}.key{display:inline-block;width:13px;height:13px;margin-right:6px;border-radius:2px}footer{margin:30px 0;font-size:12px;overflow-wrap:anywhere}</style>
<p class="badge">'''+html.escape(badge)+'''</p>
<h1>Fix the score before ranking models</h1>
<p>The old score rewards large areas that never burn. The candidate measures improvement over passive behavior using verified outcome costs. These are projections from saved scripted runs, not new model evaluations.</p>
<div class="cards"><div class="card"><div class="number">24</div>Expected scenarios</div>
<div class="card"><div class="number">'''+str(qualified)+''' / 24</div>Positive reference headroom</div>
<div class="card"><div class="number">'''+str(weak_count)+''' / 24</div>Verified weak-active controls</div>
<div class="card"><div class="number">'''+official+'''</div>Official aggregate</div></div>
<h2>Why the old score is misleading</h2>
<p><span class="key" style="background:#a35233"></span>Passive &nbsp; <span class="key" style="background:#22756b"></span>Dispatcher · historical raw means by family</p>
<div class="chart"><svg viewBox="0 0 710 425" role="img" aria-label="Historical raw scores are close to one even for passive behavior"><line x1="190" x2="610" y1="22" y2="22" stroke="#9cac9e"/><text x="190" y="15" font-size="12">0</text><text x="602" y="15" font-size="12">1</text>'''+''.join(bars)+'''</svg></div>
<h2>Same 80/20 harm objective, useful candidate scale</h2>
<p><code>cost = 0.8 × new exposure fraction + 0.2 × new burned fraction</code></p>
<p><code>grade = clip((passive cost − agent cost) / (passive cost − reference cost), 0, 1)</code></p>
<p>Passive = 0 and the scripted reference = 1 <strong>by construction, only where a positive benefit is verified</strong>. Neither endpoint demonstrates difficulty. Better-than-reference and negative unclipped gains remain in JSON. References are best observed, not proven optimal.</p>
<h2>Qualification inventory</h2>
<p>No incomplete subset average is reported. The unchanged catalog remains 24 cases. Missing or too-strong weak-active controls, unqualified references and a candidate protocol block release; no original result has been replaced. Historical dispatcher raw scores remain explicitly separate from the selected best-found reference.</p>
<div class="scroll"><table><thead><tr><th>Case</th><th>Family</th><th>Historical passive raw</th><th>Historical dispatcher raw</th><th>Selected reference policy</th><th>Selected reference raw</th><th>Candidate passive</th><th>Candidate selected reference</th><th>People protected vs passive</th><th>Hectares saved vs passive</th><th>Reference adverse flags</th></tr></thead><tbody>'''+''.join(table)+'''</tbody></table></div>
<h2>Next evidence required</h2><p>Establish beneficial references for any unqualified candidates, collect a preregistered weak-active control on every case, and explicitly release a qualified protocol. Preserve verified adverse outcomes as flags; do not convert them into infrastructure failures.</p>
<p><a href="diagnostics.json">Per-case diagnostic JSON</a> · <a href="anchors.json">Private pinned anchors</a></p>
<footer>Historical source SHA256: '''+html.escape(result['source_report_sha256'])+'''<br>Candidate anchor SHA256: '''+html.escape(result['sha256'])+'''<br>Private evaluator artifact. Do not send anchors or curation outcomes to the model.</footer></html>'''


def build(report_path,freeze_path,data_dir,protocol_path,output,*,expected_report_sha256,expected_freeze_sha256,
          controls_report=None,expected_controls_sha256=None):
    loaded_grader_sources=grading_sources()
    report_path,freeze_path,data_dir=report_path.resolve(strict=True),freeze_path.resolve(strict=True),data_dir.resolve(strict=True)
    output=output.absolute()
    resolved=output.resolve()
    if output.exists() or output.is_symlink() or any(resolved==p or p in resolved.parents for p in (data_dir,report_path.parent)):
        raise ValueError('Output must be fresh and separate from source evidence/package')
    if (controls_report is None)!=(expected_controls_sha256 is None):
        raise ValueError('Controls report and externally expected SHA256 must be supplied together')
    if controls_report is not None:
        controls_report=Path(controls_report).resolve(strict=True)
        if resolved==controls_report.parent or controls_report.parent in resolved.parents:
            raise ValueError('Output must be separate from controls evidence')
    report,report_sha=document(report_path);freeze,freeze_sha=document(freeze_path)
    if report.get('version')!='wildfire-packaged-evaluation-v1' or report.get('split')!='evaluation' or report.get('training_eligible') is not False:
        raise ValueError('Expected verified evaluation-only baseline source')
    if report_sha!=expected_report_sha256 or freeze_sha!=expected_freeze_sha256:raise ValueError('Reviewed source SHA differs')
    manifest,manifest_sha=document(data_dir/'manifest.json')
    if manifest_sha!=freeze['manifest_sha256'] or seal(manifest)!=report['pins']['package_sha256'] or seal(manifest)!=freeze['package_sha256']:
        raise ValueError('Frozen package identity differs')
    # Stream every pinned asset; do not import GIS, model or simulator libraries.
    for item in manifest['files']:
        path=data_dir/item['path']
        if not path.resolve().is_relative_to(data_dir) or path.is_symlink() or path.stat().st_size!=item['bytes']:
            raise ValueError('Package asset path/size differs')
        with path.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest()!=item['sha256']:raise ValueError('Package asset hash differs')
    protocol,_=document(protocol_path)
    if protocol['version']!='wildfire-benchmark-protocol-v2' or protocol['expected_tasks']!=24 or len(protocol['families'])!=6:
        raise ValueError('Unexpected protocol domain')
    if protocol['objective']!={'version':'new-harm-cost-v1','population_weight':.8,'burned_area_weight':.2}:
        raise ValueError('Unexpected objective')
    declared_references=reference_candidates(protocol)
    rows={row['task_id']:row for row in freeze['tasks']}
    if len(rows)!=24 or len(freeze['tasks'])!=24 or any(row['split']!='evaluation' or row['training_eligible'] is not False for row in rows.values()):
        raise ValueError('Expected24 frozen evaluation-only rows')
    expected={(task,policy) for task in rows for policy in ('passive','dispatcher')}
    cells={(c['case_id'],c['policy']):c for c in report['cells']}
    if len(cells)!=48 or len(report['cells'])!=48 or set(cells)!=expected:
        raise ValueError('Source report does not cover exact48 baseline cells')
    if report['baseline_coverage_contract']['unobserved_required']:
        raise ValueError('Source baseline coverage is incomplete')
    if report['pins'].get('observation_version')!='v3' or manifest['engine_implementation_sha256']!=report['pins']['implementation_sha256']:
        raise ValueError('Source must use the declared corrected observation/core contract')
    collected={};evidence_hashes={}
    for task,policy in sorted(expected):
        row=rows[task];cell=cells[(task,policy)]
        if not re.fullmatch(r'incident-day-evaluation-v1-\d{3}',task) or cell['runtime_id']!=row['runtime_id']:
            raise ValueError('Unexpected task/runtime ID')
        runtime=manifest['runtimes'][row['runtime_id']]
        identity=dict(task_id=task,seed=row['episode_seed'],scenario_sha256=row['scenario_sha256'],
            inputs_sha256=runtime['inputs_sha256'],network_sha256=runtime['network_sha256'],
            implementation_sha256=report['pins']['implementation_sha256'],observation_sha256=report['pins']['observation_sha256'],
            interface_version=protocol['source_observation_contract'])
        outcome,metadata,name,source_hash=read_execution(report_path,report,row,cell,identity)
        collected[(task,policy)]=(outcome,metadata)
        evidence_hashes[name]=source_hash
    controls,controls_hashes,controls_sha={},{},None
    if controls_report is not None:
        controls,controls_hashes,controls_sha=collect_controls(controls_report,expected_controls_sha256,
            rows,report,protocol,collected)
    cases={}
    for task,row in sorted(rows.items()):
        p,pm=collected[(task,'passive')];dispatcher,dispatcher_meta=collected[(task,'dispatcher')]
        if (pm['initial_audit_sha256']!=dispatcher_meta['initial_audit_sha256']
                or pm['identity']!=dispatcher_meta['identity']):
            raise ValueError('Baseline policies began from different worlds: '+task)
        qualify(p,dispatcher)
        candidates={'dispatcher':(dispatcher,dispatcher_meta)}
        if (task,'stress') in controls:
            candidates['stress']=controls[(task,'stress')]
        selected,selection=select_reference(p,candidates,declared_references)
        r,rm=candidates[selected]
        if pm['initial_audit_sha256']!=rm['initial_audit_sha256'] or pm['identity']!=rm['identity']:
            raise ValueError('Reference policies began from different worlds: '+task)
        group=row['configuration_group'];match=re.fullmatch(r'evaluation-v1-family-([0-5])',group)
        if not match:raise ValueError('Unknown family group')
        cases[task]=dict(identity=pm['identity'],family=protocol['families'][int(match[1])],
            initial_audit_sha256=pm['initial_audit_sha256'],passive_outcome=asdict(p),reference_outcome=asdict(r),
            passive_metadata=pm,reference_metadata=rm,reference_policy=selected,
            candidate_reference_policies=declared_references[:],reference_kind=REFERENCE_KIND,reference_proven_optimal=False,
            reference_selection=selection,
            observed_reference_policies=[policy for policy in declared_references if policy in candidates],
            source_evidence_sha256={'passive':evidence_hashes[f"{row['runtime_id']}-{task}-passive.json.gz"],
                selected:(evidence_hashes if selected=='dispatcher' else controls_hashes)[
                    f"{row['runtime_id']}-{task}-{selected}.json.gz"]})
        weak_policy=protocol['weak_active_registration']['name']
        if (task,weak_policy) in controls:
            weak,weak_meta=controls[(task,weak_policy)]
            cases[task].update(weak_active_outcome=asdict(weak),weak_active_metadata=weak_meta,
                weak_active_policy=weak_policy,
                weak_active_evidence_sha256=controls_hashes[f"{row['runtime_id']}-{task}-{weak_policy}.json.gz"])
    if Counter(c['family'] for c in cases.values())!=Counter({f:4 for f in protocol['families']}):
        raise ValueError('Expected4 tasks per family')
    passives={t:collected[(t,'passive')][0] for t in rows}
    refs={t:cases[t]['reference_outcome'] for t in rows}
    weak={t:case['weak_active_outcome'] for t,case in cases.items() if 'weak_active_outcome' in case}
    qualification=aggregate(refs,passives,refs,expected_tasks=sorted(rows),weak_active=weak,
        protocol_released=protocol['status']=='released')
    diagnostics={t:dict(qualification=qualify(passives[t],refs[t]),
        projected_passive=grade(passives[t],passives[t],refs[t]),projected_reference=grade(refs[t],passives[t],refs[t]),
        legacy_passive_reward=cells[(t,'passive')]['outcome']['graded_score'],
        legacy_reference_reward=cases[t]['reference_metadata']['raw_reward'],
        historical_dispatcher_raw_reward=cells[(t,'dispatcher')]['outcome']['graded_score'],
        selected_reference_policy=cases[t]['reference_policy'],
        selected_reference_raw_reward=cases[t]['reference_metadata']['raw_reward']) for t in sorted(rows)}
    result=dict(version='wildfire-benchmark-anchors-v2',protocol=protocol,protocol_sha256=seal(protocol),
        expected_tasks=sorted(rows),cases=cases,qualification=qualification,
        source_report_sha256=report_sha,source_freeze_sha256=freeze_sha,package_manifest_sha256=manifest_sha,
        source_pins=report['pins'],source_evidence_sha256=evidence_hashes,
        grading_sources=loaded_grader_sources,
        build_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        historical_projection_only=True,not_new_model_results=True,training=False)
    if controls_report is not None:
        result.update(source_controls_report_sha256=controls_sha,controls_source_evidence_sha256=controls_hashes,
            controls_complete_for_supplied_cells=True,
            control_policy_registrations={policy:control_registration(protocol,policy)
                for policy in sorted({policy for _,policy in controls})})
    result['sha256']=seal(result)
    # The writer and terminal verifier must accept the exact same protocol,
    # task domain, anchor evidence and source identity before anything is saved.
    BenchmarkVerifier(result,result['sha256'])
    # Recheck generation inputs before writing. No source artifact is modified.
    if grading_sources()!=loaded_grader_sources:
        raise ValueError('Loaded grading sources changed during qualification')
    if document(report_path)[1]!=report_sha or document(freeze_path)[1]!=freeze_sha:
        raise ValueError('Source evidence changed during qualification')
    if controls_report is not None and document(controls_report)[1]!=controls_sha:
        raise ValueError('Controls report changed during qualification')
    output.mkdir(mode=0o700,parents=True,exist_ok=False)
    write(output/'anchors.json',result);write(output/'diagnostics.json',diagnostics)
    fd=os.open(output/'report.html',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:stream.write(render_report(result,diagnostics))
    good=sum(d['qualification']['qualified'] for d in diagnostics.values())
    print(json.dumps(dict(tasks=len(cases),normalization_ready=good,unqualified=len(cases)-good,
        release_ready=qualification['release_ready'],official_mean=qualification['official_mean'],output=str(output))))
    return result,diagnostics


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('source-report','task-freeze','data-dir','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--protocol',type=Path,default=ROOT/'data/benchmark_v2/protocol.json')
    p.add_argument('--expected-source-report-sha256',required=True);p.add_argument('--expected-task-freeze-sha256',required=True)
    p.add_argument('--controls-report',type=Path)
    p.add_argument('--expected-controls-report-sha256')
    args=p.parse_args()
    # Hard address-space ceiling for bounded artifact analysis, not a simulation.
    resource.setrlimit(resource.RLIMIT_AS,(256*1024**2,256*1024**2))
    build(args.source_report,args.task_freeze,args.data_dir,args.protocol,args.output,
          expected_report_sha256=args.expected_source_report_sha256,expected_freeze_sha256=args.expected_task_freeze_sha256,
          controls_report=args.controls_report,expected_controls_sha256=args.expected_controls_report_sha256)


if __name__=='__main__':main()
