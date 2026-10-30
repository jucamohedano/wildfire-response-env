#!/usr/bin/env python3
"""Private, non-runtime surface and fuel-estimation evidence for the extended grid."""
import argparse
import sys
from collections import Counter
from pathlib import Path

from extended_map_common import digest, file_sha256, read_json, serialize, write_private_json
import extended_map_v2 as base
import extended_map_terrain_v2 as terrain
import extended_access_v1 as access

PINS = {
    'coast': '8be89addcb3dae3437b42dbb9e31720c22ec6fc5291fa5ee19595987d45741ce',
    'water': 'f5fa1249f0a8efbb90ec396c1a233cc48be8297801073fdfbf16cdb37e71b432',
    'worldcover': '224ffccb57011119e7395a2ff172f3b1b6059add8d12a4280ae1a994d0b71212',
    'osm_manifest': '96c5d0c165644911f20098fe37a51aa4431af388fbd573505bf1ffb509132076',
    'osm_samples': '9aadf4305d432ad675f4e6fcd272247ca1d4292ea95bf325a4f86f05c491b752',
}
LAND_CODES = {10, 20, 30, 40, 50, 60, 100}
VEGETATION_CODES = {'10', '20', '30', '40'}
BUFFER_METRES = 250
PRIVATE_ROOT = Path('/tmp/wildfire-map-resolution-private')


def checked(path, name):
    if file_sha256(path) != PINS[name]:
        raise ValueError(f'{name} hash mismatch')


def collection(path, expected):
    obj = read_json(path)
    fs = obj.get('features')
    if (obj.get('type') != 'FeatureCollection' or not isinstance(fs, list) or
        obj.get('numberMatched') != expected or obj.get('numberReturned') != expected or len(fs) != expected or
        obj.get('crs', {}).get('properties', {}).get('name') != 'urn:ogc:def:crs:EPSG::25830'):
        raise ValueError('incomplete or unexpected DERA feature collection')
    from shapely.geometry import shape
    geometries = [shape(f['geometry']) for f in fs]
    if any(g.is_empty or not g.is_valid for g in geometries):
        raise ValueError('invalid DERA geometry')
    return geometries


def partition(line, area, name):
    from shapely.geometry import Point
    from shapely.ops import polygonize, unary_union
    clip = line.intersection(area)
    if clip.is_empty or not clip.is_valid or not clip.is_simple:
        raise ValueError(f'{name} coast invalid')
    faces = list(polygonize(unary_union([area.boundary, clip])))
    if not faces or area.symmetric_difference(unary_union(faces)).area > 0.01:
        raise ValueError(f'{name} coast does not partition grid')
    # Fixed, cross-checked reference points far from the shoreline.
    def face_at(pt):
        matches = [f for f in faces if f.contains(Point(*pt))]
        if len(matches) != 1:
            raise ValueError(f'{name} ambiguous reference point')
        return matches[0]
    land = face_at((311000, 4064000))
    sea = face_at((331750, 4035250))
    if land.equals(sea):
        raise ValueError(f'{name} land and sea anchors share face')
    return land, sea, clip


def osm_line(manifest, samples):
    from pyproj import Transformer
    from shapely.geometry import LineString
    checked(manifest, 'osm_manifest'); checked(samples, 'osm_samples')
    nodes, ways, source_sha = access.load_surface_sources(manifest, samples)
    diagnostic = access.source_coastline_diagnostic(nodes, ways)
    starts = Counter(w['nodes'][0] for w in ways.values())
    ends = Counter(w['nodes'][-1] for w in ways.values())
    candidates = [n for n in starts if starts[n] == 1 and ends[n] == 0]
    if len(candidates) != 1:
        raise ValueError('OSM coast has ambiguous start')
    by_start = {w['nodes'][0]: w for w in ways.values()}
    n = candidates[0]; seq = [n]; visited = set()
    while n in by_start:
        way = by_start[n]
        if way['id'] in visited:
            raise ValueError('OSM coastline cycle')
        visited.add(way['id']); seq.extend(way['nodes'][1:]); n = way['nodes'][-1]
    if len(visited) != len(ways) or any(n not in nodes for n in seq):
        raise ValueError('OSM coastline missing ways or nodes')
    transform = Transformer.from_crs(4326, 25830, always_xy=True)
    line = LineString([transform.transform(nodes[n]['lon'], nodes[n]['lat']) for n in seq])
    return line, diagnostic, source_sha


def classify_cell(land_covers, sea_covers, excluded, classes):
    if excluded or not classes:
        return 'unknown'
    if land_covers and classes <= LAND_CODES:
        return 'land'
    if sea_covers and classes <= {0, 80}:
        return 'sea'
    return 'unknown'


def surface_inputs(coast_path, water_path, raster_path, manifest, samples, *, include_interior=False):
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.warp import reproject, Resampling
    from shapely.geometry import box, Point
    from shapely.ops import unary_union
    from shapely.prepared import prep
    for name, path in [('coast', coast_path), ('water', water_path), ('worldcover', raster_path)]:
        checked(path, name)
    area = box(*base.BOUNDS)
    coast = unary_union([g for g in collection(coast_path, 1580) if g.intersects(area)])
    waters = unary_union(collection(water_path, 94))
    dl, ds, dc = partition(coast, area, 'DERA')
    osm, diag, osm_sha = osm_line(manifest, samples)
    ol, os, oc = partition(osm, area, 'OSM')
    # Verify the OSM land-left/sea-right orientation of the chosen reference faces.
    for face, side in ((ol, 'left'), (os, 'right')):
        found = [entry for entry in diag['faces'] if entry['side'] == side and
                 face.distance(Point(*entry['representative_m'])) < .1]
        if len(found) != 1:
            raise ValueError('OSM reference side disagrees with directed coastline')
    land = prep(dl.intersection(ol)); sea = prep(ds.intersection(os))
    exclusion = prep(unary_union([dc, oc]).buffer(BUFFER_METRES).union(waters))
    # A fixed 10 m centre-sample lattice. Whole-cell claims are not implied.
    with rasterio.Env(PROJ_NETWORK='OFF', GDAL_PAM_ENABLED='NO'):
        with rasterio.open(raster_path) as src:
            if (src.crs.to_string() != 'EPSG:4326' or src.shape != (36000, 36000) or
                src.count != 1 or src.dtypes != ('uint8',) or src.nodata != 0 or
                tuple(src.bounds) != (-6., 36., -3., 39.)):
                raise ValueError('unexpected WorldCover raster')
            arr = np.zeros((base.HEIGHT*50, base.WIDTH*50), dtype='uint8')
            reproject(source=rasterio.band(src, 1), destination=arr, src_transform=src.transform,
                      src_crs=src.crs, src_nodata=0, dst_transform=from_origin(290000, 4064500, 10, 10),
                      dst_crs='EPSG:25830', dst_nodata=0, resampling=Resampling.nearest, num_threads=1)
    surface = []; covers = []; interior = []
    for r in range(base.HEIGHT):
        sr=[]; cr=[]; ir=[]
        for c in range(base.WIDTH):
            cell = box(290000+c*500, 4064500-(r+1)*500, 290000+(c+1)*500, 4064500-r*500)
            vals, counts = np.unique(arr[r*50:(r+1)*50,c*50:(c+1)*50], return_counts=True)
            hist = {str(int(v)):int(n) for v,n in zip(vals,counts)}
            classes=set(map(int, vals))
            kind=classify_cell(land.covers(cell),sea.covers(cell),exclusion.intersects(cell),classes)
            sr.append(kind); cr.append(hist); ir.append(land.covers(cell) and not exclusion.intersects(cell))
        surface.append(sr); covers.append(cr); interior.append(ir)
    comparison = {'osm_source_manifest_sha256':osm_sha, 'osm_diagnostic':diag,
        'coastline_hausdorff_metres':round(dc.hausdorff_distance(oc),3),
        'land_face_disagreement_square_metres':round(dl.symmetric_difference(ol).area,3),
        'sea_face_disagreement_square_metres':round(ds.symmetric_difference(os).area,3),
        'shoreline_buffer_metres':BUFFER_METRES}
    return (surface,covers,comparison,interior) if include_interior else (surface,covers,comparison)


