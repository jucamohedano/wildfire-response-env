"""Two-case paired private fire/weather sensitivity; no operations or scores."""
import argparse
import copy
import json
import math
from pathlib import Path
import time

import adapter_v1 as api
from eval.extended_scenario_drafts_v1 import build_drafts
from .estimated_fuel_v1 import prepare_estimated_inputs, _file_sha
from .estimated_weather_v1 import EstimatedWeatherFireV1
from .private_output import private_destination, read_private_json, write_private_json

VERSION='wildfire-mixed-estimated-fuel-paired-diagnostic-v1'


def main():
    start=time.monotonic();source=_file_sha(__file__)
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('strict','water-v2','overlay-v3','overlay-v4','parent','catalog','hypothesis','road','coast','water',
                 'worldcover','osm-manifest','osm-samples','dera-rivers','strict-sha256','water-v2-sha256',
                 'overlay-v3-sha256','overlay-v4-sha256','parent-sha256','catalog-sha256','road-sha256',
                 'prior-checkpoint','output'):
        p.add_argument('--'+name,required=True)
    p.add_argument('--wall-seconds',type=float,default=240.)
    p.add_argument('--opt-in-estimated-weather',action='store_true')
    a=p.parse_args()
    if not a.opt_in_estimated_weather:raise ValueError('explicit opt-in required')
    if not math.isfinite(a.wall_seconds) or not 0<a.wall_seconds<=600:raise ValueError('invalid wall budget')
    output=private_destination(a.output)
    def read(path):
        if Path(path).is_symlink():raise ValueError('symlink input unsupported')
        with open(path) as f:return json.load(f)
    strict,water,v3,v4,parent,catalog,hypo=[read(x) for x in
        (a.strict,a.water_v2,a.overlay_v3,a.overlay_v4,a.parent,a.catalog,a.hypothesis)]
    prior=read_private_json(a.prior_checkpoint,max_bytes=150_000_000)
    if (prior.get('version')!='wildfire-v4-weather-fire-run-v2'
            or prior.get('sha256')!=api._hash({k:v for k,v in prior.items() if k!='sha256'})
            or prior['identity']['input_identity']['water_v2']!=a.water_v2_sha256
            or prior['identity']['input_identity']['overlay_v4']!=a.overlay_v4_sha256):
        raise ValueError('prior private checkpoint identity differs')
    opts=dict(strict_pin=a.strict_sha256,water_pin=a.water_v2_sha256,v3_pin=a.overlay_v3_sha256,
        v4_pin=a.overlay_v4_sha256,parent_pin=a.parent_sha256,catalog_pin=a.catalog_sha256,
        road_path=a.road,road_pin=a.road_sha256,coast_path=a.coast,water_path=a.water,
        worldcover_path=a.worldcover,osm_manifest_path=a.osm_manifest,osm_samples_path=a.osm_samples,
        dera_rivers_path=a.dera_rivers,scenario='nominal',crossing_proxy=.9,opt_in_mixed_experiment=True)
    # Nominal surface assumptions stay fixed. Only the estimated fuel scenario varies.
    inputs={s:prepare_estimated_inputs(strict,water,v3,v4,parent,catalog,hypo,fuel_scenario=s,
             opt_in_estimated_fuel=True,**opts) for s in ('low','high')}
    drafts=build_drafts(catalog,parent['grid'],water['grid'])
    if drafts['sha256']!=prior['identity']['drafts_sha256']:raise ValueError('draft selection lineage differs')
    targets=[(r['column'],r['row']) for r in hypo['cells']]
    # Select one censored case for each target based on the saved fire frontier;
    # all selection, case labels and outputs remain evaluator-private.
    selection=[]
    for target in targets:
        candidates=[]
        for row in prior['cases']:
            snap=row.get('checkpoint')
            if snap is None or snap.get('status')!='censored' or row['case'] in selection:continue
            for x,y,cell in snap['fire']['land']:
                if cell['heat']>0 and abs(x-target[0])<=1 and abs(y-target[1])<=1:
                    candidates.append(row['case']);break
        if not candidates:raise ValueError('no distinct prior censored case for estimated target')
        selection.append(sorted(candidates)[0])
    if len(selection)!=2:raise ValueError('expected two independently selected cases')
    config=dict(work_gain=2,max_strength=5,decay=.5,wind_factor=1,report_delay=2,wear_factor=.1)
    entries=[]
    for case in selection:
        for scenario in ('low','high'):
            try:
                host=EstimatedWeatherFireV1(inputs[scenario],drafts,expected_drafts_sha256=drafts['sha256'],
                    case_id=case,containment_config=config,pressure_scale=1,opt_in_estimated_weather=True)
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
    report=dict(version=VERSION,role='private_unscored_weather_fire_sensitivity',runner_sha256=source,
        prior_checkpoint_sha256=prior['sha256'],drafts_sha256=drafts['sha256'],
        inputs={s:dict(inputs[s].identity) for s in inputs},cases=results,elapsed_seconds=round(time.monotonic()-start,3),
        wall_seconds_limit=a.wall_seconds,limitations=['no reward, operations, resources, population or roads',
        'all non-weather events unmodeled','initial-fuel labels do not order fire outcomes',
        'assumptions and crossing proxy are uncalibrated','partial runs are not full-day simulations',
        'no step starts after the wall deadline; source validation and one step may exceed it'])
    report['sha256']=api._hash(report);write_private_json(output,report)
    print('Private diagnostic finished.',flush=True)

if __name__=='__main__':main()
