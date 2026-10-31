"""Private, bounded synthetic mixed-cell crew corridor hypotheses, not access."""
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

for _key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[_key] = '1'
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'envs/wildfire_response/core/src')]
from shapely.geometry import LineString, Point
from eval import expanded_crew_walk_front_v1 as walk
from eval import expanded_road_staging_v1 as staging
from eval import expanded_road_segment_exercise_v1 as segment
from eval.expanded_population_anchors_v1 import private_destination
from eval.expanded_road_runtime_v1 import ensure_new_output, write_private_bytes
from envs.wildfire_response.regional.ops.generator import digest

VERSION = 'expanded-crew-mixed-corridor-v1'
AUDIT_PIN = '867b64973df6b8714d98ec85ecaf8c0455cedc4e073b550083b1ac895e0bc8ff'
OUT_NAME = re.compile(r'expanded-crew-mixed-corridor-v1(?:-[a-z0-9][a-z0-9-]{0,63})?\Z')


def eligible_cells(v2, v4):
    """Source fuel and finite terrain, v2 land or explicitly admitted v4 mixed."""
    supported, censored = staging.supported_cells(v2, v4)
    mixed = {(item['column'], item['row']) for item in v4['cells']}
    return supported - censored, mixed


def touched_cells(a, b, grid):
    """Supercover of the exact centerline, including boundaries and endpoints."""
    t = grid['transform']
    start = ((a[0]-t[2])/t[0], (a[1]-t[5])/t[4])
    end = ((b[0]-t[2])/t[0], (b[1]-t[5])/t[4])
    times = {0., 1.}
    for axis in (0, 1):
        change = end[axis] - start[axis]
        if change:
            for bound in range(math.floor(min(start[axis], end[axis])) + 1,
                               math.ceil(max(start[axis], end[axis]))):
                times.add((bound-start[axis])/change)
    times = sorted(times)
    samples = times + [(a+b)/2 for a,b in zip(times,times[1:])]
    result = set()
    for t0 in samples:
        p = [start[k]+t0*(end[k]-start[k]) for k in (0, 1)]
        axes = []
        for value in p:
            near = round(value)
            axes.append((near-1,near) if abs(value-near)<1e-10 else (math.floor(value),))
        result.update((x,y) for x in axes[0] for y in axes[1])
    return result


def screen_segment(a, b, *, grid, supported, hazards, water):
    """Return eligible, excluded, or unknown; never infer a crossing."""
    if not touched_cells(a, b, grid) <= supported:
        return 'excluded', 'unsupported_centerline_cell'
    if touched_cells(a, b, grid) & hazards:
        return 'excluded', 'observed_fire_wind_guard'
    shape = (Point(a).buffer(walk.WATER_BUFFER_M) if math.dist(a,b)<1e-8 else
             LineString([a,b]).buffer(walk.WATER_BUFFER_M, cap_style=1))
    try:
        if not water['bounds'].covers(shape) or not water['land'].covers(shape):
            return 'unknown', 'land_face_unverified'
        for name in ('osm', 'dera'):
            tree = water.get(name)
            if tree is None or not len(tree.geometries):
                return 'unknown', name + '_water_layer_missing'
            for index in tree.query(shape):
                geom = tree.geometries[int(index)]
                if geom.is_empty or not geom.is_valid:
                    return 'unknown', name + '_water_geometry_invalid'
                if geom.intersects(shape):
                    return 'unknown', name + '_mapped_water_corridor_conflict'
    except (KeyError, TypeError, ValueError):
        return 'unknown', 'water_evidence_unavailable'
    return 'eligible', 'exact_corridor_passed'