def estimate(r, c, source, surface, exclude_quadrant=None):
    candidates = []
    h, w = len(surface), len(surface[0])
    for dr in range(-2,3):
        for dc in range(-2,3):
            rr, cc = r+dr, c+dc
            if (0 <= rr < h and 0 <= cc < w and 0 < dr*dr+dc*dc <= 4 and
                (exclude_quadrant is None or (int(rr >= h//2), int(cc >= w//2)) != exclude_quadrant) and surface[rr][cc] == 'land' and source[rr][cc] is not None):
                candidates.append((rr,cc,source[rr][cc]))
    counts = Counter(item[2] for item in candidates)
    if len(candidates) < 3:
        return None
    ranking = counts.most_common()
    code, votes = ranking[0]
    if votes / len(candidates) < .75 or (len(ranking)>1 and ranking[1][1] == votes):
        return None
    return {'class':code, 'method':'neighbour_majority_1000m_v1', 'confidence':'low_uncalibrated',
            'support_count':len(candidates), 'agreement':round(votes/len(candidates),6),
            'support': [{'row':rr,'column':cc,'source_class':v} for rr,cc,v in candidates]}


def vegetation_supported(histogram):
    return sum(histogram.get(code, 0) for code in VEGETATION_CODES) >= 2250


def compile_document(map_doc, terrain_doc, surface, cover, comparison):
    source=map_doc['source_fuel_class']; output=[]; summary=Counter(); holdout=Counter(); blocked=Counter()
    for r,row in enumerate(surface):
        result=[]
        for c,s in enumerate(row):
            val=source[r][c]
            # A contradictory source class prevents an unequivocal sea claim.
            if s == 'sea' and val is not None:
                s = 'unknown'; summary['sea_source_conflicts'] += 1
            record={'surface':s, 'worldcover_samples':cover[r][c], 'source_fuel_class':val,
                    'estimated_fuel':None, 'fuel_status':'source' if val is not None else 'unknown',
                    'fuel_provenance':{'kind':'effis_source_center_sample','confidence':'source_class_unverified'} if val is not None
                        else {'kind':'unknown','confidence':'unavailable'}}
            if s == 'land' and val is None and vegetation_supported(cover[r][c]):
                e=estimate(r,c,source,surface)
                if e is not None:
                    record['estimated_fuel']=e; record['fuel_status']='estimated'
                    record['fuel_provenance']={'kind':'spatial_estimate','confidence':'low_uncalibrated'}
            if s == 'land' and val is not None and vegetation_supported(cover[r][c]):
                e=estimate(r,c,source,surface)
                if e is not None:
                    holdout['eligible']+=1; holdout['correct']+=int(e['class']==val)
                quadrant=(int(r>=len(surface)//2), int(c>=len(row)//2))
                block_estimate=estimate(r,c,source,surface,exclude_quadrant=quadrant)
                if block_estimate is not None:
                    blocked['eligible']+=1; blocked['correct']+=int(block_estimate['class']==val)
            summary['surface_'+s]+=1; summary['fuel_'+record['fuel_status']]+=1
            if s == 'land' and val is None and record['estimated_fuel'] is None: summary['land_fuel_unknown']+=1
            result.append(record)
        output.append(result)
    doc={'version':'extended-map-resolution-evidence-v1', 'status':'private_evidence_not_simulator_input',
         'grid':map_doc['grid'], 'source_pins':{**PINS,'map_document_sha256':map_doc['sha256'],
          'terrain_document_sha256':terrain_doc['sha256']}, 'comparison':comparison,
         'cells':output, 'terrain_candidate':terrain_doc['elevation_candidate'],
         'terrain_zero_samples_per_cell':terrain_doc['zero_samples_per_cell'],
         'summary':dict(summary), 'leave_one_out_diagnostic':dict(holdout),
         'quadrant_blocked_diagnostic':dict(blocked),
         'limitations':'Land and sea are mapped evidence, not surveyed ground truth; unknown is censored; no access or runtime contract implied.'}
    doc['sha256']=digest(doc)
    return doc


def osm_water_kind(tags):
    if (tags.get('natural') in ('water', 'wetland') or
        tags.get('landuse') in ('reservoir', 'basin') or
        tags.get('waterway') == 'riverbank' or
        tags.get('water') not in (None, '', 'no')):
        return 'water_area'
    if tags.get('waterway') in ('river', 'stream', 'canal', 'drain', 'ditch'):
        return 'watercourse'
    return None


def osm_water_geometry(way, nodes, transform):
    from shapely.geometry import Polygon, LineString
    kind=osm_water_kind(way['tags'])
    ids=way['nodes']
    if not kind or len(ids)<2 or any(n not in nodes for n in ids):
        raise ValueError('water way has missing nodes or unsupported geometry')
    points=[transform(nodes[n]['lon'],nodes[n]['lat']) for n in ids]
    if kind=='water_area' and len(ids)>=4 and ids[0]==ids[-1]:
        geom=Polygon(points)
        if geom.is_empty or not geom.is_valid or geom.area<=0:
            raise ValueError('invalid OSM water polygon')
        return kind,geom,'polygon'
    geom=LineString(points)
    if geom.is_empty or not geom.is_valid or geom.length<=0:
        raise ValueError('invalid OSM water line')
    return kind,geom,'line'


def load_osm_water(manifest_path, samples_path, *, include_way_metadata=False):
    from pyproj import Transformer
    from shapely.geometry import box
    checked(manifest_path,'osm_manifest'); checked(samples_path,'osm_samples')
    manifest=read_json(manifest_path); samples=read_json(samples_path)
    if manifest.get('status')!='complete_requests' or not manifest.get('started_at','').startswith('2026-'):
        raise ValueError('unexpected OSM acquisition status or epoch')
    tiles=[t for t in manifest['tiles'] if t.get('status')=='success']+samples['tiles']
    nodes={}; ways={}; relations={}
    for tile in tiles:
        path=Path(tile['path'])
        if file_sha256(path)!=tile['sha256']:
            raise ValueError('OSM water tile hash mismatch')
        obj=read_json(path)
        for item in obj['elements']:
            typ=item.get('type')
            if typ=='node':
                record={k:item[k] for k in ('id','version','lon','lat') if k in item}
                old=nodes.setdefault(item['id'],record)
                if old!=record: raise ValueError('conflicting OSM water node')
            elif typ=='way' and osm_water_kind(item.get('tags',{})):
                record={k:item[k] for k in ('id','version','nodes','tags') if k in item}
                old=ways.setdefault(item['id'],record)
                if old!=record: raise ValueError('conflicting OSM water way')
            elif typ=='relation' and osm_water_kind(item.get('tags',{}))=='water_area':
                record={k:item[k] for k in ('id','version','members','tags') if k in item}
                old=relations.setdefault(item['id'],record)
                if old!=record: raise ValueError('conflicting OSM water relation')
    member_ids={member['ref'] for relation in relations.values() for member in relation['members'] if member['type']=='way'}
    members={}
    for tile in tiles:
        for item in read_json(tile['path'])['elements']:
            if item.get('type')=='way' and item.get('id') in member_ids:
                record={k:item[k] for k in ('id','version','nodes') if k in item}
                old=members.setdefault(item['id'],record)
                if old!=record: raise ValueError('conflicting OSM water relation member')
    transform=Transformer.from_crs(4326,25830,always_xy=True).transform
    area=box(*base.BOUNDS); features=[]; stats=Counter()
    for way in sorted(ways.values(),key=lambda v:v['id']):
        kind,geometry,form=osm_water_geometry(way,nodes,transform)
        if not geometry.intersects(area): continue
        stats[kind+'_'+form+'_features']+=1
        features.append((way['id'],kind,form,geometry))
    from shapely.geometry import LineString
    from shapely.ops import polygonize_full, unary_union
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
        # Fill holes too: a mapped island is uncertain, never asserted water.
        geometry=unary_union(list(polygons.geoms))
        if not geometry.is_valid or geometry.is_empty:
            raise ValueError('invalid OSM water relation polygon')
        if geometry.intersects(area):
            stats['water_area_relation_features']+=1
            features.append((relation['id'],'water_area_relation','polygon',geometry))
    if include_way_metadata:
        metadata={ident:{'version':way.get('version'),'tags':way['tags']} for ident,way in ways.items()}
        return features,dict(stats),metadata
    return features,dict(stats)


def water_conflicts(features, width, height, west, north):
    from shapely.geometry import box
    from shapely.strtree import STRtree
    if not features: return {}
    geoms=[item[3] for item in features]
    tree=STRtree(geoms)
    # Shapely 2 returns indices. Reject unknown API behavior rather than omit evidence.
    conflicts={}
    for r in range(height):
        for c in range(width):
            cell=box(west+c*500,north-(r+1)*500,west+(c+1)*500,north-r*500)
            ids=[]
            for index in tree.query(cell,predicate='intersects'):
                way,kind,form,geom=features[int(index)]
                ids.append({('relation_id' if kind=='water_area_relation' else 'way_id'):way,'kind':kind,'geometry':form,'epoch':'2026'})
            if ids: conflicts[(r,c)]=sorted(ids,key=lambda item:(item['kind'],item.get('way_id',item.get('relation_id'))))
    return conflicts


def compile_water_v2(strict, map_doc, terrain_doc, surface, cover, comparison, conflicts, stats):
    validate_document(strict,map_doc,terrain_doc,surface,cover,comparison)
    revised=[row[:] for row in surface]
    for r,c in conflicts:
        if revised[r][c] in ('land','sea'): revised[r][c]='unknown'
    result=compile_document(map_doc,terrain_doc,revised,cover,comparison)
    result['version']='extended-map-resolution-evidence-v2'
    result['parent_document_sha256']=strict['sha256']
    result['osm_water_policy']={'source_epoch':'2026','effect':'conflict_censors_cell',
        'limitations':'Community mapped, non-atomic and potentially incomplete; open water-area ways are line evidence only; relation holes conservatively censored; absent observations do not establish dry land.',
        'feature_statistics':stats,'conflicting_cells':len(conflicts),
        'strict_land_demoted':sum(strict['cells'][r][c]['surface']=='land' for r,c in conflicts),
        'strict_sea_demoted':sum(strict['cells'][r][c]['surface']=='sea' for r,c in conflicts)}
    for (r,c), evidence in conflicts.items():
        result['cells'][r][c]['osm_water_conflicts']=evidence
    result.pop('sha256'); result['sha256']=digest(result)
    return result


EXERCISE_CATALOG_PIN = '2b264d9668f0585a560d82af716f794ed386d6a7cdc713b24987269d647b33fc'
EXERCISE_FACTORS = {'low':0.8,'nominal':0.9,'high':1.0}


def select_exercise_mixed(cell, inland):
    """Return eligibility and a conservative reason; never promote v2 itself."""
    conflicts=cell.get('osm_water_conflicts',[])
    if cell['surface']!='unknown' or not any(x['kind']=='watercourse' for x in conflicts):
        return False,'not_unknown_linear_water'
    if any(x['kind'] in ('water_area','water_area_relation') for x in conflicts):
        return False,'osm_water_area_or_open_area_boundary'
    if not inland: return False,'not_corroborated_interior'
    if cell['fuel_status']!='source' or cell['source_fuel_class'] is None:
        return False,'no_source_fuel'
    hist=cell['worldcover_samples']
    if (sum(hist.values())!=2500 or not set(map(int,hist)) <= LAND_CODES|{80} or
            hist.get('80',0)>25 or not vegetation_supported(hist)):
        return False,'worldcover_mixed_or_major_water'
    return True,'eligible'


def exercise_neighborhoods(catalog, parent, v2, candidate_positions, extra_censored=frozenset()):
    from extended_scenario_drafts_v1 import _remap, _validate_grid
    checked_document(catalog,EXERCISE_CATALOG_PIN,'exercise catalog')
    if (catalog.get('version')!='wildfire-incident-eval-catalog-v1' or
            catalog.get('inputs_sha256')!=parent['sha256'] or
            catalog.get('network_sha256')!=parent['network_sha256'] or
            len(catalog.get('cases',[]))!=12):
        raise ValueError('unexpected exercise catalog or parent')
    grid=v2['grid']; _validate_grid(parent['grid']); _validate_grid(grid)
    h,w=grid['height'],grid['width']; outcomes=[]
    for i,case in enumerate(catalog['cases']):
        if case.get('id')!=f'incident-eval-v1-{i:03}' or case.get('sha256')!=digest({k:v for k,v in case.items() if k!='sha256'}):
            raise ValueError('exercise case identity or hash mismatch')
        burning={tuple(_remap(p,parent['grid'],grid)) for p in case['initial_fire']}
        stencil={(c+dc,r+dr) for c,r in burning for dc in (-1,0,1) for dr in (-1,0,1)}
        inside={(c,r) for c,r in stencil if 0<=c<w and 0<=r<h}
        counts=Counter()
        for c,r in inside:
            entry=v2['cells'][r][c]; mixed=(r,c) in candidate_positions
            if mixed and (r,c) in extra_censored: raise ValueError('mixed cell also marked as censored')
            surface='unknown' if (r,c) in extra_censored else entry['surface']
            if mixed:
                counts['mixed_exercise_cells']+=1
                if (c,r) in burning: counts['mixed_initial_fire_cells']+=1
            elif surface=='unknown': counts['unknown_surface_cells']+=1
            if surface=='sea':
                if (c,r) in burning: counts['sea_initial_fire_cells']+=1
                continue
            if surface=='land' or mixed:
                if entry['fuel_status']=='unknown': counts['fuel_unknown_cells']+=1
                if entry['fuel_status']=='estimated': counts['fuel_estimated_cells']+=1
                if v2['terrain_candidate'][r][c] is None: counts['terrain_unknown_cells']+=1
            if (c,r) in burning and surface=='unknown' and not mixed:
                counts['unknown_initial_fire_cells']+=1
        counts['boundary_contacts']=len(stencil-inside)
        for k in ('mixed_exercise_cells','mixed_initial_fire_cells','unknown_surface_cells','fuel_unknown_cells',
                  'fuel_estimated_cells','terrain_unknown_cells','sea_initial_fire_cells',
                  'unknown_initial_fire_cells','boundary_contacts'): counts.setdefault(k,0)
        blocked=any(counts[k] for k in ('unknown_surface_cells','fuel_unknown_cells',
                    'terrain_unknown_cells','sea_initial_fire_cells','boundary_contacts'))
        outcomes.append({'case_id':case['id'],'initial_fire_cells':len(burning),'stencil_cells':len(inside),**counts,
                         'fire_layer_stencil_complete_assuming_estimates':not blocked})
    return outcomes


def compile_exercise_v3(strict,v2,interior,catalog,parent):
    checked_document(strict,strict['sha256'],'strict')
    checked_document(v2,v2['sha256'],'water v2')
    if v2.get('parent_document_sha256')!=strict['sha256'] or v2['grid']!=strict['grid']:
        raise ValueError('exercise parent lineage mismatch')
    height=len(v2['cells']); width=len(v2['cells'][0])
    if len(interior)!=height or any(len(row)!=width for row in interior):
        raise ValueError('invalid interior evidence dimensions')
    records=[]; reasons=Counter()
    for r,row in enumerate(v2['cells']):
        for c,cell in enumerate(row):
            eligible,reason=select_exercise_mixed(cell,interior[r][c])
            if cell['surface']=='unknown' and any(x['kind']=='watercourse' for x in cell.get('osm_water_conflicts',[])):
                reasons[reason]+=1
            if not eligible: continue
            records.append({'column':c,'row':r,'surface':'mixed_exercise_assumption',
                'source_fuel_class':cell['source_fuel_class'],'fuel_provenance':cell['fuel_provenance'],
                'fuel_factor_assumptions':dict(EXERCISE_FACTORS),
                'worldcover_samples':cell['worldcover_samples'],
                'watercourse_observations':[x for x in cell['osm_water_conflicts'] if x['kind']=='watercourse'],
                'terrain_candidate_available':v2['terrain_candidate'][r][c] is not None,
                'confidence':'synthetic_uncalibrated',
                'limitations':'Fuel factors are assumptions, not mapped water area, stream width, crossing or physical calibration.'})
    positions={(x['row'],x['column']) for x in records}
    cases=exercise_neighborhoods(catalog,parent,v2,positions)
    summary={'mixed_exercise_cells':len(records),'eligibility_reasons':dict(sorted(reasons.items())),
        'cases':len(cases),'cases_fire_layer_stencil_complete':sum(x['fire_layer_stencil_complete_assuming_estimates'] for x in cases),
        'cases_with_remaining_unknown_surface':sum(x['unknown_surface_cells']>0 for x in cases),
        'cases_with_unknown_fuel':sum(x['fuel_unknown_cells']>0 for x in cases),
        'cases_with_estimated_fuel':sum(x['fuel_estimated_cells']>0 for x in cases),
        'cases_with_unknown_terrain':sum(x['terrain_unknown_cells']>0 for x in cases),
        'cases_with_boundary_contact':sum(x['boundary_contacts']>0 for x in cases)}
    result={'version':'extended-map-inland-mixed-exercise-v3',
        'status':'private_exercise_hypothesis_not_v1_input', 'parent_v2_sha256':v2['sha256'],
        'source_catalog_sha256':catalog['sha256'],'grid':v2['grid'],
        'policy':{'minimum_vegetation_samples':2250,'maximum_water_samples':25,
                  'shoreline_buffer_metres':BUFFER_METRES,'fuel_factor_assumptions':dict(EXERCISE_FACTORS),
                  'eligibility':'mapped inland watercourse, no mapped water-area intersection, corroborated land faces, source fuel',
                  'limitations':'Waterway widths, flow direction and fire crossings are not measured or calibrated; no assumed factor is inferred from line area.'},
        'cells':records,'cases':cases,'summary':summary}
    result['sha256']=digest(result)
    return result


DERA_RIVER_PIN = '6f26722f6c97bc5d0defc2f5e9245d39db0ba4f6816d89e0678dcda01adcc9d8'


def dera_river_conflicts(path, width, height, west, north):
    from shapely.geometry import box
    from shapely.strtree import STRtree
    if file_sha256(path)!=DERA_RIVER_PIN: raise ValueError('DERA river response hash mismatch')
    obj=read_json(path); geometries=collection(path,410); features=obj['features']
    ids=[f.get('properties',{}).get('id_dera') for f in features]
    if len(set(ids))!=410 or any(type(i) is not int for i in ids):
        raise ValueError('unexpected DERA river identities')
    if any(g.geom_type not in ('LineString','MultiLineString') or g.length<=0 for g in geometries):
        raise ValueError('unexpected DERA river geometry')
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


def assess_small_water_polygons(cell, refs, geometry_by_id, metadata):
    from shapely.ops import unary_union
    if not refs: return False,'no_water_area',None
    ids=[]; polygons=[]
    for ref in refs:
        if ref.get('kind')!='water_area' or ref.get('geometry')!='polygon' or 'way_id' not in ref:
            return False,'ambiguous_water_area_geometry',None
        ident=ref['way_id']; g=geometry_by_id.get(ident); meta=metadata.get(ident)
        if (g is None or meta is None or meta['tags'].get('natural')!='water' or
            g.geom_type!='Polygon' or not g.is_valid or not g.is_simple or
            len(g.interiors)>0 or not cell.contains(g) or cell.boundary.intersects(g)):
            return False,'ambiguous_water_area_geometry',None
        if ident in ids: return False,'ambiguous_water_area_geometry',None
        ids.append(ident); polygons.append(g)
    if any(a.intersects(b) for i,a in enumerate(polygons) for b in polygons[i+1:]):
        return False,'overlapping_or_touching_water_polygons',None
    area=unary_union(polygons).intersection(cell).area
    if not (0<area<0.05*cell.area): return False,'water_area_not_below_5_percent',None
    return True,'eligible',{'mapped_union_area_m2':round(area,6),
        'mapped_cell_fraction':round(area/cell.area,9),
        'polygons':[{'way_id':ident,'version':metadata[ident]['version'],
                     'tags':metadata[ident]['tags'],'mapped_area_m2':round(geometry_by_id[ident].area,6)}
                    for ident in sorted(ids)]}


def compile_exercise_v4(v2,v3,interior,features,metadata,river_cells,catalog,parent):
    from shapely.geometry import box
    import copy
    checked_document(v2,v2['sha256'],'water v2'); checked_document(v3,v3['sha256'],'exercise v3')
    if (v2.get('version')!='extended-map-resolution-evidence-v2' or
        v3.get('version')!='extended-map-inland-mixed-exercise-v3' or
        v3.get('parent_v2_sha256')!=v2['sha256'] or v3.get('grid')!=v2['grid']):
        raise ValueError('v4 parent lineage mismatch')
    height=len(v2['cells']); width=len(v2['cells'][0])
    if len(interior)!=height or any(len(row)!=width for row in interior):
        raise ValueError('invalid interior evidence dimensions')
    geometries={ident:geom for ident,kind,form,geom in features if kind=='water_area' and form=='polygon'}
    records=[copy.deepcopy(record) for record in v3['cells']]
    positions={(item['row'],item['column']) for item in records}
    if len(positions)!=len(records): raise ValueError('duplicate v3 cells')
    for item in records:
        item['exercise_basis']='v3_osm_watercourse'
        loc=(item['row'],item['column'])
        if loc in river_cells: item['dera_watercourses']=river_cells[loc]
    reasons=Counter(); new_area=0; new_dera_line=0
    for r,row in enumerate(v2['cells']):
        for c,cell in enumerate(row):
            pos=(r,c); river=river_cells.get(pos,[]); osm=cell.get('osm_water_conflicts',[])
            if pos in positions: continue
            has_osm_line=any(x['kind']=='watercourse' for x in osm)
            area_refs=[x for x in osm if x['kind'] in ('water_area','water_area_relation')]
            if not (river or has_osm_line) or (not area_refs and not river): continue
            # Only replace v2-unknown cells, or v2-land newly challenged by DERA.
            if cell['surface'] not in ('land','unknown') or (cell['surface']=='land' and not river): continue
            if not interior[r][c]: reasons['not_corroborated_interior']+=1; continue
            if cell['fuel_status']!='source' or cell['source_fuel_class'] is None:
                reasons['no_source_fuel']+=1; continue
            hist=cell['worldcover_samples']
            if (sum(hist.values())!=2500 or not set(map(int,hist))<=LAND_CODES|{80} or
                hist.get('80',0)>25 or not vegetation_supported(hist)):
                reasons['worldcover_mixed_or_major_water']+=1; continue
            area_evidence=None
            if area_refs:
                square=box(290000+c*500,4064500-(r+1)*500,290000+(c+1)*500,4064500-r*500)
                ok,reason,area_evidence=assess_small_water_polygons(square,area_refs,geometries,metadata)
                if not ok: reasons[reason]+=1; continue
                basis='small_osm_water_area_2026'; new_area+=1
            elif river:
                basis='additional_dera_watercourse'; new_dera_line+=1
            else: continue
            record={'column':c,'row':r,'surface':'mixed_exercise_assumption',
                'source_fuel_class':cell['source_fuel_class'],'fuel_provenance':cell['fuel_provenance'],
                'fuel_factor_assumptions':dict(EXERCISE_FACTORS),'worldcover_samples':hist,
                'worldcover_epoch':'2021','osm_water_epoch':'2026',
                'watercourse_observations':[x for x in osm if x['kind']=='watercourse'],
                'dera_watercourses':river,'terrain_candidate_available':v2['terrain_candidate'][r][c] is not None,
                'confidence':'synthetic_uncalibrated','exercise_basis':basis,
                'limitations':'Mapped geometry and selected factors do not measure 2021 water, width, flow, crossing, or burnable fraction.'}
            if area_evidence is not None: record['mapped_2026_water_area']=area_evidence
            records.append(record); positions.add(pos); reasons['eligible_'+basis]+=1
    records.sort(key=lambda value:(value['row'],value['column']))
    extra={}
    for (r,c),river in river_cells.items():
        if (r,c) not in positions and v2['cells'][r][c]['surface'] in ('land','sea'):
            extra[(r,c)]=river
    cases=exercise_neighborhoods(catalog,parent,v2,positions,frozenset(extra))
    summary={'mixed_exercise_cells':len(records),'inherited_v3_cells':len(v3['cells']),
        'new_small_water_area_cells':new_area,'new_dera_line_cells':new_dera_line,
        'additional_dera_censored_cells':len(extra),
        'dera_river_intersecting_cells':len(river_cells),
        'dera_conflict_v2_land_without_osm':sum(v2['cells'][r][c]['surface']=='land' and not v2['cells'][r][c].get('osm_water_conflicts') for r,c in river_cells),
        'eligibility_reasons':dict(sorted(reasons.items())),
        'cases':len(cases),'cases_fire_layer_stencil_complete':sum(x['fire_layer_stencil_complete_assuming_estimates'] for x in cases),
        'cases_with_remaining_unknown_surface':sum(x['unknown_surface_cells']>0 for x in cases)}
    doc={'version':'extended-map-small-water-exercise-v4','status':'private_exercise_hypothesis_not_v1_input',
        'parent_v2_sha256':v2['sha256'],'parent_v3_sha256':v3['sha256'],
        'dera_rivers_response_sha256':DERA_RIVER_PIN,'dera_rivers_acquired':'2026-10-30',
        'dera_rivers_source_epoch':'unverified','worldcover_epoch':'2021','osm_water_epoch':'2026',
        'source_catalog_sha256':catalog['sha256'],'grid':v2['grid'],
        'policy':{'maximum_mapped_water_fraction_exclusive':0.05,
            'area_topology':'valid disjoint whole polygons strictly within one cell; no holes, relations or open boundaries',
            'fuel_factor_assumptions':dict(EXERCISE_FACTORS),
            'limitations':'Area is mapped 2026 geometry, not proof of 2021 water. Fuel factors are synthetic and independent of area; widths, flow and fire crossings are uncalibrated.'},
        'cells':records,'additional_dera_censors':[{'row':r,'column':c,'dera_watercourses':refs} for (r,c),refs in sorted(extra.items())],
        'cases':cases,'summary':summary}
    doc['sha256']=digest(doc)
    return doc


def validate_document(candidate, map_doc, terrain_doc, surface, cover, comparison):
    if serialize(candidate) != serialize(compile_document(map_doc,terrain_doc,surface,cover,comparison)):
        raise ValueError('candidate differs from source reconstruction')



# Sensitivity-only assumptions, anchored to the existing exercise mapping.
# They are dimensionless initial-fuel parameters, not NFFL class numbers,
# measured fuel loads, or calibrated bounds.
SYNTHETIC = {'10': ('woodland', .9), '20': ('scrub', 1.0),
             '30': ('field', .75), '40': ('field', .75)}


def compile_sensitivity(strict):
    if strict.get('sha256') != digest({k:v for k,v in strict.items() if k != 'sha256'}):
        raise ValueError('strict candidate hash mismatch')
    records = []
    counts = Counter()
    totals = Counter()
    for r, row in enumerate(strict['cells']):
        for c, cell in enumerate(row):
            if cell['surface'] != 'land' or cell['fuel_status'] != 'unknown':
                continue
            hist = cell['worldcover_samples']
            classes = {key for key, count in hist.items() if count}
            vegetation = classes & set(SYNTHETIC)
            # Single mapped vegetation category, at least 90%, remaining samples
            # bare only. Any built-up, mixed vegetation or other class stays unknown.
            if len(vegetation) != 1 or not classes <= vegetation | {'60'}:
                reason = 'built_up' if '50' in classes else 'mixed_or_unmapped'
                counts['unresolved_'+reason] += 1
                continue
            code = next(iter(vegetation))
            if hist[code] < 2250:
                counts['unresolved_mixed_or_unmapped'] += 1
                continue
            kind, baseline = SYNTHETIC[code]
            low, high = round(baseline * .5, 3), round(min(1.0, baseline * 1.5), 3)
            records.append({'row':r, 'column':c, 'worldcover_class':int(code),
                            'terrain_assumption':kind, 'kind':'synthetic_sensitivity_only',
                            'confidence':'not_calibrated', 'initial_fuel_low':low,
                            'initial_fuel_high':high, 'baseline':baseline,
                            'worldcover_support_samples':hist[code]})
            counts['synthetic_parameterized'] += 1
            counts['class_'+code] += 1
            totals['low'] += low; totals['high'] += high
    counts['land_gaps_after_strict'] = sum(v for k,v in counts.items() if k.startswith('unresolved_')) + counts['synthetic_parameterized']
    totals = {k:round(v,3) for k,v in totals.items()}
    if strict.get('version')=='extended-map-resolution-evidence-v2':
        counts.setdefault('synthetic_parameterized',0)
    doc={'version':'extended-map-synthetic-sensitivity-v1',
         'status':'private_sensitivity_assumptions_not_simulator_input',
         'strict_candidate_sha256':strict['sha256'], 'cells':records, 'summary':dict(counts),
         'parameter_sum_sensitivity':totals,
         'limitations':'Synthetic local exercise parameters only; not measured, calibrated, ordered fire outcomes or a simulation.'}
    if strict.get('version')=='extended-map-resolution-evidence-v2':
        doc['version']='extended-map-synthetic-sensitivity-v2'
    doc['sha256']=digest(doc)
    return doc


def validate_sensitivity(candidate, strict):
    if serialize(candidate) != serialize(compile_sensitivity(strict)):
        raise ValueError('sensitivity differs from reconstruction')



def build_aggregate(strict, sensitivity, archive_audit, water_v2, sensitivity_v2, exercise_v3, exercise_v4):
    # All arguments must first be independently reconstructed from pinned inputs.
    counts=Counter((cell['surface'],cell['fuel_status']) for row in strict['cells'] for cell in row)
    cross={'land_source':counts['land','source'],'land_estimated':counts['land','estimated'],
           'land_unknown':counts['land','unknown'],'sea_unknown':counts['sea','unknown'],
           'unknown_surface_source':counts['unknown','source'],'unknown_surface_unknown':counts['unknown','unknown']}
    archive={k:v for k,v in archive_audit.items() if k not in ('version','status','strict_candidate_sha256')}
    return {'version':'extended-map-resolution-aggregate-v1','status':strict['status'],
        'private_candidate_sha256':strict['sha256'],'source_pins':strict['source_pins'],
        'summary':strict['summary'],'surface_fuel_cross_counts':cross,
        'leave_one_out_diagnostic':strict['leave_one_out_diagnostic'],
        'quadrant_blocked_diagnostic':strict['quadrant_blocked_diagnostic'],
        'comparison':{k:v for k,v in strict['comparison'].items() if k!='osm_diagnostic'},
        'terrain_candidate_values':sum(v is not None for row in strict['terrain_candidate'] for v in row),
        'terrain_cells_with_zero':sum(v>0 for row in strict['terrain_zero_samples_per_cell'] for v in row),
        'synthetic_sensitivity':{'private_document_sha256':sensitivity['sha256'],
            'strict_candidate_sha256':sensitivity['strict_candidate_sha256'],
            'summary':sensitivity['summary'],'parameter_sum_sensitivity':sensitivity['parameter_sum_sensitivity']},
        'dera_archive_audit':archive,
        'water_v2':{'private_candidate_sha256':water_v2['sha256'],
            'parent_document_sha256':water_v2['parent_document_sha256'],
            'summary':water_v2['summary'],'osm_water_policy':water_v2['osm_water_policy']},
        'water_v2_sensitivity':{'private_document_sha256':sensitivity_v2['sha256'],
            'strict_candidate_sha256':sensitivity_v2['strict_candidate_sha256'],
            'summary':sensitivity_v2['summary'],'parameter_sum_sensitivity':sensitivity_v2['parameter_sum_sensitivity']},
        'exercise_v3':{'parent_v2_sha256':exercise_v3['parent_v2_sha256'],
            'mixed_exercise_cells':exercise_v3['summary']['mixed_exercise_cells'],
            'eligibility_reasons':exercise_v3['summary']['eligibility_reasons']},
        'exercise_v4':{'parent_v2_sha256':exercise_v4['parent_v2_sha256'],
            'dera_rivers_response_sha256':exercise_v4['dera_rivers_response_sha256'],
            'geographic_summary':{k:v for k,v in exercise_v4['summary'].items() if not k.startswith('cases')}}}


def validate_aggregate(evidence, strict, sensitivity, archive_audit, water_v2, sensitivity_v2, exercise_v3, exercise_v4):
    expected=build_aggregate(strict,sensitivity,archive_audit,water_v2,sensitivity_v2,exercise_v3,exercise_v4)
    if serialize(evidence)!=serialize(expected):
        raise ValueError('aggregate differs from complete source-validated reconstruction')


def require_source_files(paths):
    missing=[str(p) for p in paths if not Path(p).is_file()]
    if missing: raise FileNotFoundError('required validation sources absent: '+', '.join(missing))


def require_osm_tile_files(manifest_path, samples_path):
    checked(manifest_path,'osm_manifest'); checked(samples_path,'osm_samples')
    manifest=read_json(manifest_path); samples=read_json(samples_path)
    tiles=[item for item in manifest['tiles'] if item.get('status')=='success']+samples['tiles']
    require_source_files([item['path'] for item in tiles])


def reject_output_alias(output, inputs):
    if Path(output).resolve() in {Path(item).resolve() for item in inputs}:
        raise ValueError('output cannot overwrite an input')


def require_private_path(path):
    resolved = Path(path).resolve()
    private = PRIVATE_ROOT.resolve()
    tmp = Path('/tmp').resolve()
    if (Path(path).is_symlink() or not private.is_relative_to(tmp) or private == tmp or
            not resolved.is_relative_to(private) or resolved == private):
        raise ValueError('candidate must be under the dedicated private directory outside all git worktrees')
    # Reject actual worktrees and gitfiles at every existing ancestor, including
    # nested repositories. Some sandboxes expose empty placeholder .git dirs;
    # those are not repositories, so only directories with Git markers count.
    for ancestor in (resolved.parent, *resolved.parent.parents):
        marker = ancestor / '.git'
        if marker.is_file() or (marker.is_dir() and any((marker / name).exists() for name in ('HEAD', 'config', 'objects', 'commondir'))):
            raise ValueError('candidate must be outside all git worktrees')



ARCHIVE_PINS = {
    'relief': '922b32d80018ef6d49d989c5d3d29ad63454f17bf1f68418cec85b495209aba8',
    'limits': '749c61776cb90d71b174859bcd8f09abaca9883434b1fb5d251bb276d39b5eb2',
}


def audit_archives(relief_zip, limits_zip, coast_path, strict):
    import shutil
    import tempfile
    import zipfile
    import pyogrio
    from shapely.geometry import box
    from shapely.ops import unary_union
    for path, name in ((relief_zip,'relief'),(limits_zip,'limits')):
        if file_sha256(path) != ARCHIVE_PINS[name]:
            raise ValueError(f'{name} archive hash mismatch')
    checked(coast_path,'coast')
    if strict.get('sha256') != digest({k:v for k,v in strict.items() if k != 'sha256'}):
        raise ValueError('strict candidate hash mismatch')
    if strict['grid']['bounds'] != base.BOUNDS:
        raise ValueError('strict grid mismatch')
    area=box(*base.BOUNDS)
    wfs=unary_union([g for g in collection(coast_path,1580) if g.intersects(area)]).intersection(area)
    with tempfile.TemporaryDirectory(prefix='dera-audit-',dir='/tmp') as temp:
        paths=[]
        for archive, member in ((relief_zip,'1_Relieve.gpkg'),
                                (limits_zip,'094-dera-13-limites-administrativos-gpkg.gpkg')):
            target=Path(temp)/member
            with zipfile.ZipFile(archive) as z, z.open(member) as src, target.open('wb') as dst:
                shutil.copyfileobj(src,dst)
            paths.append(target)
        coast=pyogrio.read_dataframe(paths[0],layer='T01_07_LineaCostaAndalucia',bbox=tuple(base.BOUNDS))
        limits=pyogrio.read_dataframe(paths[1],layer='T13_01_TerminoMunicipal',bbox=tuple(base.BOUNDS))
        if (len(coast)!=64 or len(limits)!=30 or coast.crs.to_epsg()!=25830 or limits.crs.to_epsg()!=25830
                or not coast.geometry.is_valid.all() or not limits.geometry.is_valid.all()
                or coast.geometry.is_empty.any() or limits.geometry.is_empty.any()):
            raise ValueError('unexpected DERA archive geometry or coverage')
        line=unary_union(list(coast.geometry)).intersection(area)
        municipalities=unary_union(list(limits.geometry))
        counts=Counter()
        for r,row in enumerate(strict['cells']):
            for c,cell in enumerate(row):
                square=box(290000+c*500,4064500-(r+1)*500,290000+(c+1)*500,4064500-r*500)
                relation='covers' if municipalities.covers(square) else ('partial' if municipalities.intersects(square) else 'outside')
                counts[cell['surface']+'_'+relation]+=1
        doc={'version':'extended-map-dera-archive-audit-v1','status':'private_audit_not_land_truth',
             'strict_candidate_sha256':strict['sha256'],'archive_sha256':ARCHIVE_PINS,
             'coast_features':len(coast),'municipality_features':len(limits),
             'coast_symmetric_difference_metres':round(wfs.symmetric_difference(line).length,3),
             'coast_hausdorff_metres':round(wfs.hausdorff_distance(line),3),
             'surface_municipality_relation_counts':dict(counts)}
    doc['sha256']=digest(doc)
    return doc


def archive_main():
    p=argparse.ArgumentParser(description='Audit official DERA archives against the strict candidate')
    p.add_argument('command', choices=['audit-dera','audit-dera-validate'])
    for name in ('relief-archive','limits-archive','coast','strict','output'):
        p.add_argument('--'+name, type=Path, required=True)
    a=p.parse_args()
    require_private_path(a.output)
    reject_output_alias(a.output, (a.relief_archive,a.limits_archive,a.coast,a.strict))
    doc=audit_archives(a.relief_archive,a.limits_archive,a.coast,read_json(a.strict))
    if a.command=='audit-dera': write_private_json(a.output,doc)
    elif serialize(read_json(a.output)) != serialize(doc): raise ValueError('archive audit differs from reconstruction')
    print(doc)



def checked_document(doc, expected, label):
    if (not isinstance(expected,str) or len(expected)!=64 or doc.get('sha256')!=expected or
        digest({k:v for k,v in doc.items() if k!='sha256'})!=expected):
        raise ValueError(f'{label} document pin mismatch')


def compile_interior_mixed(strict, v2, audit, *, strict_pin, v2_pin, audit_pin):
    """Versioned private hypotheses only; current contract still censors mixed."""
    checked_document(strict,strict_pin,'strict'); checked_document(v2,v2_pin,'water v2')
    checked_document(audit,audit_pin,'neighborhood audit')
    if (v2.get('parent_document_sha256')!=strict_pin or audit.get('strict_sha256')!=strict_pin or
            audit.get('v2_sha256')!=v2_pin):
        raise ValueError('mixed evidence lineage mismatch')
    cells=[]; positions=set()
    for evidence in audit['reviewed_cells']:
        c,r=evidence['column'],evidence['row']; point=(c,r)
        if (type(c) is not int or type(r) is not int or r<0 or c<0 or
            r>=len(strict['cells']) or r>=len(v2['cells']) or
            c>=len(strict['cells'][r]) or c>=len(v2['cells'][r])):
            raise ValueError('invalid mixed cell coordinate')
        if point in positions: raise ValueError('duplicate mixed cell')
        positions.add(point)
        source=strict['cells'][r][c]; current=v2['cells'][r][c]
        hist=evidence['worldcover_samples']
        if (type(hist) is not dict or any(type(k) is not str or not k.isdigit() or
                type(v) is not int or v<0 for k,v in hist.items())):
            raise ValueError('invalid mixed sample histogram')
        sample_total=sum(hist.values()); water=hist.get('80',0)
        area_conflicts=[x for x in current.get('osm_water_conflicts',[]) if x['kind'] in ('water_area','water_area_relation')]
        lines=[x for x in current.get('osm_water_conflicts',[]) if x['kind']=='watercourse']
        if (source['surface']!='unknown' or current['surface']!='unknown' or
            hist!=source['worldcover_samples'] or hist!=current['worldcover_samples'] or
            sample_total!=2500 or not 0<water<=250 or set(map(int,hist))-(LAND_CODES|{80}) or
            source['source_fuel_class'] is None or source['source_fuel_class']!=evidence['fuel_source_class'] or
            strict['terrain_candidate'][r][c] is None or
            strict['terrain_candidate'][r][c]!=evidence['terrain_candidate_mean'] or
            not evidence['dera_land_face_covers'] or not evidence['osm_land_face_covers'] or
            evidence['coast_distance_m']<=BUFFER_METRES or evidence['osm_coast_distance_m']<=BUFFER_METRES or
            evidence['water_polygon_intersections']!=0 or evidence['osm_area_conflicts']!=len(area_conflicts) or
            area_conflicts or not lines):
            raise ValueError('interior mixed evidence incomplete or conflicting')
        fraction=round(1-water/sample_total,6)
        cells.append({'column':c,'row':r,'surface':'mixed','status':'hypothesis_censored_under_v1',
            'source_fuel_class':source['source_fuel_class'],
            'worldcover_water_samples':water,'worldcover_total_samples':sample_total,
            'sample_nonwater_fraction':fraction,
            'modeled_initial_fuel_factor':{'low_assumption':0.9,'nominal_sample_assumption':fraction,'high_assumption':1.0},
            'waterway_evidence':lines,'dera_water_polygon_intersections':0,'osm_water_area_conflicts':0,
            'confidence':'uncalibrated_hypothesis',
            'limitations':'10 m sample share is not validated whole-cell burnable fraction or subcell geometry.'})
    cases={}
    for name,case in audit['cases'].items():
        unknown={tuple(p) for p in case['v2_unknown']}
        if len(unknown)!=case['v2_unknown_count']:
            raise ValueError('neighborhood audit count mismatch')
        remaining=len(unknown-positions)
        cases[name]={'v2_unknown_neighborhood':len(unknown),
                     'hypothetical_unknown_after_mixed':remaining,
                     'surface_check_cleared':remaining==0}
    doc={'version':'extended-map-interior-mixed-hypotheses-v1',
        'status':'private_hypotheses_not_runtime_contract',
        'strict_sha256':strict_pin,'water_v2_sha256':v2_pin,'audit_sha256':audit_pin,
        'assumptions':'Fuel factors are illustrative 0.9 to 1.0 scenarios; not a calibrated confidence interval or measured fuel load.',
        'cells':cells,'hypothetical_surface_preflight':cases,
        'limitations':'Mixed remains censored under v1; no subcell fire geometry or route access is established.'}
    doc['sha256']=digest(doc)
    return doc


def mixed_main():
    p=argparse.ArgumentParser(description='Private interior mixed-cell evidence hypotheses')
    p.add_argument('command',choices=['mixed-evidence-compile','mixed-evidence-validate'])
    for name in ('strict','water-v2','audit','output'):
        p.add_argument('--'+name,type=Path,required=True)
    for name in ('strict-sha256','water-v2-sha256','audit-sha256'):
        p.add_argument('--'+name,required=True)
    a=p.parse_args()
    require_private_path(a.output)
    reject_output_alias(a.output,(a.strict,a.water_v2,a.audit))
    require_source_files((a.strict,a.water_v2,a.audit))
    result=compile_interior_mixed(read_json(a.strict),read_json(a.water_v2),read_json(a.audit),
        strict_pin=a.strict_sha256,v2_pin=a.water_v2_sha256,audit_pin=a.audit_sha256)
    if a.command=='mixed-evidence-compile': write_private_json(a.output,result)
    elif serialize(read_json(a.output))!=serialize(result): raise ValueError('mixed evidence differs from reconstruction')
    print({'sha256':result['sha256'],'candidate_cells':len(result['cells']),
           'surface_neighborhoods_cleared':sum(c['surface_check_cleared'] for c in result['hypothetical_surface_preflight'].values())})


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['compile','validate','sensitivity-compile','sensitivity-validate','water-v2-compile','water-v2-validate','water-v2-sensitivity-compile','water-v2-sensitivity-validate','exercise-v3-compile','exercise-v3-validate','exercise-v4-compile','exercise-v4-validate','validate-aggregate'])
    for key in ('parent','fuel-archive','boundary-archive','expanded','parent-sample','coast','water','worldcover','osm-manifest','osm-samples','output'):
        p.add_argument('--'+key, type=Path, required=True)
    p.add_argument('--sensitivity-output', type=Path)
    p.add_argument('--water-v2-output', type=Path)
    p.add_argument('--water-v2-sensitivity-output', type=Path)
    p.add_argument('--exercise-v3-output', type=Path)
    p.add_argument('--exercise-v3-catalog', type=Path)
    p.add_argument('--exercise-v4-output', type=Path)
    p.add_argument('--dera-rivers', type=Path)
    p.add_argument('--relief-archive', type=Path)
    p.add_argument('--limits-archive', type=Path)
    p.add_argument('--archive-audit', type=Path)
    p.add_argument('--aggregate', type=Path)
    a=p.parse_args()
    require_private_path(a.output)
    inputs=(a.parent,a.fuel_archive,a.boundary_archive,a.expanded,a.parent_sample,a.coast,a.water,a.worldcover,a.osm_manifest,a.osm_samples)
    reject_output_alias(a.output, inputs)
    if a.command.startswith('sensitivity-') or a.command == 'validate-aggregate':
        if a.sensitivity_output is None: p.error('sensitivity commands require --sensitivity-output')
        require_private_path(a.sensitivity_output)
        reject_output_alias(a.sensitivity_output, inputs+(a.output,))
    if a.command.startswith(('water-v2-','exercise-v3-','exercise-v4-')) or a.command == 'validate-aggregate':
        if a.water_v2_output is None: p.error('water v2 commands require --water-v2-output')
        require_private_path(a.water_v2_output)
        reject_output_alias(a.water_v2_output, inputs+(a.output,))
    if a.command.startswith('water-v2-sensitivity-') or a.command == 'validate-aggregate':
        if a.water_v2_sensitivity_output is None: p.error('v2 sensitivity and aggregate validation require --water-v2-sensitivity-output')
        require_private_path(a.water_v2_sensitivity_output)
        reject_output_alias(a.water_v2_sensitivity_output,inputs+(a.output,a.water_v2_output))
    if a.command.startswith(('exercise-v3-','exercise-v4-')) or a.command == 'validate-aggregate':
        if a.exercise_v3_output is None or a.exercise_v3_catalog is None: p.error('exercise v3 requires its output and source catalog')
        require_private_path(a.exercise_v3_output)
        other=(a.sensitivity_output,a.water_v2_sensitivity_output,a.exercise_v4_output,a.dera_rivers,a.relief_archive,a.limits_archive,a.archive_audit,a.aggregate)
        reject_output_alias(a.exercise_v3_output,inputs+(a.output,a.water_v2_output,a.exercise_v3_catalog)+tuple(x for x in other if x is not None))
    if a.command.startswith('exercise-v4-') or a.command == 'validate-aggregate':
        if a.exercise_v4_output is None or a.dera_rivers is None: p.error('exercise v4 requires its output and DERA rivers')
        require_private_path(a.exercise_v4_output)
        other=(a.sensitivity_output,a.water_v2_sensitivity_output,a.relief_archive,a.limits_archive,a.archive_audit,a.aggregate)
        reject_output_alias(a.exercise_v4_output,inputs+(a.output,a.water_v2_output,a.exercise_v3_output,a.exercise_v3_catalog,a.dera_rivers)+tuple(x for x in other if x is not None))
    required=list(inputs)
    if a.command != 'compile': required.append(a.output)
    if a.command.startswith(('exercise-v3-','exercise-v4-')):
        required.extend([a.water_v2_output,a.exercise_v3_catalog])
        if a.command!='exercise-v3-compile': required.append(a.exercise_v3_output)
    if a.command.startswith('exercise-v4-'):
        required.append(a.dera_rivers)
        if a.command=='exercise-v4-validate': required.append(a.exercise_v4_output)
    if a.command == 'validate-aggregate':
        if any(x is None for x in (a.relief_archive,a.limits_archive,a.archive_audit,a.aggregate)): p.error('aggregate validation requires both archives, the archive audit and aggregate evidence')
        required.extend([a.sensitivity_output,a.water_v2_output,a.relief_archive,a.limits_archive,a.archive_audit,a.aggregate,a.water_v2_sensitivity_output,a.exercise_v3_output,a.exercise_v3_catalog,a.exercise_v4_output,a.dera_rivers])
    require_source_files(required)
    require_osm_tile_files(a.osm_manifest,a.osm_samples)
    parent=base.load_parent(a.parent); ref=base.inspect_boundaries(a.boundary_archive)
    fuels=base.sample_fuel(a.fuel_archive,parent,width=base.WIDTH,height=base.HEIGHT,west=base.BOUNDS[0],north=base.BOUNDS[3],parent_row=base.ROW,parent_col=base.COL)
    map_doc=base.compile_map(parent,fuels,ref)
    terrain_doc=terrain.build(a.expanded,a.parent_sample)
    surface,cover,comparison,interior=surface_inputs(a.coast,a.water,a.worldcover,a.osm_manifest,a.osm_samples,include_interior=True)
    doc=compile_document(map_doc,terrain_doc,surface,cover,comparison)
    if a.command == 'compile':
        write_private_json(a.output,doc)
    else:
        validate_document(read_json(a.output),map_doc,terrain_doc,surface,cover,comparison)
    if a.command.startswith(('water-v2-','exercise-v3-','exercise-v4-')) or a.command == 'validate-aggregate':
        if a.command.startswith('exercise-v4-') or a.command == 'validate-aggregate':
            features,stats,water_metadata=load_osm_water(a.osm_manifest,a.osm_samples,include_way_metadata=True)
        else:
            features,stats=load_osm_water(a.osm_manifest,a.osm_samples)
        conflicts=water_conflicts(features,base.WIDTH,base.HEIGHT,base.BOUNDS[0],base.BOUNDS[3])
        revised=compile_water_v2(doc,map_doc,terrain_doc,surface,cover,comparison,conflicts,stats)
        if a.command == 'water-v2-compile': write_private_json(a.water_v2_output,revised)
        elif serialize(read_json(a.water_v2_output)) != serialize(revised): raise ValueError('water v2 differs from source reconstruction')
        print({'water_v2_sha256':revised['sha256'],'summary':revised['summary'],'osm_water_policy':revised['osm_water_policy']})
    if a.command.startswith(('exercise-v3-','exercise-v4-')) or a.command == 'validate-aggregate':
        exercise_v3=compile_exercise_v3(doc,revised,interior,read_json(a.exercise_v3_catalog),parent)
        if a.command == 'exercise-v3-compile': write_private_json(a.exercise_v3_output,exercise_v3)
        elif serialize(read_json(a.exercise_v3_output))!=serialize(exercise_v3): raise ValueError('exercise v3 differs from source reconstruction')
        print({'exercise_v3_sha256':exercise_v3['sha256'],'geographic_summary':{k:v for k,v in exercise_v3['summary'].items() if not k.startswith('cases')}})
    if a.command.startswith('exercise-v4-') or a.command == 'validate-aggregate':
        rivers=dera_river_conflicts(a.dera_rivers,base.WIDTH,base.HEIGHT,base.BOUNDS[0],base.BOUNDS[3])
        exercise_v4=compile_exercise_v4(revised,exercise_v3,interior,features,water_metadata,rivers,read_json(a.exercise_v3_catalog),parent)
        if a.command == 'exercise-v4-compile': write_private_json(a.exercise_v4_output,exercise_v4)
        elif serialize(read_json(a.exercise_v4_output))!=serialize(exercise_v4): raise ValueError('exercise v4 differs from source reconstruction')
        print({'exercise_v4_sha256':exercise_v4['sha256'],'geographic_summary':{k:v for k,v in exercise_v4['summary'].items() if not k.startswith('cases')}})
    if a.command.startswith('water-v2-sensitivity-') or a.command == 'validate-aggregate':
        sensitivity_v2=compile_sensitivity(revised)
        if a.command == 'water-v2-sensitivity-compile': write_private_json(a.water_v2_sensitivity_output,sensitivity_v2)
        else: validate_sensitivity(read_json(a.water_v2_sensitivity_output),revised)
        print({'water_v2_sensitivity_sha256':sensitivity_v2['sha256'],'summary':sensitivity_v2['summary'],'parameter_sum_sensitivity':sensitivity_v2['parameter_sum_sensitivity']})
    if a.command.startswith('sensitivity-'):
        sensitivity = compile_sensitivity(doc)
        if a.command == 'sensitivity-compile': write_private_json(a.sensitivity_output,sensitivity)
        else: validate_sensitivity(read_json(a.sensitivity_output),doc)
        print({'sensitivity_sha256':sensitivity['sha256'],'summary':sensitivity['summary'],'parameter_sum_sensitivity':sensitivity['parameter_sum_sensitivity']})
    if a.command == 'validate-aggregate':
        sensitivity=compile_sensitivity(doc)
        validate_sensitivity(read_json(a.sensitivity_output),doc)
        archive=audit_archives(a.relief_archive,a.limits_archive,a.coast,doc)
        if serialize(read_json(a.archive_audit)) != serialize(archive): raise ValueError('archive audit differs from source reconstruction')
        validate_aggregate(read_json(a.aggregate),doc,sensitivity,archive,revised,sensitivity_v2,exercise_v3,exercise_v4)
        print('aggregate source validation passed: all published fields reconstructed; rights and release approval remain unresolved')
    print({'sha256':doc['sha256'],'summary':doc['summary'],'leave_one_out':doc['leave_one_out_diagnostic'],'quadrant_blocked':doc['quadrant_blocked_diagnostic'],'comparison':comparison})

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1] in ('audit-dera','audit-dera-validate'): archive_main()
    elif len(sys.argv)>1 and sys.argv[1] in ('mixed-evidence-compile','mixed-evidence-validate'): mixed_main()
    else: main()
