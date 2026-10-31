"""Private initial-ignition screening for water-conflict v2 mixed hypotheses."""
import argparse
import json
from pathlib import Path

import adapter_v1 as api
from eval.extended_scenario_drafts_v1 import build_drafts
from .mixed_v2 import prepare_mixed_inputs, InteriorMixedFireV2
from .source_converter_v1 import _checked_document
from .private_output import private_destination, read_private_json, write_private_json


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('strict','water-v2','audit','hypotheses','parent','catalog','road',
                 'strict-sha256','water-v2-sha256','audit-sha256','hypotheses-sha256',
                 'parent-sha256','catalog-sha256','road-sha256','audit-trust-manifest','output'):
        p.add_argument('--'+name, required=True)
    p.add_argument('--opt-in-mixed-experiment',action='store_true')
    args=p.parse_args()
    out=private_destination(args.output)
    trusted_audit_manifest=read_private_json(args.audit_trust_manifest)
    values=[]
    for path in (args.strict,args.water_v2,args.audit,args.hypotheses,args.parent,args.catalog):
        if Path(path).is_symlink(): raise ValueError('input symlink is unsupported')
        with open(path) as f: values.append(json.load(f))
    strict,water,audit,hypotheses,parent,catalog=values
    parent=_checked_document(parent,args.parent_sha256,'incident-day-inputs-v2')
    catalog=_checked_document(catalog,args.catalog_sha256,'wildfire-incident-eval-catalog-v1')
    if catalog['inputs_sha256']!=args.parent_sha256 or catalog['network_sha256']!=parent['network_sha256']:
        raise ValueError('catalog/parent identities differ')
    drafts=build_drafts(catalog,parent['grid'],water['grid'])
    config=dict(work_gain=2,max_strength=5,decay=.5,wind_factor=1,report_delay=2,wear_factor=.1)
    all_results={}
    for tier in ('low','nominal','high'):
        inputs=prepare_mixed_inputs(strict,water,audit,hypotheses,strict_pin=args.strict_sha256,
            water_v2_pin=args.water_v2_sha256,audit_pin=args.audit_sha256,hypothesis_pin=args.hypotheses_sha256,
            trusted_audit_manifest=trusted_audit_manifest,
            road_path=args.road,road_pin=args.road_sha256,scenario=tier,
            opt_in_mixed_experiment=args.opt_in_mixed_experiment)
        cases=[]
        for case in drafts['cases']:
            f=InteriorMixedFireV2(inputs,seed=case['seed'],containment_config=config,pressure_scale=1,
                                  wind=case['wind'],opt_in_mixed_experiment=True)
            safe=f.change({'ignite':case['initial_fire']})
            cases.append(dict(case=case['id'],status='initial_supported_unrun' if safe else 'censored',
                              reasons=list(f.reasons),physical_steps=0))
        all_results[tier]=cases
    report=dict(version='wildfire-interior-mixed-preflight-v2',role='private_unscored_no_spread',
        strict=args.strict_sha256,water_v2=args.water_v2_sha256,audit=args.audit_sha256,
        hypotheses=args.hypotheses_sha256,road=args.road_sha256,parent=args.parent_sha256,
        catalog=args.catalog_sha256,drafts=drafts['sha256'],containment_config=config,pressure_scale=1,
        scenarios=all_results,limitations=['initial preflight only; no physical spread steps',
            'unvalidated water-edge geometry', 'all access unknown; no routing',
            'uncalibrated fractions are hypotheses, not measurements', 'no score'])
    report['sha256']=api._hash(report)
    write_private_json(out,report)
    print('Private screening finished.',flush=True)


if __name__=='__main__': main()
