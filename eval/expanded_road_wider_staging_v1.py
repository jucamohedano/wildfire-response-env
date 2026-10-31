"""Private sensitivity: wider synthetic predeployment staging, not work access."""
import argparse
from collections import Counter
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'envs/wildfire_response/core/src')]
from eval import expanded_predeployed_handover_v1 as pre
from eval import expanded_road_staging_v1 as staging
from eval import expanded_road_segment_exercise_v1 as segment
from eval.expanded_population_anchors_v1 import audit_original_tiles, load_pinned_inputs, private_destination
from eval.expanded_post_anchors_v2 import anchors, draft_catalog
from eval.expanded_origin_guard_v1 import GuardedOriginRouter
from eval.extended_scenario_drafts_v1 import build_drafts
from eval.expanded_road_runtime_v1 import ensure_new_output, write_private_bytes
from envs.wildfire_response.regional.ops.generator import digest

VERSION = 'expanded-wider-road-staging-sensitivity-v1'
SECTOR_RADIUS = 8.0
FIRE_RADIUS = 6.0
OUT_NAME = re.compile(r'expanded-wider-road-staging-v1(?:-[a-z0-9][a-z0-9-]{0,63})?\Z')


def wider_nodes(network, router, guard, public_fire, wind, sector, supported):
    """Relax only proximity heuristics; retain the observed-hazard and node guards."""
    if not public_fire:
        return []
    fire = router._excluded(public_fire)
    hazards = router._hazards(fire, wind)
    incoming = Counter(e['target'] for e in network.edges.values() if staging._has_both_modes(e))
    found = []
    for node, position in network.nodes.items():
        cell = router._cell(position)
        if (not router._inside(cell) or cell not in supported or cell in hazards
                or math.dist(cell, sector) > SECTOR_RADIUS):
            continue
        distance = min(math.dist(cell, p) for p in fire)
        if (distance > FIRE_RADIUS or not incoming[node]
                or not any(staging._has_both_modes(network.edges[e]) for e in network.adjacency[node])
                or guard.assess_origin(position)['status'] != 'unambiguous'):
            continue
        found.append((distance, math.dist(cell, sector), int(node), node))
    return [item[-1] for item in sorted(found)]


@contextmanager
def wider_selection():
    # candidate_case is reused unchanged, and the override is local to this
    # single-process diagnostic; always restore it even if screening fails.
    old = staging.candidate_nodes
    staging.candidate_nodes = wider_nodes
    try:
        yield
    finally:
        staging.candidate_nodes = old


