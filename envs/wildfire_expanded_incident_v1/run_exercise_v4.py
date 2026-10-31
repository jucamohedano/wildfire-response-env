"""Private v4 source-grounded passive fire diagnostics; no score or routing."""
import argparse
import hashlib
import json
from pathlib import Path

import adapter_v1 as api
from eval.extended_scenario_drafts_v1 import build_drafts
from .exercise_v4 import prepare_exercise_v4
from .mixed_host_v3 import SourceGroundedMixedDiagnosticV4
from .private_output import private_destination, write_private_json


def main():
    runner_pin=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('strict','water-v2','overlay-v3','overlay-v4','parent','catalog','road','coast','water',
                 'worldcover','osm-manifest','osm-samples','dera-rivers','strict-sha256','water-v2-sha256',
                 'overlay-v3-sha256','overlay-v4-sha256','parent-sha256','catalog-sha256','road-sha256',
                 'scenario','crossing-proxy','output'):
        p.add_argument('--'+name,required=True)
    p.add_argument('--opt-in-mixed-experiment',action='store_true')
    a=p.parse_args()
    out=private_destination(a.output)
    vals=[]
    for path in (a.strict,a.water_v2,a.overlay_v3,a.overlay_v4,a.parent,a.catalog):
        if Path(path).is_symlink():raise ValueError('input symlink unsupported')
        with open(path) as f:vals.append(json.load(f))
    strict,water,v3,v4,parent,catalog=vals
    inputs=prepare_exercise_v4(strict,water,v3,v4,parent,catalog,strict_pin=a.strict_sha256,
        water_pin=a.water_v2_sha256,v3_pin=a.overlay_v3_sha256,v4_pin=a.overlay_v4_sha256,
        parent_pin=a.parent_sha256,catalog_pin=a.catalog_sha256,road_path=a.road,road_pin=a.road_sha256,
        coast_path=a.coast,water_path=a.water,worldcover_path=a.worldcover,osm_manifest_path=a.osm_manifest,
        osm_samples_path=a.osm_samples,dera_rivers_path=a.dera_rivers,scenario=a.scenario,
        crossing_proxy=float(a.crossing_proxy),opt_in_mixed_experiment=a.opt_in_mixed_experiment)
    drafts=build_drafts(catalog,parent['grid'],water['grid'])
    config=dict(work_gain=2,max_strength=5,decay=.5,wind_factor=1,report_delay=2,wear_factor=.1)
    cases=[]
    for case in drafts['cases']:
        try:
            host=SourceGroundedMixedDiagnosticV4(inputs,drafts,expected_drafts_sha256=drafts['sha256'],
                case_id=case['id'],containment_config=config,pressure_scale=1,opt_in_mixed_experiment=True)
            while host.situation()['status']=='active':host.advance(60)
            state=host.situation()
            reason=('fire_support_censor' if state['status']=='censored' else
                    'scheduled_event_or_weather_unsupported' if state['status']=='blocked' else None)
            result=dict(case=case['id'],status=state['status'],minute=state['minute'],
                warmup_steps=state['warmup_steps'],warmup_target=case['warmup_minutes'],blocker=reason,
                censor_reasons=list(host._fire.reasons) if state['status']=='censored' else [],
                host_identity=host._identity)
        except Exception as exc:
            result=dict(case=case['id'],status='error',blocker=type(exc).__name__,detail=str(exc))
        cases.append(result);print(json.dumps({'completed':len(cases),'total':len(drafts['cases'])}),flush=True)
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=runner_pin:
        raise RuntimeError('diagnostic runner source changed during execution')
    report=dict(version='wildfire-small-water-mixed-source-diagnostic-report-v4',
        role='private_unscored_passive_fire_only',input_identity=dict(inputs.identity),drafts_sha256=drafts['sha256'],
        runner_sha256=runner_pin,
        configuration=config,pressure_scale=1,scenario=a.scenario,crossing_proxy=float(a.crossing_proxy),cases=cases,
        limitations=['no scoring or operational routes','resources and population omitted',
            'weather beyond initial wind omitted','stops before first scheduled event',
            'fuel and crossing factors are uncalibrated exercise assumptions, not measurements'])
    report['sha256']=api._hash(report)
    write_private_json(out,report)


if __name__=='__main__':main()
