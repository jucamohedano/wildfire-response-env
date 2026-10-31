"""Private opt-in, source-pinned road-segment water exercise candidate.

An explicitly synthetic no-unmapped-crossing assumption is restricted to
exact graph edges; this is not cell access, a walking rule or a safe route.
"""
import argparse
from collections import Counter
import copy
import hashlib
import json
import math
import os
import re
import subprocess
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'eval'))
sys.path.insert(0,str(ROOT/'envs/wildfire_response/core/src'))
from shapely.geometry import LineString, Point, box, mapping, shape
from shapely.ops import transform, unary_union
from shapely.strtree import STRtree
from pyproj import Transformer
import extended_map_resolved_v1 as maps
from eval.expanded_population_anchors_v1 import (load_pinned_inputs,audit_original_tiles,
    AnchorResolver,build_candidate,private_destination)
from eval.expanded_origin_guard_v1 import GuardedOriginRouter
from eval.expanded_guarded_routes_v1 import _verify_reviewed_pair, REVIEWED_PAIR_MANIFEST, TRUSTED_PAIR_SHA256
from wildfire_core.mobility_routing import make_journey, make_leg, point
from eval.expanded_road_runtime_v1 import ensure_new_output,write_private_bytes,strict_json
from eval.expanded_road_exercise_v1 import mode_path
from envs.wildfire_response.regional.ops.generator import digest

VERSION='expanded-road-segment-water-exercise-v1'
ANCHOR_PIN='5ea3fcb157146a61072dba4818c7b6d224a5242d50ed5efbd5a28fc822ce39d4'
GRAPH_PIN='97998cd2dbda86142d6f66d189c05e6b67d814f244c7fd0f37d729a15d98c525'
SOURCE_PIN='e9fb0ba0488a05f74947373872cae7ac83a2ac40a8465209e97910c7c26c35d1'
V4_PIN='c8972227b95e90a94f85d07ff6bd9df853fb0d5a046d938b8036ae48931e1afa'
OVERLAY_PIN='82bed516b5bd42ec4cd709035eacbd40c7f9cbf3a4fa6aa385ec5e6abc50c615'
V2_PIN='a44d432066d6c3039f49032ca21b8a82496dff47c855fee1e07f8e854ab15b87'
ANCHORS=Path('/tmp/expanded-population-anchors-v1-secure/candidate.json')
PRIVATE=Path('/tmp/wildfire-map-resolution-private')
V4=PRIVATE/'small-water-exercise-v4.json'
V2=PRIVATE/'resolution-v2.json'
MANIFEST=Path('/tmp/extended-access-lattice-v1/manifest.json')
SAMPLES=ROOT/'data/extended_access_v1/tile-acquisition.json'
OUT=Path('/tmp/expanded-road-segment-exercise-v1')
WATER_BUFFER_M=50.0
COAST_BUFFER_M=250.0
BOUNDS=(290000,4035000,332000,4064500)
LAND_CLASSES={10,20,30,40,50,60,100}
RASTER_PYTHON=Path('/home/juancm/miniconda3/envs/detectree/bin/python')


def _pinned_doc(path,expected):
    value=strict_json(path.read_bytes())
    if value.get('sha256')!=expected or digest({k:v for k,v in value.items() if k!='sha256'})!=expected:
        raise ValueError('private document pin mismatch')
    return value


