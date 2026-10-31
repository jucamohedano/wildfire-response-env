"""Private all-censored-case nominal fuel follow-up; no operations or scores.

Uses independently reconstructed four-cell hypotheses and leaves the
underlying censor unchanged. Output and checkpoints are evaluator-private.
"""
import argparse
import copy
import json
import math
from pathlib import Path
import time

import adapter_v1 as api
from eval.extended_scenario_drafts_v1 import build_drafts
import wildfire_expanded_incident_v1.estimated_fuel_v1 as estimated_fuel
from wildfire_expanded_incident_v1.estimated_fuel_v1 import prepare_estimated_inputs, _file_sha
from wildfire_expanded_incident_v1.estimated_weather_v1 import EstimatedWeatherFireV1
from wildfire_expanded_incident_v1.private_output import private_destination, read_private_json, write_private_json

VERSION='wildfire-estimated-fuel-frontier-followup-v2'
# Canonical digest independently produced by the private full-source compiler.
HYPOTHESIS_PIN='5ea3d8ffdf47e1854272a1bf6af65f6d51e12c3ca066e344720e6d4f05cb1266'


def selected_cases(prior, targets):
    """Select censored cases adjacent to a hypothesized cell, without changing it."""
    targets=set(targets)
    if not targets:
        raise ValueError('empty hypothesis')
    selected=[]
    for row in prior['cases']:
        snap=row.get('checkpoint')
        if snap is None or snap.get('status')!='censored':
            continue
        if any(cell['heat']>0 and cell['fuel']>0 and
               any(abs(x-tx)<=1 and abs(y-ty)<=1 for tx,ty in targets)
               for x,y,cell in snap['fire']['land']):
            selected.append(row['case'])
    if len(selected)!=len(set(selected)):
        raise ValueError('duplicate case in prior checkpoint')
    return selected


