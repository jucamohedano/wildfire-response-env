#!/usr/bin/env python3
"""Private, source-reconstructed mixed-surface and estimated-fuel hypotheses.

This is an exercise evidence document, not an input contract or runtime adapter.
Targets are supplied through a separately pinned private file.
"""
import argparse
import hashlib
import json
import math
import os
import secrets
import stat
import sys
from collections import Counter
from pathlib import Path

import extended_map_resolved_v1 as source
from extended_map_common import digest, file_sha256, read_json, serialize

ROOT = Path(__file__).resolve().parents[1]
MAPPING_PATH = ROOT / 'envs/wildfire_response/core/src/envs/wildfire_response/regional/prepared/data.py'
MAPPING_PIN = '196ac0aad5daa0b654c53e219df7b00b0df75ffb015c2f66a4fb3636b489efbb'
VERSION = 'mixed-estimated-fuel-hypotheses-v1'


def _unique_json_object(pairs):
    value={}
    for key,item in pairs:
        if key in value: raise ValueError(f'duplicate JSON key: {key}')
        value[key]=item
    return value


def read_pinned_json(path, expected_sha, label):
    """Hash and parse exactly the same byte snapshot, without a second read."""
    raw=Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected_sha:
        raise ValueError(f'{label} hash mismatch')
    def invalid_constant(value):
        raise ValueError(f'invalid JSON number {value}')
    return json.loads(raw,object_pairs_hook=_unique_json_object,parse_constant=invalid_constant)


def read_pinned_collection(path, expected_sha, expected_count, label, *, include_features=False):
    obj=read_pinned_json(path,expected_sha,label)
    fs=obj.get('features') if isinstance(obj,dict) else None
    if (type(obj) is not dict or obj.get('type')!='FeatureCollection' or not isinstance(fs,list) or
        obj.get('numberMatched')!=expected_count or obj.get('numberReturned')!=expected_count or
        len(fs)!=expected_count or
        obj.get('crs',{}).get('properties',{}).get('name')!='urn:ogc:def:crs:EPSG::25830'):
        raise ValueError(f'{label} incomplete or unexpected feature collection')
    from shapely.geometry import shape
    geometries=[shape(feature['geometry']) for feature in fs]
    if any(g.is_empty or not g.is_valid for g in geometries):
        raise ValueError(f'{label} invalid geometry')
    return (fs,geometries) if include_features else geometries


def river_cells_from_verified_features(features, geometries, width, height, west, north):
    """Derive source observations from the same verified river snapshot."""
    from shapely.geometry import box
    from shapely.strtree import STRtree
    ids=[item.get('properties',{}).get('id_dera') for item in features]
    if (len(ids)!=len(geometries) or len(set(ids))!=len(ids) or
            any(type(i) is not int for i in ids) or
            any(g.geom_type not in ('LineString','MultiLineString') or g.length<=0 for g in geometries)):
        raise ValueError('unexpected verified DERA river identities or geometry')
    tree=STRtree(geometries); result={}
    for r in range(height):
        for c in range(width):
            cell=box(west+c*500,north-(r+1)*500,west+(c+1)*500,north-r*500)
            hits=[]
            for index in tree.query(cell,predicate='intersects'):
                i=int(index); props=features[i]['properties']
                hits.append({'id_dera':ids[i],'name':props.get('nombre'),
                             'acquired':'2026-10-30','source_epoch':'unverified'})
            if hits: result[(r,c)]=sorted(hits,key=lambda value:value['id_dera'])
    return result


def verified_osm_tiles(manifest_path, samples_path):
    """Read exactly pinned manifests and return every tile the loader will read."""
    manifest=read_pinned_json(manifest_path,source.PINS['osm_manifest'],'OSM manifest')
    samples=read_pinned_json(samples_path,source.PINS['osm_samples'],'OSM sample manifest')
    if (type(manifest) is not dict or manifest.get('status')!='complete_requests' or
        not manifest.get('started_at','').startswith('2026-') or
        type(manifest.get('tiles')) is not list or type(samples) is not dict or
        type(samples.get('tiles')) is not list):
        raise ValueError('unexpected OSM acquisition manifest')
    if any(type(tile) is not dict for tile in manifest['tiles']+samples['tiles']):
        raise ValueError('invalid OSM tile manifest entry')
    tiles=[tile for tile in manifest['tiles'] if tile.get('status')=='success']+samples['tiles']
    for tile in tiles:
        if (type(tile) is not dict or type(tile.get('path')) is not str or not tile['path'] or
            type(tile.get('sha256')) is not str):
            raise ValueError('invalid OSM tile manifest entry')
    return tiles