def load_water_sources():
    """Reconstruct pinned, separate OSM and DERA observations and land faces."""
    for name in ('coast','water','worldcover'):
        maps.checked(PRIVATE/('worldcover-2021.tif' if name=='worldcover' else f'dera-{name}.geojson'),name)
    if maps.file_sha256(PRIVATE/'dera-rivers.geojson')!=maps.DERA_RIVER_PIN:
        raise ValueError('DERA rivers pin mismatch')
    features,stats,_=maps.load_osm_water(MANIFEST,SAMPLES,include_way_metadata=True)
    expected={'water_area_line_features':7,'water_area_polygon_features':271,
              'water_area_relation_features':3,'watercourse_line_features':2354}
    if stats!=expected: raise ValueError('OSM water feature inventory mismatch')
    osm=[geom for _,_,_,geom in features]
    dera_area=maps.collection(PRIVATE/'dera-water.geojson',94)
    dera_rivers=maps.collection(PRIVATE/'dera-rivers.geojson',410)
    water_doc=strict_json((PRIVATE/'dera-water.geojson').read_bytes())
    water_ids=[v.get('properties',{}).get('id_dera') for v in water_doc['features']]
    if any(type(v) is not int for v in water_ids) or len(set(water_ids))!=94:
        raise ValueError('DERA water area identities mismatch')
    if any(g.geom_type not in ('Polygon','MultiPolygon') for g in dera_area):
        raise ValueError('DERA water area type mismatch')
    if any(g.geom_type not in ('LineString','MultiLineString') or g.length<=0 for g in dera_rivers):
        raise ValueError('DERA river type mismatch')
    river_doc=strict_json((PRIVATE/'dera-rivers.geojson').read_bytes())
    ids=[v.get('properties',{}).get('id_dera') for v in river_doc['features']]
    if any(type(v) is not int for v in ids) or len(set(ids))!=410:
        raise ValueError('DERA river identities mismatch')
    area=box(*BOUNDS)
    coast_doc=strict_json((PRIVATE/'dera-coast.geojson').read_bytes())
    coast_ids=[v.get('properties',{}).get('id_dera') for v in coast_doc['features']]
    if any(type(v) is not int for v in coast_ids) or len(set(coast_ids))!=1580:
        raise ValueError('DERA coast identities mismatch')
    coast=unary_union([g for g in maps.collection(PRIVATE/'dera-coast.geojson',1580) if g.intersects(area)])
    dland,_,dc=maps.partition(coast,area,'DERA')
    osm_coast,diagnostic,_=maps.osm_line(MANIFEST,SAMPLES)
    oland,osea,oc=maps.partition(osm_coast,area,'OSM')
    for face,side in ((oland,'left'),(osea,'right')):
        hits=[entry for entry in diagnostic['faces'] if entry['side']==side
              and face.distance(Point(*entry['representative_m']))<.1]
        if len(hits)!=1:raise ValueError('OSM coastline side ambiguous')
    return {'osm':STRtree(osm),'dera':STRtree(dera_area+dera_rivers),
            'land':dland.intersection(oland),'shore':unary_union([dc,oc]),
            'bounds':area,'osm_counts':stats,'dera_counts':{'water_areas':94,'rivers':410}}


class _WorldCoverRaster:
    """Check all touched raster cells in a conservative, uncalibrated corridor."""
    def __init__(self,path):
        import rasterio
        self._env=rasterio.Env(PROJ_NETWORK='OFF',GDAL_PAM_ENABLED='NO');self._env.__enter__()
        try:
            self._dataset=rasterio.open(path)
            ds=self._dataset
            if (ds.crs.to_string()!='EPSG:4326' or ds.shape!=(36000,36000) or ds.count!=1
                or ds.dtypes!=('uint8',) or ds.nodata!=0 or tuple(ds.bounds)!=(-6.,36.,-3.,39.)):
                raise ValueError('WorldCover raster contract mismatch')
        except BaseException:
            self._env.__exit__(*sys.exc_info());raise
        self._transform=Transformer.from_crs(25830,4326,always_xy=True).transform

    def sample(self,corridor):
        from rasterio.features import geometry_window,geometry_mask
        ds=self._dataset;geographic=transform(self._transform,corridor)
        if geographic.is_empty or not box(*ds.bounds).covers(geographic):
            raise ValueError('WorldCover corridor outside source raster')
        window=geometry_window(ds,[mapping(geographic)])
        if window.width*window.height>1_000_000:
            raise ValueError('WorldCover corridor window exceeds bound')
        values=ds.read(1,window=window)
        mask=geometry_mask([mapping(geographic)],out_shape=values.shape,
                           transform=ds.window_transform(window),all_touched=True,invert=True)
        counts=Counter(map(int,values[mask]))
        if not counts: raise ValueError('WorldCover corridor has no samples')
        return dict(sorted(counts.items()))

    def close(self):
        self._dataset.close();self._env.__exit__(None,None,None)



