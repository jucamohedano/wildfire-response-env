"""Private synthetic crew walk-to-observed-front hypotheses; not operational access."""
import argparse
from collections import Counter
import hashlib
import heapq
import json
import math
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'envs/wildfire_response/core/src')]
from shapely.geometry import LineString
from eval import expanded_road_staging_v1 as staging
from eval import expanded_road_segment_exercise_v1 as segment
from eval.expanded_population_anchors_v1 import private_destination
from eval.expanded_road_runtime_v1 import ensure_new_output, write_private_bytes
from envs.wildfire_response.regional.ops.generator import digest

VERSION = 'expanded-crew-observed-front-walk-v1'
PARENT = Path('/tmp/expanded-wider-road-staging-v1-complete/candidate.json')
PARENT_PIN = '17b690b2e7cfbab71ae6d96ac993dd8d9aeb567d4d3657a1e00c640fda76c025'
OUT_NAME = re.compile(r'expanded-crew-walk-front-v1(?:-[a-z0-9][a-z0-9-]{0,63})?\Z')
MAX_WALK_M = 2000.0
MAX_GRADE = 0.30
WATER_BUFFER_M = 5.0
FRONT_STANDOFF_M = 10.0
DIRECTIONS = ((0, -1), (-1, 0), (1, 0), (0, 1))


def supported_land(v2, v4):
    """Only explicit v2 land with known source fuel and finite elevation."""
    supported, censored = staging.supported_cells(v2, v4)
    mixed = {(cell['column'], cell['row']) for cell in v4['cells']}
    return {p for p in supported if p not in censored and p not in mixed
            and v2['cells'][p[1]][p[0]]['surface'] == 'land'}


def hazards_and_front(fire, wind, supported):
    """Synthetic exposed-front profile, weaker than the existing router buffer."""
    if not isinstance(fire, (list, tuple)) or not fire:
        raise ValueError('nonempty partial public fire required')
    if (not isinstance(wind, (list, tuple)) or len(wind) != 2
            or any(type(x) not in (int, float) or not math.isfinite(x) for x in wind)):
        raise ValueError('finite observed wind required')
    if any(not isinstance(c, (list, tuple)) or len(c) != 2
           or any(type(x) is not int for x in c) for c in fire):
        raise ValueError('invalid observed fire cells')
    burning = {tuple(c) for c in fire}
    direction = tuple(0 if abs(x) < 1e-12 else (1 if x > 0 else -1) for x in wind)
    hazards = burning | {(x + dx, y + dy) for x, y in burning
                         for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                         if dx * direction[0] + dy * direction[1] > 0}
    # Cardinal face adjacency gives an actual segment on an observed burning
    # grid cell boundary, rather than declaring an arbitrary distant cell a site.
    front = {}
    for fx, fy in sorted(burning):
        for dx, dy in DIRECTIONS:
            cell = (fx + dx, fy + dy)
            if cell in supported and cell not in hazards:
                front.setdefault(cell, []).append((fx, fy))
    return hazards, front


def cell_of(position, grid):
    t = grid['transform']
    return (math.floor((position[0] - t[2]) / t[0]), math.floor((position[1] - t[5]) / t[4]))


def center(cell, grid):
    t = grid['transform']
    return (t[2] + (cell[0] + .5) * t[0], t[5] + (cell[1] + .5) * t[4])


def front_point(cell, fire, grid):
    c = center(cell, grid); f = center(fire, grid)
    dist = math.dist(c, f)
    # 10 m short of the shared boundary, still inside the non-burning cell.
    factor = (dist / 2 - FRONT_STANDOFF_M) / dist
    return (c[0] + (f[0] - c[0]) * factor, c[1] + (f[1] - c[1]) * factor)


def water_clear(a, b, water):
    if math.dist(a, b) < 1e-8:
        return True
    corridor = LineString([a, b]).buffer(WATER_BUFFER_M, cap_style=2)
    if not water['bounds'].covers(corridor) or not water['land'].covers(corridor):
        return False
    for name in ('osm', 'dera'):
        tree = water.get(name)
        if tree is None or len(tree.geometries) == 0:
            raise ValueError('required water evidence unavailable')
        if len(tree.query(corridor, predicate='intersects')):
            return False
    return True