def snapshot_osm_tile_inputs(manifest_path, samples_path):
    """Pin both tile names and their resolved file identities for alias checks."""
    tiles=verified_osm_tiles(manifest_path,samples_path)
    paths=[Path(tile['path']) for tile in tiles]
    source.require_source_files(paths)
    result=[]
    for path in paths:
        resolved=path.resolve(strict=True)
        info=path.stat()
        if not stat.S_ISREG(info.st_mode): raise ValueError('OSM tile is not a regular file')
        result.append((path,resolved,info.st_dev,info.st_ino))
    return result


def verify_osm_tile_identities(snapshot):
    for path,resolved,device,inode in snapshot:
        try:
            current=path.resolve(strict=True); info=path.stat()
        except FileNotFoundError as error:
            raise ValueError('OSM tile identity changed or disappeared') from error
        if current!=resolved or (info.st_dev,info.st_ino)!=(device,inode):
            raise ValueError('OSM tile identity changed or disappeared')


def protected_osm_tile_paths(snapshot):
    return [item for path,resolved,_,_ in snapshot for item in (path,resolved)]


def load_pinned_osm_water(manifest_path, samples_path):
    """Reconstruct source OSM features using hash-checked bytes on every read."""
    from pyproj import Transformer
    from shapely.geometry import box,LineString
    from shapely.ops import polygonize_full,unary_union
    tiles=verified_osm_tiles(manifest_path,samples_path)
    nodes={}; ways={}; relations={}
    for tile in tiles:
        obj=read_pinned_json(tile['path'],tile['sha256'],'OSM water tile')
        for item in obj['elements']:
            typ=item.get('type')
            if typ=='node':
                record={key:item[key] for key in ('id','version','lon','lat') if key in item}
                if nodes.setdefault(item['id'],record)!=record:
                    raise ValueError('conflicting OSM water node')
            elif typ=='way' and source.osm_water_kind(item.get('tags',{})):
                record={key:item[key] for key in ('id','version','nodes','tags') if key in item}
                if ways.setdefault(item['id'],record)!=record:
                    raise ValueError('conflicting OSM water way')
            elif typ=='relation' and source.osm_water_kind(item.get('tags',{}))=='water_area':
                record={key:item[key] for key in ('id','version','members','tags') if key in item}
                if relations.setdefault(item['id'],record)!=record:
                    raise ValueError('conflicting OSM water relation')
    member_ids={member['ref'] for relation in relations.values()
                for member in relation['members'] if member['type']=='way'}
    members={}
    for tile in tiles:
        obj=read_pinned_json(tile['path'],tile['sha256'],'OSM water tile')
        for item in obj['elements']:
            if item.get('type')=='way' and item.get('id') in member_ids:
                record={key:item[key] for key in ('id','version','nodes') if key in item}
                if members.setdefault(item['id'],record)!=record:
                    raise ValueError('conflicting OSM water relation member')
    transform=Transformer.from_crs(4326,25830,always_xy=True).transform
    area=box(*source.base.BOUNDS); features=[]; stats=Counter()
    for way in sorted(ways.values(),key=lambda value:value['id']):
        kind,geometry,form=source.osm_water_geometry(way,nodes,transform)
        if not geometry.intersects(area): continue
        stats[kind+'_'+form+'_features']+=1
        features.append((way['id'],kind,form,geometry))
    for relation in sorted(relations.values(),key=lambda item:item['id']):
        if relation['tags'].get('type')!='multipolygon':
            raise ValueError('unsupported water relation type')
        outer=[]
        for member in relation['members']:
            if member['type']!='way' or member.get('role','') not in ('outer','inner',''):
                raise ValueError('unsupported OSM water relation member')
            way=members.get(member['ref'])
            if way is None or len(way['nodes'])<2 or any(n not in nodes for n in way['nodes']):
                raise ValueError('incomplete OSM water relation')
            if member.get('role','') in ('outer',''):
                outer.append(LineString([transform(nodes[n]['lon'],nodes[n]['lat']) for n in way['nodes']]))
        polygons,cuts,dangles,invalid=polygonize_full(unary_union(outer))
        if polygons.is_empty or not cuts.is_empty or not dangles.is_empty or not invalid.is_empty:
            raise ValueError('unclosed or invalid OSM water relation outer')
        geometry=unary_union(list(polygons.geoms))
        if not geometry.is_valid or geometry.is_empty:
            raise ValueError('invalid OSM water relation polygon')
        if geometry.intersects(area):
            stats['water_area_relation_features']+=1
            features.append((relation['id'],'water_area_relation','polygon',geometry))
    metadata={ident:{'version':way.get('version'),'tags':way['tags']} for ident,way in ways.items()}
    return features,dict(stats),metadata