def plan(position, fire, wind, supported, heights, grid, water, *, max_walk=walk.MAX_WALK_M):
    hazards, front = walk.hazards_and_front(fire, wind, supported)
    start = walk.cell_of(position, grid)
    reasons = Counter()
    incomplete = False
    def screen(a, b):
        nonlocal incomplete
        status, reason = screen_segment(a,b,grid=grid,supported=supported,hazards=hazards,water=water)
        if status != 'eligible': reasons[reason] += 1
        if status == 'unknown': incomplete = True
        return status == 'eligible'
    if start not in supported or start in hazards:
        return {'status':'no_hypothesis_in_profile','reason':'unsupported_or_excluded_start', 'screen_reasons':{}}
    first = walk.center(start, grid)
    initial_distance = math.dist(position, first)
    if initial_distance > max_walk:
        return {'status':'no_hypothesis_in_profile','reason':'start_connector_distance','screen_reasons':{}}
    if not screen(position, first):
        return {'status':'search_incomplete_unknown' if incomplete else 'no_hypothesis_in_profile',
                'reason':'start_connector_censored' if incomplete else 'start_connector_excluded',
                'screen_reasons':dict(sorted(reasons.items()))}
    queue = [(initial_distance, start)]; distances = {start:initial_distance}; parents = {}; cache = {}
    while queue:
        distance, cell = heapq.heappop(queue)
        if distance != distances[cell]: continue
        for observed in front.get(cell, []):
            point = walk.front_point(cell, observed, grid)
            length = math.dist(walk.center(cell,grid), point)
            if distance+length <= max_walk and screen(walk.center(cell,grid), point):
                route = [cell]
                while route[-1] != start: route.append(parents[route[-1]])
                route.reverse()
                return {'status':'synthetic_mixed_corridor_hypothesis', 'walk_cells':[list(p) for p in route],
                        'walk_distance_m':round(distance+length,3),'front_cell':list(observed),
                        'site_cell':list(cell),'site_position':list(point),'search_incomplete':incomplete,
                        'screen_reasons':dict(sorted(reasons.items())),
                        'work_authorization':'none','return_walk':'requires_revalidation',
                        'road_return_to_base':'unknown'}
        for dx,dy in walk.DIRECTIONS:
            nxt = (cell[0]+dx,cell[1]+dy)
            if nxt not in supported or nxt in hazards: continue
            length = math.dist(walk.center(cell,grid),walk.center(nxt,grid))
            if abs(heights[cell[1]][cell[0]]-heights[nxt[1]][nxt[0]])/length > walk.MAX_GRADE:
                reasons['coarse_grade_limit'] += 1; continue
            cost = distance + length
            if cost > max_walk:
                reasons['walking_distance_limit'] += 1; continue
            if cost >= distances.get(nxt, math.inf): continue
            key=tuple(sorted((cell,nxt)))
            if key not in cache: cache[key]=screen(walk.center(cell,grid),walk.center(nxt,grid))
            if not cache[key]: continue
            distances[nxt]=cost;parents[nxt]=cell;heapq.heappush(queue,(cost,nxt))
    return {'status':'search_incomplete_unknown' if incomplete else 'no_hypothesis_in_profile',
            'reason':'unresolved_corridor_evidence' if incomplete else 'no_front_within_bounded_profile',
            'screen_reasons':dict(sorted(reasons.items()))}


