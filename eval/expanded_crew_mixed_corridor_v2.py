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
import subprocess
import sys

for _key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[_key] = '1'
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'eval'), str(ROOT / 'envs/wildfire_response/core/src')]
from shapely.geometry import LineString, Point, mapping, shape
import shapely
import pyproj
import extended_map_resolved_v1 as maps
from eval import expanded_crew_walk_front_v1 as walk
from eval import expanded_road_staging_v1 as staging
from eval import expanded_road_segment_exercise_v1 as segment
from eval.expanded_population_anchors_v1 import private_destination
from eval.expanded_road_runtime_v1 import ensure_new_output, write_private_bytes
from envs.wildfire_response.regional.ops.generator import digest

VERSION = 'expanded-crew-mixed-corridor-v2'
AUDIT_PIN = '867b64973df6b8714d98ec85ecaf8c0455cedc4e073b550083b1ac895e0bc8ff'
OUT_NAME = re.compile(r'expanded-crew-mixed-corridor-v2(?:-[a-z0-9][a-z0-9-]{0,63})?\Z')


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


def screen_segment(a, b, *, grid, supported, hazards, water, cover):
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
    try:
        histogram = cover.sample(shape)
        if (not isinstance(histogram, dict) or not histogram
                or any(type(k) is not int or type(v) is not int or v <= 0 for k,v in histogram.items())
                or not set(histogram) <= segment.LAND_CLASSES):
            return 'unknown', 'worldcover_water_or_unknown'
    except Exception:
        return 'unknown', 'worldcover_evidence_unavailable'
    return 'eligible', 'exact_corridor_passed'


def plan(position, fire, wind, supported, heights, grid, water, cover, *, max_walk=walk.MAX_WALK_M):
    hazards, front = walk.hazards_and_front(fire, wind, supported)
    start = walk.cell_of(position, grid)
    reasons = Counter()
    incomplete = False
    def screen(a, b):
        nonlocal incomplete
        status, reason = screen_segment(a,b,grid=grid,supported=supported,hazards=hazards,water=water,cover=cover)
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


def implementation_paths():
    # Bind the full local Python geometry/raster chain, not only its caller.
    names = ('expanded_crew_mixed_corridor_v2.py', 'expanded_crew_walk_front_v1.py',
             'expanded_road_staging_v1.py', 'expanded_road_segment_exercise_v1.py',
             'extended_map_resolved_v1.py', 'extended_map_common.py', 'extended_map_v2.py',
             'extended_map_terrain_v2.py', 'extended_access_v1.py', 'extended_domain_v1.py')
    return {name: ROOT/'eval'/name for name in names}


def implementation_hashes(paths):
    return {name:hashlib.sha256(Path(path).read_bytes()).hexdigest() for name,path in paths.items()}


def verify_implementation(expected, paths):
    if implementation_hashes(paths)!=expected:
        raise ValueError('geometry or raster implementation changed during run')


def source_receipts():
    """Hash exact pinned vector/raster files and every tile used by reconstruction."""
    files = {'coast':segment.PRIVATE/'dera-coast.geojson', 'water':segment.PRIVATE/'dera-water.geojson',
             'worldcover':segment.PRIVATE/'worldcover-2021.tif', 'osm_manifest':segment.MANIFEST,
             'osm_samples':segment.SAMPLES, 'dera_rivers':segment.PRIVATE/'dera-rivers.geojson'}
    values = {name:maps.file_sha256(path) for name,path in files.items()}
    if any(values[k]!=maps.PINS[k] for k in ('coast','water','worldcover','osm_manifest','osm_samples')):
        raise ValueError('pinned geometry or raster source changed')
    if values['dera_rivers']!=maps.DERA_RIVER_PIN:
        raise ValueError('pinned DERA rivers changed')
    manifest=maps.read_json(files['osm_manifest']);samples=maps.read_json(files['osm_samples'])
    tiles=[t for t in manifest['tiles'] if t.get('status')=='success']+samples['tiles']
    receipts=[]
    for tile in tiles:
        actual=maps.file_sha256(Path(tile['path']))
        if actual!=tile['sha256']:raise ValueError('pinned OSM tile changed')
        receipts.append(actual)
    values['osm_tile_count']=len(receipts)
    values['osm_tile_receipts_sha256']=digest(sorted(receipts))
    return values


def geometry_identity(water):
    """Bind effective inputs, including generated land face and both trees."""
    return {'osm':digest(sorted(g.wkb_hex for g in water['osm'].geometries)),
            'dera':digest(sorted(g.wkb_hex for g in water['dera'].geometries)),
            'land':hashlib.sha256(water['land'].wkb).hexdigest(),
            'bounds':hashlib.sha256(water['bounds'].wkb).hexdigest(),
            'shore':hashlib.sha256(water['shore'].wkb).hexdigest(),
            'shapely':shapely.__version__, 'geos':shapely.geos_version_string,
            'pyproj':pyproj.__version__, 'proj':pyproj.proj_version_str}