def main():
    start=time.monotonic();source=_file_sha(__file__)
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('strict','water-v2','overlay-v3','overlay-v4','parent','catalog','hypothesis','road','coast','water',
                 'worldcover','osm-manifest','osm-samples','dera-rivers','strict-sha256','water-v2-sha256',
                 'overlay-v3-sha256','overlay-v4-sha256','parent-sha256','catalog-sha256','road-sha256',
                 'prior-checkpoint','prior-checkpoint-sha256','output'):
        p.add_argument('--'+name,required=True)
    p.add_argument('--resume', help='prior private follow-up report')
    p.add_argument('--resume-sha256', help='independent canonical hash of resume report')
    p.add_argument('--wall-seconds',type=float,default=240.)
    p.add_argument('--opt-in-estimated-weather',action='store_true')
    a=p.parse_args()
    if not a.opt_in_estimated_weather:raise ValueError('explicit opt-in required')
    if bool(a.resume)!=bool(a.resume_sha256):raise ValueError('resume and independent pin must be supplied together')
    if not math.isfinite(a.wall_seconds) or not 0<a.wall_seconds<=600:raise ValueError('invalid wall budget')
    output=private_destination(a.output)
    def read(path):
        if Path(path).is_symlink():raise ValueError('symlink input unsupported')
        with open(path) as f:return json.load(f)
    strict,water,v3,v4,parent,catalog,hypo=[read(x) for x in
        (a.strict,a.water_v2,a.overlay_v3,a.overlay_v4,a.parent,a.catalog,a.hypothesis)]
    prior=read_private_json(a.prior_checkpoint,max_bytes=150_000_000)
    if (prior.get('version')!='wildfire-estimated-fuel-frontier-followup-v1'
            or prior.get('sha256')!=a.prior_checkpoint_sha256
            or prior.get('sha256')!=api._hash({k:v for k,v in prior.items() if k!='sha256'})
            or any(prior['inputs']['nominal']['base'].get(k)!=v for k,v in (
                ('strict',a.strict_sha256),('parent',a.parent_sha256),('catalog',a.catalog_sha256),
                ('overlay_v3',a.overlay_v3_sha256),('road',a.road_sha256)))
            or prior['inputs']['nominal']['base']['water_v2']!=a.water_v2_sha256
            or prior['inputs']['nominal']['base']['overlay_v4']!=a.overlay_v4_sha256):
        raise ValueError('prior private checkpoint identity differs')
    opts=dict(strict_pin=a.strict_sha256,water_pin=a.water_v2_sha256,v3_pin=a.overlay_v3_sha256,
        v4_pin=a.overlay_v4_sha256,parent_pin=a.parent_sha256,catalog_pin=a.catalog_sha256,
        road_path=a.road,road_pin=a.road_sha256,coast_path=a.coast,water_path=a.water,
        worldcover_path=a.worldcover,osm_manifest_path=a.osm_manifest,osm_samples_path=a.osm_samples,
        dera_rivers_path=a.dera_rivers,scenario='nominal',crossing_proxy=.9,opt_in_mixed_experiment=True)
    # The private experiment admits only the separately reconstructed v2 pin.
    # This assignment is confined to this evaluator process; host code is unchanged.
    estimated_fuel.HYPOTHESIS_SHA256=HYPOTHESIS_PIN
    # Both surface and fuel use the explicitly uncalibrated nominal scenario.
    inputs={s:prepare_estimated_inputs(strict,water,v3,v4,parent,catalog,hypo,fuel_scenario=s,
             opt_in_estimated_fuel=True,**opts) for s in ('nominal',)}
    drafts=build_drafts(catalog,parent['grid'],water['grid'])
    if drafts['sha256']!=prior['drafts_sha256']:raise ValueError('draft selection lineage differs')
    targets=[(r['column'],r['row']) for r in hypo['cells']]
    selection=selected_cases(prior,targets)
    if not selection:raise ValueError('no censored case adjacent to a hypothesis')
    resume=None
    if a.resume:
        resume=read_private_json(a.resume,max_bytes=150_000_000)
        if (resume.get('version')!=VERSION or resume.get('sha256')!=a.resume_sha256
                or resume['sha256']!=api._hash({k:v for k,v in resume.items() if k!='sha256'})
                or resume.get('prior_checkpoint_sha256')!=prior['sha256']
                or resume.get('drafts_sha256')!=drafts['sha256']
                or api._encoded(resume.get('inputs'))!=api._encoded({s:dict(inputs[s].identity) for s in inputs})
                or [r['case'] for r in resume['cases']]!=selection
                or any(r.get('assumption')!='nominal' or r.get('status') not in
                       ('budget_exhausted','censored','diagnostic_end') for r in resume['cases'])):
            raise ValueError('resume report identity or cases differ')
    config=dict(work_gain=2,max_strength=5,decay=.5,wind_factor=1,report_delay=2,wear_factor=.1)
    entries=[]
    for case in selection:
        for scenario in ('nominal',):
            try:
                host=EstimatedWeatherFireV1(inputs[scenario],drafts,expected_drafts_sha256=drafts['sha256'],
                    case_id=case,containment_config=config,pressure_scale=1,opt_in_estimated_weather=True)
                if resume:
                    saved=next(r for r in resume['cases'] if r['case']==case)
                    host.restore(saved['checkpoint'])
                    if (host.situation()['status'] != ('active' if saved['status']=='budget_exhausted' else saved['status'])):
                        raise ValueError('resume status differs from checkpoint')
                entries.append(dict(case=case,scenario=scenario,host=host,error=None))
            except Exception as exc:entries.append(dict(case=case,scenario=scenario,host=None,error=dict(kind=type(exc).__name__,detail=str(exc))))
    deadline=start+a.wall_seconds
    while time.monotonic()<deadline:
        live=[e for e in entries if e['host'] is not None and e['host'].situation()['status']=='active']
        if not live:break
        for e in live:
            for _ in range(30):
                if time.monotonic()>=deadline or e['host'].situation()['status']!='active':break
                try:e['host'].step()
                except Exception as exc:e['error']=dict(kind=type(exc).__name__,detail=str(exc));e['host']=None;break
    if _file_sha(__file__)!=source:raise RuntimeError('runner source changed')
    results=[]
    for e in entries:
        h=e['host']
        if h is None:results.append(dict(case=e['case'],assumption=e['scenario'],status='error',error=e['error']));continue
        state=h.situation(); status=state['status'] if state['status']!='active' else 'budget_exhausted'
        results.append(dict(case=e['case'],assumption=e['scenario'],status=status,minute=state['minute'],
            warmup_steps=state['warmup_steps'],censor_reasons=list(h._fire.reasons) if status=='censored' else [],
            checkpoint=h.checkpoint(),seed=h._case['seed']))
    report=dict(version=VERSION,role='private_unscored_weather_fire_frontier_followup',runner_sha256=source,
        prior_checkpoint_sha256=prior['sha256'],resume_sha256=resume['sha256'] if resume else None,drafts_sha256=drafts['sha256'],
        inputs={s:dict(inputs[s].identity) for s in inputs},cases=results,elapsed_seconds=round(time.monotonic()-start,3),
        wall_seconds_limit=a.wall_seconds,limitations=['no reward, operations, resources, population or roads',
        'all non-weather events unmodeled', 'selection uses prior censored frontiers; new gaps may still censor','synthetic nominal fuel hypothesis is not a source measurement',
        'assumptions and crossing proxy are uncalibrated','partial runs are not full-day simulations',
        'no step starts after the wall deadline; source validation and one step may exceed it'])
    report['sha256']=api._hash(report);write_private_json(output,report)
    print('Private diagnostic finished.',flush=True)

if __name__=='__main__':main()