def later_crews(post):
    """Inventory later availability; no position, journey or access inferred."""
    result=[]
    for unit in post['resources']:
        available=unit.get('available_at');duty=unit.get('duty_end_at');work=unit.get('work_remaining')
        if (unit.get('kind')=='crew' and type(available) is int and available>0
                and type(duty) is int and duty>available and type(work) is int and work>0):
            result.append({'resource_id':unit['id'],'available_at':available,
                           'duty_end_at':duty,'position_at_availability':'unknown',
                           'route_to_staging':'unknown','work_authorization':'none'})
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--opt-in-synthetic-mixed-corridors',action='store_true')
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    if not args.opt_in_synthetic_mixed_corridors: parser.error('explicit synthetic corridor opt-in required')
    if not args.output_dir.is_absolute() or args.output_dir.parent!=Path('/tmp') or not OUT_NAME.fullmatch(args.output_dir.name):
        parser.error('fresh dedicated /tmp directory required')
    ensure_new_output(args.output_dir)
    if not hasattr(os,'sched_getaffinity'): raise ValueError('single CPU affinity unavailable')
    os.sched_setaffinity(0,{min(os.sched_getaffinity(0))})
    parent=segment._pinned_doc(walk.PARENT,walk.PARENT_PIN)
    audit=segment._pinned_doc(staging.PRIVATE.parent/'expanded-road-staging-v1-review-cache-fix'/'case-audit.json',AUDIT_PIN)
    v2=segment._pinned_doc(staging.PRIVATE/'resolution-v2.json',staging.V2_PIN)
    v4=segment._pinned_doc(staging.PRIVATE/'small-water-exercise-v4.json',staging.V4_PIN)
    posts=segment._pinned_doc(staging.POSTS,staging.POST_PIN)
    deps={'profile':Path(__file__),'walking':Path(walk.__file__),'staging':Path(staging.__file__),
          'road_water':Path(segment.__file__)}
    hashes={k:hashlib.sha256(p.read_bytes()).hexdigest() for k,p in deps.items()}
    if not all(len(v)==12 for v in (parent['cases'],audit['case_audits'],posts['cases'])):
        raise ValueError('expected twelve source cases')
    supported,mixed=eligible_cells(v2,v4);water=segment.load_water_sources();cases=[]
    for record,evidence,post in zip(parent['cases'],audit['case_audits'],posts['cases']):
        if (record['original_case_id']!=evidence['case_id'] or record['post_draft_sha256']!=post['sha256']
                or record['original_case_sha256']!=post['parent_scenario_sha256']):
            raise ValueError('case lineage mismatch')
        obs=evidence.get('public_observation')
        if not obs: raise ValueError('public handover unavailable')
        view={'id':record['fire_draft_id'],'case_sha256':record['fire_draft_sha256'],
              'status':'handover','diagnostic_identity_sha256':obs['diagnostic_identity_sha256'],
              'public_fire':obs['fire'],'wind':obs['wind']}
        if digest(view)!=record['handover_public_observation_sha256']:raise ValueError('public handover mismatch')
        results=[]
        for variant in record['variants']:
            if (variant['version']!='expanded-wider-road-staging-sensitivity-v1' or
                    variant['sha256']!=digest({k:v for k,v in variant.items() if k!='sha256'})):
                raise ValueError('invalid retained staging variant')
            result=plan(variant['initial_position'],obs['fire'],obs['wind'],supported,v2['terrain_candidate'],v2['grid'],water)
            result.update(parent_sha256=variant['sha256'],resource_kind=variant['resource_kind'],
                          applicability=('crew_synthetic_only' if variant['resource_kind']=='crew' else 'geometry_only_engine_cannot_walk'))
            if 'walk_cells' in result:
                result['mixed_cells_on_path']=sum(tuple(c) in mixed for c in result['walk_cells'])
            results.append(result)
        cases.append({'original_case_id':record['original_case_id'],'variants':results,
                      'parent_search_incomplete':record['search_incomplete'],'later_crews':later_crews(post)})
    if any(hashlib.sha256(p.read_bytes()).hexdigest()!=hashes[k] for k,p in deps.items()):
        raise ValueError('implementation changed during run')
    result={'version':VERSION,'status':'private_unreviewed_corridor_hypotheses_not_host_integrated',
        'pins':{'parent':walk.PARENT_PIN,'audit':AUDIT_PIN,'water_v2':staging.V2_PIN,
                'mixed_v4':staging.V4_PIN,'posts':staging.POST_PIN,'implementations':hashes},
        'assumptions':{'max_walk_m':walk.MAX_WALK_M,'max_grade':walk.MAX_GRADE,
            'water_corridor_m':walk.WATER_BUFFER_M,'front_standoff_m':walk.FRONT_STANDOFF_M,
            'mixed':'v4 mixed cells with source fuel and finite terrain may be crossed only along individually screened corridors',
            'hazard':'partial public handover fire and wind; weaker than existing router buffer',
            'crossings':'unknown or mapped-water conflicts remain censored',
            'limits':'synthetic only; no work authorization, route to staging or road return inferred'},
        'summary':{'variants':dict(Counter(r['status'] for c in cases for r in c['variants'])),
            'crew_hypotheses':sum(r['status']=='synthetic_mixed_corridor_hypothesis' and r['resource_kind']=='crew' for c in cases for r in c['variants']),
            'cases_with_crew_hypothesis':sum(any(r['status']=='synthetic_mixed_corridor_hypothesis' and r['resource_kind']=='crew' for r in c['variants']) for c in cases),
            'engine_geometry_hypotheses':sum(r['status']=='synthetic_mixed_corridor_hypothesis' and r['resource_kind']=='engine' for c in cases for r in c['variants']),
            'cases_with_later_crews':sum(bool(c['later_crews']) for c in cases)},'cases':cases}
    result['sha256']=digest(result)
    args.output_dir.mkdir(mode=0o700,exist_ok=False)
    write_private_bytes(private_destination(args.output_dir/'candidate.json'),json.dumps(result,sort_keys=True,separators=(',',':'),allow_nan=False).encode())
    print('Private mixed corridor hypotheses written.')


if __name__=='__main__':main()