def plan(position, fire, wind, supported, heights, grid, water, *, max_walk=MAX_WALK_M):
    hazards, front = hazards_and_front(fire, wind, supported)
    start = cell_of(position, grid)
    if start not in supported or start in hazards:
        return {'status': 'no_hypothesis_in_profile', 'reason': 'unsupported_or_excluded_start'}
    initial_center = center(start, grid)
    initial_distance = math.dist(position, initial_center)
    if initial_distance > max_walk or not water_clear(position, initial_center, water):
        return {'status': 'no_hypothesis_in_profile', 'reason': 'start_connector_fails_profile'}
    queue = [(initial_distance, start)]
    distances = {start: initial_distance}; parents = {}; water_cache = {}
    while queue:
        distance, cell = heapq.heappop(queue)
        if distance != distances[cell]:
            continue
        for observed in front.get(cell, []):
            point = front_point(cell, observed, grid)
            last = math.dist(center(cell, grid), point)
            if distance + last <= max_walk and water_clear(center(cell, grid), point, water):
                route = [cell]
                while route[-1] != start:
                    route.append(parents[route[-1]])
                route.reverse()
                return {'status': 'synthetic_walk_front_hypothesis', 'walk_cells': [list(p) for p in route],
                        'walk_distance_m': round(distance + last, 3), 'front_cell': list(observed),
                        'site_cell': list(cell), 'site_position': list(point),
                        'observed_fire_status': 'partial_handover_only', 'work_authorization': 'none',
                        'return_walk': 'requires_revalidation', 'road_return_to_base': 'unknown'}
        for dx, dy in DIRECTIONS:
            nxt = (cell[0] + dx, cell[1] + dy)
            if nxt not in supported or nxt in hazards:
                continue
            length = math.dist(center(cell, grid), center(nxt, grid))
            if abs(heights[cell[1]][cell[0]] - heights[nxt[1]][nxt[0]]) / length > MAX_GRADE:
                continue
            candidate = distance + length
            if candidate > max_walk or candidate >= distances.get(nxt, math.inf):
                continue
            key = tuple(sorted((cell, nxt)))
            if key not in water_cache:
                water_cache[key] = water_clear(center(cell, grid), center(nxt, grid), water)
            if not water_cache[key]:
                continue
            distances[nxt] = candidate; parents[nxt] = cell
            heapq.heappush(queue, (candidate, nxt))
    return {'status': 'no_hypothesis_in_profile', 'reason': 'no_front_within_supported_bounded_walk'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--opt-in-synthetic-front-walk', action='store_true')
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    if not args.opt_in_synthetic_front_walk:
        parser.error('explicit synthetic walk opt-in required')
    if (not args.output_dir.is_absolute() or args.output_dir.parent != Path('/tmp')
            or not OUT_NAME.fullmatch(args.output_dir.name)):
        parser.error('fresh dedicated /tmp directory required')
    ensure_new_output(args.output_dir)
    os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})
    parent = segment._pinned_doc(PARENT, PARENT_PIN)
    audit = segment._pinned_doc(staging.PRIVATE.parent / 'expanded-road-staging-v1-review-cache-fix' / 'case-audit.json',
                                '867b64973df6b8714d98ec85ecaf8c0455cedc4e073b550083b1ac895e0bc8ff')
    v2 = segment._pinned_doc(staging.PRIVATE/'resolution-v2.json', staging.V2_PIN)
    v4 = segment._pinned_doc(staging.PRIVATE/'small-water-exercise-v4.json', staging.V4_PIN)
    if len(parent['cases']) != 12 or len(audit['case_audits']) != 12:
        raise ValueError('expected twelve cases')
    source_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    grid = v2['grid']; heights = v2['terrain_candidate']; supported = supported_land(v2, v4)
    water = segment.load_water_sources(); cases = []
    for record, evidence in zip(parent['cases'], audit['case_audits']):
        if record['original_case_id'] != evidence['case_id']:
            raise ValueError('case mismatch')
        obs = evidence.get('public_observation')
        if obs is None:
            raise ValueError('public handover unavailable')
        view = {'id': record['fire_draft_id'], 'case_sha256': record['fire_draft_sha256'],
                'status': 'handover', 'diagnostic_identity_sha256': obs['diagnostic_identity_sha256'],
                'public_fire': obs['fire'], 'wind': obs['wind']}
        if digest(view) != record['handover_public_observation_sha256']:
            raise ValueError('public observation mismatch')
        results = []
        for variant in record['variants']:
            if (variant['version'] != 'expanded-wider-road-staging-sensitivity-v1'
                    or variant['sha256'] != digest({k:v for k,v in variant.items() if k != 'sha256'})):
                raise ValueError('invalid parent candidate')
            if variant['resource_kind'] != 'crew':
                results.append({'parent_sha256': variant['sha256'], 'status': 'engine_not_walk_eligible'})
                continue
            try:
                result = plan(variant['initial_position'], obs['fire'], obs['wind'], supported, heights, grid, water)
            except ValueError:
                result = {'status': 'unknown', 'reason': 'required_walk_evidence_unavailable'}
            result['parent_sha256'] = variant['sha256']; results.append(result)
        cases.append({'original_case_id': record['original_case_id'], 'candidates': results,
                      'parent_search_incomplete': record['search_incomplete']})
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != source_hash:
        raise ValueError('implementation changed during run')
    result = {'version': VERSION, 'status': 'private_synthetic_hypotheses_not_host_integrated',
              'pins': {'parent': PARENT_PIN, 'source': source_hash, 'water_v2': staging.V2_PIN, 'mixed_v4': staging.V4_PIN},
              'assumptions': {'max_walk_m': MAX_WALK_M, 'max_coarse_grade': MAX_GRADE,
                  'mapped_water_corridor_m': WATER_BUFFER_M, 'front_standoff_m': FRONT_STANDOFF_M,
                  'hazard_profile': 'partial observed fire and downwind cells excluded; adjacent upwind or lateral cells assumed eligible',
                  'land': 'v2 source-fuel land cells only; no mixed cells; unobserved microterrain unknown',
                  'policy': 'synthetic exercise assumption, weaker than existing router buffer; no work authorized'},
              'summary': {'statuses': dict(Counter(r['status'] for c in cases for r in c['candidates'])),
                          'cases_with_hypothesis': sum(any(r['status']=='synthetic_walk_front_hypothesis' for r in c['candidates']) for c in cases)},
              'cases': cases}
    result['sha256'] = digest(result)
    args.output_dir.mkdir(mode=0o700, exist_ok=False)
    write_private_bytes(private_destination(args.output_dir/'candidate.json'), json.dumps(result, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())
    print('Private crew walking hypotheses written.')


if __name__ == '__main__':
    main()