def _check_output_inode(info, inputs):
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('output must be a regular file')
    if info.st_nlink!=1:
        raise ValueError('hardlinked output is not permitted')
    for item in inputs:
        try:
            if os.path.samestat(info,Path(item).stat()):
                raise ValueError('output inode aliases an input')
        except FileNotFoundError:
            continue


def check_private_output(path, inputs):
    """Reject existing hardlinks and input inode aliases, not just path aliases."""
    path=Path(path)
    source.require_private_path(path)
    root=source.PRIVATE_ROOT.resolve()
    if path.parent.resolve()!=root:
        raise ValueError('output must be a direct child of the dedicated private directory')
    source.reject_output_alias(path,inputs)
    try:
        info=path.lstat()
    except FileNotFoundError:
        return
    _check_output_inode(info,inputs)


def write_private_atomic(path, value, inputs, *, pre_replace_check=None):
    """Write a fresh private inode and replace only its directory entry."""
    path=Path(path)
    check_private_output(path,inputs)
    directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    name='.mixed-estimated-'+secrets.token_hex(16)+'.tmp'
    created=False
    try:
        parent=os.fstat(directory)
        if parent.st_uid!=os.geteuid() or stat.S_IMODE(parent.st_mode)&0o077:
            raise ValueError('dedicated private directory must be owner-only')
        fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
        created=True
        with os.fdopen(fd,'wb') as stream:
            os.fchmod(stream.fileno(),0o600)
            stream.write(serialize(value)); stream.flush(); os.fsync(stream.fileno())
            if os.fstat(stream.fileno()).st_nlink!=1:
                raise ValueError('temporary output inode became hardlinked')
        if pre_replace_check is not None:
            pre_replace_check()
        # Recheck path aliases even if the destination is currently absent.
        source.reject_output_alias(path,inputs)
        # Check the destination in the same opened directory; replacement never
        # truncates the old inode even if an alias appears after this check.
        try:
            existing=os.stat(path.name,dir_fd=directory,follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            _check_output_inode(existing,inputs)
        os.replace(name,path.name,src_dir_fd=directory,dst_dir_fd=directory)
        created=False
        os.fsync(directory)
    finally:
        if created:
            try: os.unlink(name,dir_fd=directory)
            except FileNotFoundError: pass
        os.close(directory)


def mapping_from_pinned_source():
    if file_sha256(MAPPING_PATH) != MAPPING_PIN:
        raise ValueError('exercise fuel mapping source hash mismatch')
    core = str(ROOT / 'envs/wildfire_response/core/src')
    if core not in sys.path:
        sys.path.insert(0, core)
    from envs.wildfire_response.regional.prepared import data
    if Path(data.__file__).resolve() != MAPPING_PATH.resolve():
        raise ImportError('unexpected fuel mapping module')
    if data.MAPPING_VERSION != 'wildfire-nffl-exercise-mapping-v1':
        raise ValueError('unexpected exercise fuel mapping version')
    return data.FUEL_MAPPING


def read_targets(path, expected_sha, width, height):
    source.require_private_path(path)
    obj=read_pinned_json(path,expected_sha,'private target file')
    if (type(obj) is not dict or set(obj)!={'version','cells'} or
        obj['version']!='private-spatial-targets-v1' or type(obj['cells']) is not list or
        not 1 <= len(obj['cells']) <= 100):
        raise ValueError('invalid private target document')
    result=[]; seen=set()
    for point in obj['cells']:
        if (type(point) is not list or len(point)!=2 or
            any(type(v) is not int for v in point) or
            not 0<=point[0]<width or not 0<=point[1]<height or tuple(point) in seen):
            raise ValueError('invalid or duplicate target cell')
        seen.add(tuple(point)); result.append(tuple(point))
    return result


def source_support(v2, v4):
    """Select original source samples, without using estimates as support."""
    h,w=v2['grid']['height'],v2['grid']['width']
    mixed={(cell['row'],cell['column']) for cell in v4['cells']}
    censored={(cell['row'],cell['column']) for cell in v4['additional_dera_censors']}
    if len(mixed)!=len(v4['cells']) or mixed & censored:
        raise ValueError('invalid v4 surface support')
    support={}
    for r in range(h):
        for c in range(w):
            cell=v2['cells'][r][c]
            status='v4_mixed_exercise' if (r,c) in mixed else (
                'v2_mapped_land' if cell['surface']=='land' and (r,c) not in censored else None)
            if status and cell['fuel_status']=='source':
                klass=cell['source_fuel_class']
                if type(klass) is not int or not 1<=klass<=10:
                    raise ValueError('invalid source fuel class')
                support[(r,c)]={'class':klass,'surface_basis':status}
    return support


def neighbor_positions(row, column, height, width):
    """All distinct source centers at 0 < Euclidean distance <= 1 km."""
    for dr in range(-2,3):
        for dc in range(-2,3):
            rr,cc=row+dr,column+dc
            distance2=dr*dr+dc*dc
            if 0<distance2<=4 and 0<=rr<height and 0<=cc<width:
                yield rr,cc,round(500*math.sqrt(distance2),3)


def decide(support, mapping):
    """Create explicitly synthetic class-and-fuel scenarios, or retain unknown."""
    votes=Counter(item['source_class'] for item in support)
    if len(support)<3:
        return {'status':'unknown','reason':'fewer_than_three_source_neighbors'}
    ranked=sorted(votes.items(),key=lambda item:(-item[1],item[0]))
    if len(ranked)>3 or (len(ranked)>1 and ranked[0][1]==ranked[1][1]) or ranked[0][1]/len(support)<0.5:
        return {'status':'unknown','reason':'ambiguous_or_tied_source_vote'}
    if any(k not in mapping for k in votes):
        raise ValueError('source class absent from fuel mapping')
    winner=ranked[0][0]; share=ranked[0][1]/len(support)
    low=min(votes,key=lambda k:(mapping[k]['initial_fuel'],-votes[k],k))
    high=min(votes,key=lambda k:(-mapping[k]['initial_fuel'],-votes[k],k))
    scenarios={}
    for label,klass in (('low',low),('nominal',winner),('high',high)):
        factor=source.EXERCISE_FACTORS[label]
        base=mapping[klass]['initial_fuel']
        scenarios[label]={'assumed_fuel_class':klass,'assumed_terrain':mapping[klass]['terrain'],
                          'fuel_factor_assumption':factor,'exercise_base_initial_fuel':base,
                          'modeled_initial_fuel':round(base*factor,6)}
    return {'status':'estimated_hypothesis' if share>=0.75 else 'ambiguous_sensitivity_hypothesis',
            'confidence':'low_uncalibrated','nominal_class_assumption':winner,
            'vote_counts':{str(k):votes[k] for k in sorted(votes)},
            'vote_share':round(share,6),'source_neighbor_count':len(support),
            'scenarios':scenarios,
            'limitations':'Alternatives and fuel factors are exercise assumptions; fuel ordering does not order fire outcomes.'}


def make_spatial_indexes(features, dera_water, dera_rivers):
    from shapely.strtree import STRtree
    areas=[g for _,kind,_,g in features if kind in ('water_area','water_area_relation')]+list(dera_water)
    osm_lines=[g for _,kind,_,g in features if kind=='watercourse']
    return tuple(STRtree(items) if items else None for items in (areas,osm_lines,list(dera_rivers)))


def neighbors_for(row, column, grid, support, indexes, *, withheld_quadrant=None):
    from shapely.geometry import LineString
    width,height=grid['width'],grid['height']; west,north=grid['transform'][2],grid['transform'][5]
    area_tree,osm_tree,dera_tree=indexes; samples=[]; blocked=0
    for rr,cc,distance in neighbor_positions(row,column,height,width):
        record=support.get((rr,cc))
        if record is None or (withheld_quadrant is not None and
                (int(rr>=height//2),int(cc>=width//2))==withheld_quadrant):
            continue
        line=LineString([(west+(column+.5)*500,north-(row+.5)*500),
                         (west+(cc+.5)*500,north-(rr+.5)*500)])
        if area_tree is not None and len(area_tree.query(line,predicate='intersects')):
            blocked+=1; continue
        osm_cross=bool(osm_tree is not None and len(osm_tree.query(line,predicate='crosses')))
        dera_cross=bool(dera_tree is not None and len(dera_tree.query(line,predicate='crosses')))
        samples.append({'row':rr,'column':cc,'distance_metres':distance,
            'source_class':record['class'],'surface_basis':record['surface_basis'],
            'crosses_osm_watercourse':osm_cross,'crosses_dera_watercourse':dera_cross})
    return samples,blocked


def source_holdout(grid, support, indexes, mapping, *, quadrant_blocked):
    """Known-source class agreement, not field accuracy or private-case tuning."""
    totals=Counter(); classes=Counter()
    for (row,col),known in sorted(support.items()):
        quadrant=(int(row>=grid['height']//2),int(col>=grid['width']//2)) if quadrant_blocked else None
        samples,_=neighbors_for(row,col,grid,support,indexes,withheld_quadrant=quadrant)
        result=decide(samples,mapping); totals['targets']+=1
        no_cross=[item for item in samples if not item['crosses_osm_watercourse'] and not item['crosses_dera_watercourse']]
        screened=decide(no_cross,mapping)
        if screened['status']!='unknown':
            totals['no_cross_vote_eligible']+=1
            totals['no_cross_vote_correct']+=int(screened['nominal_class_assumption']==known['class'])
        if result['status']=='unknown': continue
        totals['eligible']+=1; totals['correct']+=int(result['nominal_class_assumption']==known['class'])
        totals['alternative_contains_source']+=int(str(known['class']) in result['vote_counts'])
        group='strong_vote' if result['status']=='estimated_hypothesis' else 'ambiguous_vote'
        classes[group+'_eligible']+=1
        classes[group+'_correct']+=int(result['nominal_class_assumption']==known['class'])
    for name in ('targets','eligible','correct','alternative_contains_source','no_cross_vote_eligible','no_cross_vote_correct'):
        totals.setdefault(name,0)
    return {'counts':dict(totals),'vote_groups':dict(classes),
            'limits':'Spatial source-class agreement, not independent field truth; selected known cells may differ from missing cells.'}


def build_hypotheses(strict,v2,v4,interior,rivers,archive_samples,features,dera_water,dera_rivers,targets,mapping,source_pins):
    if v2.get('parent_document_sha256')!=strict['sha256'] or v4.get('parent_v2_sha256')!=v2['sha256']:
        raise ValueError('map lineage mismatch')
    grid=v2['grid']; support=source_support(v2,v4); indexes=make_spatial_indexes(features,dera_water,dera_rivers)
    records=[]
    for c,r in targets:
        original=strict['cells'][r][c]; current=v2['cells'][r][c]
        osm=current.get('osm_water_conflicts',[])
        area=[item for item in osm if item['kind'] in ('water_area','water_area_relation')]
        waterlines=[item for item in osm if item['kind']=='watercourse']
        hist=current['worldcover_samples']
        area_hit=False
        if indexes[0] is not None:
            from shapely.geometry import box
            west,north=grid['transform'][2],grid['transform'][5]
            square=box(west+c*500,north-(r+1)*500,west+(c+1)*500,north-r*500)
            area_hit=bool(len(indexes[0].query(square,predicate='intersects')))
        if (original['surface']!='land' or current['surface']!='unknown' or
            original['source_fuel_class'] is not None or current['source_fuel_class'] is not None or
            current['fuel_status']!='unknown' or not interior[r][c] or area or area_hit or not waterlines or
            sum(hist.values())!=2500 or not set(map(int,hist))<=source.LAND_CODES|{80} or
            hist.get('80',0)>25 or not source.vegetation_supported(hist) or
            v2['terrain_candidate'][r][c] is None):
            raise ValueError('target fails inland mixed-surface, source-fuel or terrain evidence gate')
        samples,blocked=neighbors_for(r,c,grid,support,indexes)
        assumption=decide(samples,mapping)
        no_cross=[item for item in samples if not item['crosses_osm_watercourse'] and not item['crosses_dera_watercourse']]
        strict_votes=dict(sorted(Counter(item['source_class'] for item in no_cross).items()))
        records.append({'column':c,'row':r,'surface':'mixed_exercise_assumption',
            'source_fuel_class':None,'full_archive_center_sample':archive_samples[r][c],
            'fuel_provenance':'source_unknown; separate synthetic spatial hypothesis',
            'surface_evidence':{'v1_surface':original['surface'],'v2_surface':current['surface'],
                'both_land_faces_and_coastal_water_polygon_screen':True,
                'osm_water_area_conflicts':len(area),'watercourse_evidence_present':True},
            'original_v1_estimate':original['estimated_fuel'],
            'worldcover_epoch':'2021','worldcover_samples':hist,
            'osm_watercourse_epoch':'2026','osm_watercourse_observations':waterlines,
            'dera_watercourses':rivers.get((r,c),[]),'dera_source_epoch':'unverified',
            'terrain_candidate':v2['terrain_candidate'][r][c],
            'neighbor_support':samples,'water_area_blocked_neighbors':blocked,
            'no_mapped_watercourse_crossing_votes':{str(k):v for k,v in strict_votes.items()},
            'no_mapped_watercourse_crossing_support_count':len(no_cross),
            'no_crossing_has_three_source_neighbors':len(no_cross)>=3,
            'fuel_hypothesis':assumption,
            'limitations':'Neighbor watercourse crossings and vegetation heterogeneity remain uncertain; no accessibility or crossing geometry is inferred.'})
    holdout={'leave_one_out':source_holdout(grid,support,indexes,mapping,quadrant_blocked=False),
             'quadrant_blocked':source_holdout(grid,support,indexes,mapping,quadrant_blocked=True)}
    result={'version':VERSION,'status':'private_sensitivity_evidence_not_runtime_input',
        'source_pins':source_pins,'grid':grid,
        'method':{'radius_metres':1000,'minimum_source_neighbors':3,
            'vote':'unweighted source-class majority; ties or >3 classes remain unknown',
            'strong_vote_threshold':0.75,'weak_vote_minimum':0.5,
            'water_area_path_policy':'exclude any center-to-center segment intersecting mapped water-area geometry',
            'watercourse_policy':'report geometric crossings; do not assume crossing or non-crossing proves fuel continuity',
            'factors':dict(source.EXERCISE_FACTORS),'mapping_version':'wildfire-nffl-exercise-mapping-v1'},
        'cells':records,'source_class_holdout':holdout,
        'limitations':'Synthetic exercise hypotheses; not observed 2021 fuel, calibrated ranges, route access, fire crossing or simulator readiness.'}
    result['sha256']=digest(result)
    return result


def reconstruct(args):
    """Rebuild v1 through v4 from their pinned original sources before using them."""
    parent=source.base.load_parent(args.parent)
    ref=source.base.inspect_boundaries(args.boundary_archive)
    samples=source.base.sample_fuel(args.fuel_archive,parent,width=source.base.WIDTH,height=source.base.HEIGHT,
        west=source.base.BOUNDS[0],north=source.base.BOUNDS[3],parent_row=source.base.ROW,parent_col=source.base.COL)
    map_doc=source.base.compile_map(parent,samples,ref)
    terrain=source.terrain.build(args.expanded,args.parent_sample)
    surface,cover,comparison,interior=source.surface_inputs(args.coast,args.water,args.worldcover,
        args.osm_manifest,args.osm_samples,include_interior=True)
    strict=source.compile_document(map_doc,terrain,surface,cover,comparison)
    source.checked_document(read_json(args.strict),args.strict_sha256,'strict')
    if serialize(strict)!=serialize(read_json(args.strict)): raise ValueError('strict source reconstruction differs')
    features,stats,metadata=load_pinned_osm_water(args.osm_manifest,args.osm_samples)
    conflicts=source.water_conflicts(features,source.base.WIDTH,source.base.HEIGHT,source.base.BOUNDS[0],source.base.BOUNDS[3])
    v2=source.compile_water_v2(strict,map_doc,terrain,surface,cover,comparison,conflicts,stats)
    source.checked_document(read_json(args.water_v2),args.water_v2_sha256,'water v2')
    if serialize(v2)!=serialize(read_json(args.water_v2)): raise ValueError('water v2 source reconstruction differs')
    catalog=read_json(args.catalog)
    v3=source.compile_exercise_v3(strict,v2,interior,catalog,parent)
    source.checked_document(read_json(args.exercise_v3),args.exercise_v3_sha256,'exercise v3')
    if serialize(v3)!=serialize(read_json(args.exercise_v3)): raise ValueError('exercise v3 source reconstruction differs')
    # Freshly read exact pinned snapshots are the sole source for both spatial
    # indexes and per-cell DERA river observations; no later file reopen is used.
    dera_water=read_pinned_collection(args.water,source.PINS['water'],94,'DERA water')
    river_features,dera_rivers=read_pinned_collection(args.dera_rivers,source.DERA_RIVER_PIN,410,'DERA rivers',include_features=True)
    river_cells=river_cells_from_verified_features(river_features,dera_rivers,source.base.WIDTH,source.base.HEIGHT,
        source.base.BOUNDS[0],source.base.BOUNDS[3])
    v4=source.compile_exercise_v4(v2,v3,interior,features,metadata,river_cells,catalog,parent)
    source.checked_document(read_json(args.exercise_v4),args.exercise_v4_sha256,'exercise v4')
    if serialize(v4)!=serialize(read_json(args.exercise_v4)): raise ValueError('exercise v4 source reconstruction differs')
    return strict,v2,v4,interior,river_cells,samples,features,dera_water,dera_rivers


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['compile','validate'])
    for key in ('parent','fuel-archive','boundary-archive','expanded','parent-sample','coast','water',
                'worldcover','osm-manifest','osm-samples','dera-rivers','catalog','strict','water-v2',
                'exercise-v3','exercise-v4','targets','output'):
        parser.add_argument('--'+key,type=Path,required=True)
    for key in ('strict-sha256','water-v2-sha256','exercise-v3-sha256','exercise-v4-sha256','targets-sha256'):
        parser.add_argument('--'+key,required=True)
    args=parser.parse_args()
    source.require_private_path(args.output)
    paths=[getattr(args,key.replace('-','_')) for key in ('parent','fuel-archive','boundary-archive','expanded',
           'parent-sample','coast','water','worldcover','osm-manifest','osm-samples','dera-rivers','catalog',
           'strict','water-v2','exercise-v3','exercise-v4','targets')]
    check_private_output(args.output,paths)
    source.require_source_files(paths+([args.output] if args.command=='validate' else []))
    tile_snapshot=snapshot_osm_tile_inputs(args.osm_manifest,args.osm_samples)
    protected_paths=paths+protected_osm_tile_paths(tile_snapshot)
    check_private_output(args.output,protected_paths)
    code_paths={'analysis':Path(__file__), 'map_compiler':Path(source.__file__),
        'common':ROOT/'eval/extended_map_common.py','map':Path(source.base.__file__),
        'terrain':Path(source.terrain.__file__),'access':Path(source.access.__file__),
        'domain':ROOT/'eval/extended_domain_v1.py','drafts':ROOT/'eval/extended_scenario_drafts_v1.py'}
    code_pins={name:file_sha256(path) for name,path in code_paths.items()}
    mapping=mapping_from_pinned_source()
    strict,v2,v4,interior,rivers,archive_samples,features,water,river_geoms=reconstruct(args)
    if code_pins!={name:file_sha256(path) for name,path in code_paths.items()}:
        raise ValueError('source compiler changed during reconstruction')
    targets=read_targets(args.targets,args.targets_sha256,v2['grid']['width'],v2['grid']['height'])
    pins={'strict':args.strict_sha256,'water_v2':args.water_v2_sha256,'exercise_v3':args.exercise_v3_sha256,
          'exercise_v4':args.exercise_v4_sha256,'private_targets_file_sha256':args.targets_sha256,
          'fuel_mapping_file_sha256':MAPPING_PIN,'dera_rivers_response_sha256':source.DERA_RIVER_PIN,
          'code_sha256':code_pins}
    result=build_hypotheses(strict,v2,v4,interior,rivers,archive_samples,features,water,river_geoms,targets,mapping,pins)
    if args.command=='compile':
        write_private_atomic(args.output,result,protected_paths,
                             pre_replace_check=lambda:verify_osm_tile_identities(tile_snapshot))
    else:
        verify_osm_tile_identities(tile_snapshot)
        check_private_output(args.output,protected_paths)
        if serialize(read_json(args.output))!=serialize(result):
            raise ValueError('mixed estimated-fuel hypothesis differs from source reconstruction')
    print({'sha256':result['sha256'],'candidate_count':len(result['cells']),'source_class_holdout':result['source_class_holdout']})

if __name__=='__main__': main()