def raster_worker():
    """Private line-oriented pipe; use one pinned raster opened once."""
    import rasterio
    from rasterio import __gdal_version__, __proj_version__
    maps.checked(segment.PRIVATE/'worldcover-2021.tif','worldcover')
    raster=segment._WorldCoverRaster(segment.PRIVATE/'worldcover-2021.tif')
    try:
        print(json.dumps({'ready':True,'rasterio':rasterio.__version__,
              'gdal':__gdal_version__,'proj':__proj_version__}),flush=True)
        for line in sys.stdin:
            try:
                corridor=shape(json.loads(line))
                if corridor.is_empty or not corridor.is_valid or corridor.geom_type not in ('Polygon','MultiPolygon'):
                    raise ValueError('invalid corridor')
                counts=raster.sample(corridor)
                print(json.dumps({'counts':counts},sort_keys=True),flush=True)
            except Exception:
                print(json.dumps({'unknown':True}),flush=True)
    finally:
        raster.close()


class RasterWorker:
    """One raster process, with geometry sent privately over an anonymous pipe."""
    def __init__(self, hashes):
        if not segment.RASTER_PYTHON.is_file():raise ValueError('pinned raster runtime unavailable')
        self.proc=subprocess.Popen([str(segment.RASTER_PYTHON),str(Path(__file__).resolve()),'--private-raster-worker'],
            cwd=ROOT,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,
            env=dict(os.environ,PROJ_NETWORK='OFF',PYTHONDONTWRITEBYTECODE='1'))
        try:
            self.identity=json.loads(self.proc.stdout.readline())
            if self.identity.get('ready') is not True:raise ValueError('raster worker unavailable')
            self.identity['implementation_sha256']=hashes[Path(__file__).name]
        except Exception:
            self.proc.kill();self.proc.wait();raise ValueError('pinned raster worker unavailable')
    def sample(self, corridor):
        self.proc.stdin.write(json.dumps(mapping(corridor),separators=(',',':'),allow_nan=False)+'\n')
        self.proc.stdin.flush()
        answer=json.loads(self.proc.stdout.readline())
        if 'counts' not in answer:raise ValueError('raster evidence unavailable')
        return {int(k):v for k,v in answer['counts'].items()}
    def close(self):
        self.proc.stdin.close()
        if self.proc.wait(timeout=30)!=0:raise ValueError('raster worker failed')
        self.proc.stdout.close()
        return self.identity


def main():
    if sys.argv[1:] == ['--private-raster-worker']:
        raster_worker(); return
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
    deps=implementation_paths()
    hashes=implementation_hashes(deps)
    sources=source_receipts()
    if not all(len(v)==12 for v in (parent['cases'],audit['case_audits'],posts['cases'])):
        raise ValueError('expected twelve source cases')
    supported,mixed=eligible_cells(v2,v4);water=segment.load_water_sources();cases=[]
    geometry=geometry_identity(water)
    cover=RasterWorker(hashes)
    try:
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
                result=plan(variant['initial_position'],obs['fire'],obs['wind'],supported,v2['terrain_candidate'],v2['grid'],water,cover)
                result.update(parent_sha256=variant['sha256'],resource_kind=variant['resource_kind'],
                              applicability=('crew_synthetic_only' if variant['resource_kind']=='crew' else 'geometry_only_engine_cannot_walk'))
                if 'walk_cells' in result:
                    result['mixed_cells_on_path']=sum(tuple(c) in mixed for c in result['walk_cells'])
                results.append(result)
            cases.append({'original_case_id':record['original_case_id'],'variants':results,
                          'parent_search_incomplete':record['search_incomplete'],'later_crews':later_crews(post)})
    finally:
        cover_identity=cover.close()
    verify_implementation(hashes,deps)
    if source_receipts()!=sources or geometry_identity(water)!=geometry:
        raise ValueError('source evidence changed during run')
    result={'version':VERSION,'status':'private_unreviewed_corridor_hypotheses_not_host_integrated',
        'pins':{'parent':walk.PARENT_PIN,'audit':AUDIT_PIN,'water_v2':staging.V2_PIN,
                'mixed_v4':staging.V4_PIN,'posts':staging.POST_PIN,'implementations':hashes,
                'source_receipts':sources,'effective_geometry':geometry,'raster_runtime':cover_identity},
        'assumptions':{'max_walk_m':walk.MAX_WALK_M,'max_grade':walk.MAX_GRADE,
            'water_corridor_m':walk.WATER_BUFFER_M,'front_standoff_m':walk.FRONT_STANDOFF_M,
            'mixed':'v4 mixed cells with source fuel and finite terrain may be crossed only along individually screened corridors',
            'hazard':'partial public handover fire and wind; weaker than existing router buffer',
            'crossings':'unknown or mapped-water conflicts remain censored',
            'raster':'all touched WorldCover pixels must be in pinned land classes; water and unknown are censored',
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