class WorldCover:
    """Use the raster runtime without changing the pinned graph-audit PROJ."""
    def __init__(self,path):
        self.path=Path(path)
        if self.path!=PRIVATE/'worldcover-2021.tif' or not RASTER_PYTHON.is_file():
            raise ValueError('pinned WorldCover raster runtime unavailable')
        maps.checked(self.path,'worldcover')

    def sample(self,corridor):
        request=json.dumps(mapping(corridor),sort_keys=True,separators=(',', ':'),allow_nan=False)
        proc=subprocess.run([str(RASTER_PYTHON),str(Path(__file__).resolve()),'--worldcover-worker'],
            input=request,text=True,capture_output=True,timeout=120,
            env=dict(os.environ,PROJ_NETWORK='OFF',PYTHONDONTWRITEBYTECODE='1'))
        if proc.returncode!=0:raise ValueError('pinned WorldCover worker failed')
        counts=strict_json(proc.stdout)
        if not isinstance(counts,dict) or any(not k.isdigit() or type(v) is not int or v<=0 for k,v in counts.items()):
            raise ValueError('invalid WorldCover worker result')
        return {int(k):v for k,v in counts.items()}

    def close(self):
        pass


def _worldcover_worker():
    # A private pipe carries the corridor; coordinates never appear in argv.
    corridor=shape(strict_json(sys.stdin.read()))
    if corridor.is_empty or not corridor.is_valid or corridor.geom_type not in ('Polygon','MultiPolygon'):
        raise ValueError('invalid private corridor')
    minx,miny,maxx,maxy=corridor.bounds
    if not box(*BOUNDS).covers(corridor) or maxx-minx>5000 or maxy-miny>5000:
        raise ValueError('private corridor exceeds the bounded exercise scope')
    path=PRIVATE/'worldcover-2021.tif';maps.checked(path,'worldcover')
    raster=_WorldCoverRaster(path)
    try: result=raster.sample(corridor)
    finally:raster.close()
    print(json.dumps(result,sort_keys=True))


def edge_source_nodes(edge,source_ways,source_nodes):
    way=source_ways[edge['source_way']];step=1 if edge['direction']=='f' else -1
    seq=list(range(edge['first_segment'],edge['last_segment']+step,step))
    if not seq:raise ValueError('empty source lineage')
    ns=way['nodes'];ids=[ns[seq[0]] if step==1 else ns[seq[0]+1]]
    ids.extend(ns[i+1] if step==1 else ns[i] for i in seq)
    if (edge['coordinates']!=[source_nodes[n]['position_m'] for n in ids]
        or edge['source']!=str(ids[0]) or edge['target']!=str(ids[-1])):
        raise ValueError('edge lineage differs from source')
    return way,[source_nodes[n] for n in ids]