def compare_case(original, firecase, post, public, network, router, guard, source,
                 water, cover, supported, censored, cache):
    with wider_selection():
        result = pre.candidate_case(original, firecase, post, public, network, router,
                                    guard, source, water, cover, supported, censored, cache)
    for variant in result['variants']:
        variant['version'] = VERSION
        variant['id'] += '-wider-staging-v1'
        variant['role'] = 'private_wider_sensitivity_not_runnable'
        variant['evidence']['proximity'] = 'wider synthetic heuristic; useful work reach unknown'
        variant['sha256'] = digest({k: v for k, v in variant.items() if k != 'sha256'})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--opt-in-wider-synthetic-staging', action='store_true')
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    if not args.opt_in_wider_synthetic_staging:
        parser.error('explicit wider synthetic staging opt-in required')
    if (not args.output_dir.is_absolute() or args.output_dir.parent != Path('/tmp')
            or not OUT_NAME.fullmatch(args.output_dir.name)):
        parser.error('fresh dedicated /tmp directory required')
    ensure_new_output(args.output_dir)
    if not hasattr(os, 'sched_getaffinity'):
        raise ValueError('single CPU affinity unavailable')
    os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
    baseline = segment._pinned_doc(pre.BASE / 'candidate.json', pre.BASE_CANDIDATE_PIN)
    audit = segment._pinned_doc(pre.BASE / 'case-audit.json', pre.BASE_AUDIT_PIN)
    if audit['candidate_sha256'] != pre.BASE_CANDIDATE_PIN:
        raise ValueError('baseline mismatch')
    deps = {'sensitivity': Path(__file__), 'predeployment': Path(pre.__file__),
            'staging': Path(staging.__file__), 'road_water': Path(segment.__file__),
            'origin_guard': ROOT / 'eval/expanded_origin_guard_v1.py',
            'source_drafts': ROOT / 'eval/extended_scenario_drafts_v1.py'}
    hashes = {k: hashlib.sha256(v.read_bytes()).hexdigest() for k, v in deps.items()}
    prior = baseline['pins']['source_implementations']
    if segment.WATER_BUFFER_M != 50.0:
        raise ValueError('baseline staging policy changed')
    if (hashes['staging'] != prior['tool'] or any(hashes[k] != prior[k]
            for k in ('road_water', 'origin_guard', 'source_drafts'))):
        raise ValueError('baseline source implementations changed')
    audit_original_tiles()
    catalog, _, snapshot, source, graph, _ = load_pinned_inputs()
    parent = segment._pinned_doc(staging.PARENT, staging.PARENT_PIN)
    v2 = segment._pinned_doc(staging.PRIVATE / 'resolution-v2.json', staging.V2_PIN)
    v4 = segment._pinned_doc(staging.PRIVATE / 'small-water-exercise-v4.json', staging.V4_PIN)
    drafts = build_drafts(catalog, parent['grid'], v2['grid'])
    posts = draft_catalog(catalog, anchors(catalog, source, snapshot), staging.GRAPH_PIN)
    if (drafts['sha256'] != staging.DRAFT_PIN or drafts != segment._pinned_doc(staging.DRAFTS, staging.DRAFT_PIN)
            or posts['sha256'] != staging.POST_PIN or posts != segment._pinned_doc(staging.POSTS, staging.POST_PIN)):
        raise ValueError('draft mismatch')
    # Reconstruct only the partial public observation from the pinned audit.
    # Its per-case digest binds it to the original handover without re-running
    # the private fire warmup or reading the complete fire.
    if len(audit['case_audits']) != 12 or len(drafts['cases']) != 12:
        raise ValueError('public audit count mismatch')
    views = []
    for fire, old, record in zip(drafts['cases'], baseline['cases'], audit['case_audits']):
        obs = record.get('public_observation')
        if not obs or record['case_id'] != old['original_case_id']:
            raise ValueError('pinned public handover unavailable')
        view = {'id': fire['id'], 'case_sha256': fire['sha256'], 'status': 'handover',
                'diagnostic_identity_sha256': obs['diagnostic_identity_sha256'],
                'public_fire': obs['fire'], 'wind': obs['wind']}
        if digest(view) != old['handover_public_observation_sha256']:
            raise ValueError('public handover mismatch')
        views.append(view)
    collections = (catalog['cases'], drafts['cases'], posts['cases'], views, baseline['cases'])
    if any(len(c) != 12 for c in collections):
        raise ValueError('expected twelve cases')
    supported, censored = staging.supported_cells(v2, v4)
    zeros = [[0] * 84 for _ in range(59)]
    guard = GuardedOriginRouter(graph, staging.GRAPH_PIN, source, staging.SOURCE_PIN,
        {'grid': drafts['grid']}, v2['terrain_candidate'], walk_mask=zeros,
        allowed_edge_ids=[e['id'] for e in graph['edges']])
    water = segment.load_water_sources()
    cover = segment.WorldCover(staging.PRIVATE / 'worldcover-2021.tif')
    cache, records = {}, []
    try:
        for original, fire, post, view, old in zip(*collections):
            if (fire['parent_scenario_sha256'] != original['sha256']
                    or post['parent_scenario_sha256'] != original['sha256']
                    or view['id'] != fire['id'] or view['case_sha256'] != fire['sha256']
                    or old['original_case_sha256'] != original['sha256']
                    or old['handover_public_observation_sha256'] != digest(view)):
                raise ValueError('case lineage mismatch')
            record = compare_case(original, fire, post, view, guard._router.network, guard._router,
                                  guard, source, water, cover, supported, censored, cache)
            record['baseline_status'] = old['status']
            records.append(record)
    finally:
        cover.close()
    if any(hashlib.sha256(p.read_bytes()).hexdigest() != hashes[k] for k, p in deps.items()):
        raise ValueError('implementation changed during search')
    result = {'version': VERSION, 'status': 'private_sensitivity_not_host_integrated',
        'assumptions': ['synthetic predeployment without a reconstructed journey',
            'no unmapped crossings only on screened edges',
            'wider proximity does not establish work reach, safe staging, or return'],
        'pins': {'baseline': pre.BASE_CANDIDATE_PIN, 'baseline_audit': pre.BASE_AUDIT_PIN,
                 'public_handover': baseline['pins']['public_handover'], 'implementations': hashes},
        'bounds': {'sector_radius_cells': SECTOR_RADIUS, 'public_fire_radius_cells': FIRE_RADIUS,
                   'nodes_per_case': pre.MAX_NODES, 'edge_screens': pre.MAX_SCREENS},
        'summary': {'statuses': dict(Counter(r['status'] for r in records)),
                    'incomplete': sum(r['search_incomplete'] for r in records)},
        'cases': records, 'edge_evidence': [cache[k] for k in sorted(cache)]}
    result['sha256'] = digest(result)
    args.output_dir.mkdir(mode=0o700, exist_ok=False)
    write_private_bytes(private_destination(args.output_dir / 'candidate.json'),
                        json.dumps(result, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())
    print('Private wider staging sensitivity written.')


if __name__ == '__main__':
    main()
