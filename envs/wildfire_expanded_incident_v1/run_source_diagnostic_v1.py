"""Run private, passive source-grounded diagnostics; never produce scores."""
import argparse
import json
from pathlib import Path

import adapter_v1 as api
from .host import SourceGroundedDiagnosticV1
from .source_converter_v1 import convert
from .private_output import private_destination, write_private_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ('resolution', 'parent', 'catalog', 'road', 'resolution-sha256',
                  'parent-sha256', 'catalog-sha256', 'road-sha256', 'output'):
        parser.add_argument('--' + field, required=True)
    parser.add_argument('--opt-in-source-grounded', action='store_true')
    args = parser.parse_args()
    path = private_destination(args.output)
    for path in (args.resolution, args.parent, args.catalog):
        if Path(path).is_symlink():
            raise ValueError('source document must not be a symlink')
    with open(args.resolution) as f:
        resolution = json.load(f)
    with open(args.parent) as f:
        parent = json.load(f)
    with open(args.catalog) as f:
        catalog = json.load(f)
    doc, evidence, drafts = convert(resolution, parent, catalog, road_path=args.road,
        expected_resolution_sha256=args.resolution_sha256, expected_parent_sha256=args.parent_sha256,
        expected_catalog_sha256=args.catalog_sha256, expected_road_sha256=args.road_sha256,
        opt_in_source_grounded=args.opt_in_source_grounded)
    config = dict(work_gain=2, max_strength=5, decay=.5, wind_factor=1, report_delay=2, wear_factor=.1)
    results = []
    for case in drafts['cases']:
        try:
            host = SourceGroundedDiagnosticV1(doc, expected_inputs_sha256=api._hash(doc),
                expected_source_pins=doc['source_pins'], evidence=evidence,
                expected_evidence_sha256=api._hash(evidence), drafts=drafts,
                expected_drafts_sha256=drafts['sha256'], case_id=case['id'],
                containment_config=config, pressure_scale=1, opt_in_source_grounded=True)
            while host.situation()['status'] == 'active':
                host.advance(60)
            state = host.situation()
            reasons = list(host._fire.reasons) if state['status'] == 'censored' else []
            blocker = ('fire_support_censor' if state['status'] == 'censored' else
                       'scheduled_event_or_weather_unsupported' if state['status'] == 'blocked' else None)
            result = dict(case=case['id'], status=state['status'], warmup_steps=state['warmup_steps'],
                          warmup_target=case['warmup_minutes'], minute=state['minute'],
                          blocker=blocker, censor_reasons=reasons)
        except Exception as exc:
            result = dict(case=case['id'], status='error', blocker=type(exc).__name__, detail=str(exc))
        results.append(result)
        print(json.dumps({'completed': len(results), 'total': len(drafts['cases'])}), flush=True)
    report = dict(version='wildfire-expanded-source-diagnostic-report-v1',
        role='private_unscored_passive_fire_only', source_converter=evidence['version'],
        source_resolution=args.resolution_sha256, source_catalog=args.catalog_sha256,
        source_parent=args.parent_sha256, source_road=args.road_sha256,
        input_sha256=api._hash(doc), draft_catalog_sha256=drafts['sha256'],
        evidence_sha256=api._hash(evidence), containment_config=config, pressure_scale=1,
        cases=results,
        limitations=['no rewards', 'source fuel only; estimated fuel remains unsupported',
                     'access unknown; road candidate not routed', 'resources and population omitted',
                     'stops before first scheduled event or weather change', 'unqualified drafts'])
    report['sha256'] = api._hash(report)
    write_private_json(path, report)


if __name__ == '__main__':
    main()