def screen_edge(edge,way,nodes,water,cover,mixed_cells,censored_cells):
    """Screen one edge; an accepted result is only a synthetic hypothesis."""
    if 'engine' not in edge.get('allowed_modes',[]):
        return {'status':'unknown','reason':'engine_mode_not_supported'}
    for tags in [way.get('tags',{})]+[n.get('tags',{}) for n in nodes]:
        if any(tags.get(k) not in (None,'no') for k in ('bridge','tunnel','ford')):
            return {'status':'unknown','reason':'tagged_structure_or_ford'}
    line=LineString(edge['coordinates'])
    if line.is_empty or not line.is_valid or not line.is_simple or line.length<=0:
        return {'status':'unknown','reason':'ambiguous_road_geometry'}
    corridor=line.buffer(WATER_BUFFER_M)
    if (not water['bounds'].covers(corridor) or not water['land'].covers(corridor)
        or water['shore'].distance(line)<=COAST_BUFFER_M+WATER_BUFFER_M):
        return {'status':'unknown','reason':'coast_or_land_face_ambiguous'}
    for name in ('osm','dera'):
        if len(water[name].geometries)==0:
            return {'status':'unknown','reason':name+'_water_layer_missing'}
        if len(water[name].query(line,predicate='dwithin',distance=WATER_BUFFER_M)):
            return {'status':'unknown','reason':name+'_mapped_water_or_buffer_conflict'}
    cells=[]
    minx,miny,maxx,maxy=line.bounds
    for row in range(max(0,int((4064500-maxy)//500)-1),min(59,int((4064500-miny)//500)+2)):
        for col in range(max(0,int((minx-290000)//500)-1),min(84,int((maxx-290000)//500)+2)):
            square=box(290000+col*500,4064500-(row+1)*500,290000+(col+1)*500,4064500-row*500)
            if line.intersects(square):cells.append((col,row))
    if not cells or any(cell not in mixed_cells or cell in censored_cells for cell in cells):
        return {'status':'unknown','reason':'outside_pinned_mixed_cell_scope'}
    # Extra 1 cm makes the raster check conservative across the distinct
    # graph and raster PROJ runtimes; neither distance is an error bound.
    histogram=cover.sample(corridor.buffer(0.01))
    if not histogram or not set(histogram)<=LAND_CLASSES or any(type(v) is not int or v<=0 for v in histogram.values()):
        return {'status':'unknown','reason':'worldcover_ambiguous_or_water'}
    distances={name:round(line.distance(water[name].geometries[int(water[name].nearest(line))]),3)
               for name in ('osm','dera')}
    return {'status':'assumed_eligible','reason':'two_mapped_water_screens_and_land_corrob',
        'nearest_mapped_water_m':distances,
        'water_screen_buffer_m':WATER_BUFFER_M,'coast_buffer_m':COAST_BUFFER_M,
        'raster_buffer_margin_m':0.01,
        'worldcover_classes':{str(k):v for k,v in histogram.items()},
        'touched_mixed_cells':len(cells),
        'confidence':'low_uncalibrated_synthetic_assumption',
        'unmapped_crossing':'assumed_absent_for_this_exact_edge_only',
        'flood_legal_vehicle_and_2021':'unknown'}


def screen_paths(cases,network,source,water,cover,mixed,censored,guard):
    ways={w['id']:w for w in source['ways']};nodes={n['id']:n for n in source['nodes']}
    edge_results={}; output=[]
    for case in cases:
        groups={g['id']:g['position'] for g in case['groups']}
        dests={d['id']:d['position'] for d in case['destinations']}
        for item in case['topology']:
            a=groups[item['group_id']];b=dests[item['destination_id']]
            record={'original_case_id':case['original_case_id'],'original_case_sha256':case['original_case_sha256'],
                    'group_id':item['group_id'],'destination_id':item['destination_id']}
            if any(x.get('status')!='source_supported_exercise_anchor' for x in (a,b)):
                record.update(status='unknown',reason='unresolved_anchor');output.append(record);continue
            if any(guard.assess_origin(x['source_position'])['status']!='unambiguous' for x in (a,b)):
                record.update(status='unknown',reason='ambiguous_endpoint');output.append(record);continue
            start,end=a['graph_node_id'],b['graph_node_id']
            record.update(source_node=start,target_node=end)
            if start==end:
                record.update(status='no_movement',edge_ids=[]);output.append(record);continue
            try: path=mode_path(network,start,end,'engine')
            except ValueError:
                record.update(status='unknown',reason='no_directed_exercise_path');output.append(record);continue
            if not path:
                record.update(status='unknown',reason='empty_path');output.append(record);continue
            for eid in path:
                if eid not in edge_results:
                    edge=network.edges[eid];way,node_records=edge_source_nodes(edge,ways,nodes)
                    screen=screen_edge(edge,way,node_records,water,cover,mixed,censored)
                    edge_results[eid]={'edge_id':eid,'source_way':edge['source_way'],
                        'first_segment':edge['first_segment'],'last_segment':edge['last_segment'],
                        'direction':edge['direction'],'source_node':edge['source'],'target_node':edge['target'],
                        'geometry_sha256':digest(edge['coordinates']),'exercise_engine_mode':('engine' in edge.get('allowed_modes',[])),
                        'way_tag_evidence':{k:way.get('tags',{}).get(k) for k in
                            ('highway','access','vehicle','motor_vehicle','motorcar','hgv',
                             'bridge','tunnel','ford','layer','surface','width','oneway')},
                        'node_structure_tag_counts':{k:sum(n.get('tags',{}).get(k) not in (None,'no')
                            for n in node_records) for k in ('bridge','tunnel','ford')},
                        'screen':screen}
            if all(edge_results[e]['screen']['status']=='assumed_eligible' for e in path):
                record.update(status='assumed_exercise_path',edge_ids=path)
            else:
                record.update(status='unknown',reason='at_least_one_edge_unresolved')
            output.append(record)
    return output,[edge_results[k] for k in sorted(edge_results)]


def make_candidate(cases,network,source,water,cover,mixed,censored,guard,*,opt_in=False):
    if opt_in is not True:raise ValueError('explicit no-unmapped-crossing exercise opt-in required')
    paths,edges=screen_paths(cases,network,source,water,cover,mixed,censored,guard)
    doc={'version':VERSION,'status':'private-unreviewed-exercise-hypothesis-not-operational',
        'assumption':{'version':'exact-edge-no-unmapped-crossing-v1',
            'scope':'only listed edges with assumed_eligible status and listed paths',
            'statement':'assume no unmapped water crossing on each screened exact segment',
            'confidence':'low_uncalibrated', 'flood':'unknown','legal':'unknown','vehicle_suitability':'unknown',
            'historical_2021_water_and_roads':'unknown','whole_cell_access':'unknown','walking_and_connectors':'not permitted',
            'mapped_water_buffer_m':WATER_BUFFER_M,'coastal_buffer_m':COAST_BUFFER_M,
            'buffers':'exercise screens, not positional-error or clearance bounds'},
        'pins':{'anchors_sha256':ANCHOR_PIN,'graph_sha256':GRAPH_PIN,'source_sha256':SOURCE_PIN,
            'reviewed_pair_sha256':TRUSTED_PAIR_SHA256,
            'source_compressed_sha256':REVIEWED_PAIR_MANIFEST['source_compressed_sha256'],
            'source_manifest_sha256':REVIEWED_PAIR_MANIFEST['source_manifest_sha256'],
            'original_catalog_sha256':'2b264d9668f0585a560d82af716f794ed386d6a7cdc713b24987269d647b33fc',
            'v4_sha256':V4_PIN,'water_v2_sha256':V2_PIN,'osm_manifest_sha256':maps.PINS['osm_manifest'],
            'osm_samples_sha256':maps.PINS['osm_samples'],'dera_coast_sha256':maps.PINS['coast'],
            'dera_water_sha256':maps.PINS['water'],'dera_rivers_sha256':maps.DERA_RIVER_PIN,
            'worldcover_sha256':maps.PINS['worldcover']},
        'coverage_caveats':['OSM map API can omit crossing ways; source is non-atomic',
            'DERA river layer contains identified named courses; stream width and survey uncertainty unknown',
            'WorldCover pixels are land-cover estimates, not surveyed water or clearance',
            'coast-face agreement is mapped evidence, not certified dry ground'],
        'edges':edges,'paths':paths}
    doc['sha256']=digest(doc);return doc



def _verify_overlay(overlay, _anchor=OVERLAY_PIN):
    # The public route adapter never accepts a caller-selected overlay pin.
    if (not isinstance(overlay,dict) or overlay.get('sha256')!=_anchor or
        digest({k:v for k,v in overlay.items() if k!='sha256'})!=_anchor or
        overlay.get('version')!=VERSION or
        overlay.get('status')!='private-unreviewed-exercise-hypothesis-not-operational'):
        raise ValueError('exercise overlay pin mismatch')
    return _anchor


class SegmentExerciseRoutes:
    """Opt-in exact-path exercise adapter; it does not modify any host or cell."""
    def __init__(self,graph,source,anchors,overlay,*,opt_in=False):
        if opt_in is not True:raise ValueError('explicit segment exercise opt-in required')
        _verify_reviewed_pair(graph,GRAPH_PIN,source,SOURCE_PIN)
        if (not isinstance(anchors,dict) or anchors.get('sha256')!=ANCHOR_PIN or
            digest({k:v for k,v in anchors.items() if k!='sha256'})!=ANCHOR_PIN):
            raise ValueError('anchor pin mismatch')
        overlay_pin=_verify_overlay(overlay)
        if (overlay.get('pins',{}).get('graph_sha256')!=GRAPH_PIN or
            overlay.get('pins',{}).get('source_sha256')!=SOURCE_PIN or
            overlay.get('pins',{}).get('anchors_sha256')!=ANCHOR_PIN):
            raise ValueError('overlay source pins mismatch')
        inventory={e['edge_id']:e for e in overlay['edges']}
        if len(inventory)!=len(overlay['edges']):raise ValueError('duplicate exercise edge')
        eligible={k for k,v in inventory.items() if v['screen']['status']=='assumed_eligible'}
        grid={'width':84,'height':59,'crs':'EPSG:25830','transform':[500,0,290000,0,-500,4064500]}
        zero=[[0]*84 for _ in range(59)];heights=[[None]*84 for _ in range(59)]
        self._guard=GuardedOriginRouter(graph,GRAPH_PIN,source,SOURCE_PIN,{'grid':grid},heights,
                                       walk_mask=zero,allowed_edge_ids=sorted(eligible))
        self._network=self._guard._router.network
        for eid,item in inventory.items():
            edge=self._network.edges.get(eid)
            if (edge is None or digest(edge['coordinates'])!=item['geometry_sha256'] or
                any(edge[k]!=item[k] for k in ('source_way','first_segment','last_segment','direction')) or
                edge['source']!=item['source_node'] or edge['target']!=item['target_node'] or
                item['exercise_engine_mode']!=('engine' in edge['allowed_modes'])):
                raise ValueError('exercise edge inventory mismatch')
        original={case['original_case_id']:case for case in anchors['cases']}
        records={}
        for record in overlay['paths']:
            key=(record['original_case_id'],record['group_id'],record['destination_id'])
            if key in records or key[0] not in original:raise ValueError('duplicate or unknown exercise path')
            case=original[key[0]]
            groups={g['id']:g['position'] for g in case['groups']}
            destinations={d['id']:d['position'] for d in case['destinations']}
            if (record['original_case_sha256']!=case['original_case_sha256'] or key[1] not in groups or
                key[2] not in destinations):raise ValueError('exercise case mapping mismatch')
            if record['status'] in ('assumed_exercise_path','no_movement'):
                a,b=groups[key[1]],destinations[key[2]]
                if (a['status']!='source_supported_exercise_anchor' or b['status']!='source_supported_exercise_anchor'
                    or record['source_node']!=a['graph_node_id'] or record['target_node']!=b['graph_node_id']):
                    raise ValueError('exercise endpoint mismatch')
                ids=record['edge_ids']
                if (record['status']=='no_movement') != (record['source_node']==record['target_node'] and ids==[]):
                    raise ValueError('invalid no-movement record')
                if record['status']=='assumed_exercise_path':
                    if not ids or any(e not in eligible for e in ids):raise ValueError('exercise path uses unknown edge')
                    cursor=record['source_node']
                    for eid in ids:
                        edge=self._network.edges[eid]
                        if edge['source']!=cursor or 'engine' not in edge['allowed_modes']:
                            raise ValueError('exercise path is not directed and continuous')
                        cursor=edge['target']
                    if cursor!=record['target_node']:raise ValueError('exercise path target differs')
            records[key]=copy.deepcopy(record)
        if len(records)!=48:raise ValueError('incomplete exercise path inventory')
        self._records=records
        self.identity={'version':VERSION,'component_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                       'overlay_sha256':overlay_pin,'source_sha256':SOURCE_PIN,
                       'graph_sha256':GRAPH_PIN,'anchors_sha256':ANCHOR_PIN,
                       'scope':'private opt-in synthetic assumption; not legal or operational access'}

    def evacuation_options(self,case_id,group_id,destination_id,origin,*,burning,wind,excluded_cells):
        """Return the exact screened road path only; caller supplies current hazards."""
        record=self._records.get((case_id,group_id,destination_id))
        if record is None or record['status']=='unknown':return {'status':'unmapped','options':[]}
        origin=point(origin);wind=point(wind)
        if not isinstance(burning,(list,tuple,set,frozenset)):
            raise ValueError('burning cells must be a reusable collection')
        router=self._guard._router;fire=router._excluded(burning);excluded=router._excluded(excluded_cells)
        start=record['source_node'];target=record['target_node']
        destination=self._network.nodes[target]
        if self._guard.assess_origin(origin)['status']=='ambiguous':
            return {'status':'unmapped','options':[],'reason':'ambiguous_source_origin'}
        if origin!=self._network.nodes[start]:
            return {'status':'unmapped','options':[],'reason':'origin_not_exact_anchored_node'}
        if record['status']=='no_movement':
            return {'status':'no_movement','options':[],
                    'scope':'same source node; no arrival or safety approval'}
        hazards=router._hazards(fire,wind) | excluded
        if router._cell(origin) in hazards or router._cell(destination) in hazards:
            return {'status':'blocked','options':[]}
        legs=[];cursor=start
        for eid in record['edge_ids']:
            edge=self._network.edges[eid]
            if (edge['source']!=cursor or 'engine' not in edge['allowed_modes'] or
                self._guard.assess_origin(self._network.nodes[edge['target']])['status']=='ambiguous' or
                not router._safe_line(edge['coordinates'],hazards,'engine')):
                return {'status':'blocked','options':[]}
            legs.append(make_leg('road',edge['coordinates'],edge['speed_kph'],eid,edge['target']))
            cursor=edge['target']
        if cursor!=target:raise ValueError('exercise path continuity changed')
        journey=make_journey(legs,origin,destination,
            'Synthetic exact-edge no-unmapped-crossing assumption; not verified access')
        journey.update(resource_mode='engine',access_node=target,access_point=destination[:],
                       access_distance_m=0,departed_at=0)
        option_id=digest({'version':VERSION,'case':case_id,'group':group_id,'destination':destination_id,
            'edges':record['edge_ids'],'burning':sorted(fire),'wind':wind,'excluded':sorted(excluded),
            'overlay':self.identity['overlay_sha256']})[:20]
        return {'status':'exercise_assumption_route',
                'options':[{'id':option_id,'journey':journey,'travel_minutes':journey['duration_minutes']}],
                'scope':'low-confidence synthetic assumption; flood, legal and real 2021 status unknown'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--opt-in-no-unmapped-crossings',action='store_true')
    parser.add_argument('--output-dir',type=Path,default=OUT)
    args=parser.parse_args()
    if not args.opt_in_no_unmapped_crossings:parser.error('explicit no-unmapped-crossing exercise opt-in required')
    if (not args.output_dir.is_absolute() or args.output_dir.parent!=Path('/tmp') or
        not (args.output_dir.name=='expanded-road-segment-exercise-v1' or
             re.fullmatch(r'expanded-road-segment-exercise-v1-[a-z0-9][a-z0-9-]{0,63}',args.output_dir.name))):
        parser.error('output must be a new dedicated directory directly under /tmp')
    ensure_new_output(args.output_dir)
    audit_original_tiles()
    catalog,old,snapshot,source,graph,_=load_pinned_inputs()
    anchors=_pinned_doc(ANCHORS,ANCHOR_PIN);v4=_pinned_doc(V4,V4_PIN);v2=_pinned_doc(V2,V2_PIN)
    if (v4.get('parent_v2_sha256')!=V2_PIN or v4.get('source_catalog_sha256')!=anchors['original_catalog_sha256']
        or v4.get('status')!='private_exercise_hypothesis_not_v1_input'
        or v2.get('status')!='private_evidence_not_simulator_input'):
        raise ValueError('mixed source lineage mismatch')
    grid={'width':84,'height':59,'crs':'EPSG:25830','transform':[500,0,290000,0,-500,4064500]}
    cells=[[0]*84 for _ in range(59)];heights=[[None]*84 for _ in range(59)]
    guard=GuardedOriginRouter(graph,GRAPH_PIN,source,SOURCE_PIN,{'grid':grid},heights,
                              walk_mask=cells,allowed_edge_ids=[e['id'] for e in graph['edges']])
    network=guard._router.network
    if build_candidate(catalog,AnchorResolver(old,snapshot,source,graph,guard),network)!=anchors:
        raise ValueError('anchors differ from complete source reconstruction')
    mixed={(v['column'],v['row']) for v in v4['cells']}
    censored={(v['column'],v['row']) for v in v4['additional_dera_censors']}
    if len(mixed)!=len(v4['cells']) or len(censored)!=len(v4['additional_dera_censors']) or mixed & censored:
        raise ValueError('mixed overlay is ambiguous')
    water=load_water_sources();cover=WorldCover(PRIVATE/'worldcover-2021.tif')
    try: doc=make_candidate(anchors['cases'],network,source,water,cover,mixed,censored,guard,opt_in=True)
    finally:cover.close()
    if doc['sha256']!=OVERLAY_PIN:
        raise ValueError('reconstructed candidate differs from fixed exercise pin; review required')
    args.output_dir.mkdir(mode=0o700,exist_ok=False)
    target=private_destination(args.output_dir/'candidate.json')
    write_private_bytes(target,json.dumps(doc,sort_keys=True,separators=(',', ':'),allow_nan=False).encode())
    print(json.dumps({'sha256':doc['sha256'],'status':doc['status'],'paths':dict(Counter(p['status'] for p in doc['paths'])),
                      'edges':dict(Counter(e['screen']['status'] for e in doc['edges']))},sort_keys=True))

if __name__=='__main__':
    if sys.argv[1:]==['--worldcover-worker']:_worldcover_worker()
    else:main()
