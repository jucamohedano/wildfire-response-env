#!/usr/bin/env python3
"""Compile a private domain-wide, source-reconstructed sensitivity profile.

No case frontiers, outcomes, or target lists enter this method. The output is
an evidence profile, not a simulator input, release, measurement or calibration.
"""
import argparse
import copy
from collections import Counter
from pathlib import Path

import mixed_estimated_fuel_v1 as prior
from extended_map_common import digest, file_sha256, read_json, serialize

VERSION = 'wildfire-domain-fuel-surface-sensitivity-profile-v3'


def vote_policy(samples, mapping):
    """Source-only votes: a strict plurality or an explicit tied alternative set."""
    votes=Counter(item['source_class'] for item in samples)
    if len(samples)<3:
        return {'status':'unknown','reason':'fewer_than_three_source_neighbors'}
    if len(votes)>3:
        return {'status':'unknown','reason':'more_than_three_source_classes'}
    if any(code not in mapping for code in votes):raise ValueError('fuel class missing in mapping')
    maximum=max(votes.values()); winners=sorted(k for k,v in votes.items() if v==maximum)
    if len(winners)>1:
        if maximum<2:
            return {'status':'unknown','reason':'tied_single_neighbor_classes'}
        return {'status':'tied_sensitivity_only','confidence':'low_uncalibrated',
                'source_votes':{str(k):votes[k] for k in sorted(votes)},
                'scenario_classes':winners,'nominal_class':None,
                'limitations':'Tied classes are separate alternatives, not a calibrated range or measured class.'}
    result=prior.decide(samples,mapping)
    if result['status']=='unknown':return result
    result['source_votes']={str(k):votes[k] for k in sorted(votes)}
    return result


def small_polygons(square, hits, metadata, dera_water):
    """Inland natural-water or basin polygons, wholly contained and <5%."""
    from shapely.ops import unary_union
    if not hits:return None
    if any(g.intersects(square) for g in dera_water):return None
    geoms=[]; details=[];ids=set()
    for ident,kind,form,g in hits:
        tags=metadata.get(ident,{}).get('tags',{})
        if (kind!='water_area' or form!='polygon' or ident in ids or
            not (tags.get('natural')=='water' or tags.get('landuse')=='basin') or
            g.geom_type!='Polygon' or not g.is_valid or not g.is_simple or len(g.interiors)>0
            or not square.contains(g) or square.boundary.intersects(g)):
            return None
        ids.add(ident);geoms.append(g)
        details.append({'way_id':ident,'tags':tags})
    if any(a.intersects(b) for i,a in enumerate(geoms) for b in geoms[i+1:]):return None
    fraction=unary_union(geoms).intersection(square).area/square.area
    if not 0<fraction<.05:return None
    return {'mapped_fraction':fraction,'features':sorted(details,key=lambda x:x['way_id'])}


def build_profile(strict,v2,v4,interior,rivers,archive,features,metadata,dera_water,dera_rivers,mapping,pins):
    from shapely.geometry import box
    from shapely.strtree import STRtree
    if v2.get('parent_document_sha256')!=strict['sha256'] or v4.get('parent_v2_sha256')!=v2['sha256']:
        raise ValueError('map lineage differs')
    grid=v2['grid']; h,w=grid['height'],grid['width'];west,north=grid['transform'][2],grid['transform'][5]
    if grid['transform'][0]!=500 or grid['transform'][4]!=-500:raise ValueError('unexpected grid scale')
    support=prior.source_support(v2,v4);indexes=prior.make_spatial_indexes(features,dera_water,dera_rivers)
    area_features=[(i,k,f,g) for i,k,f,g in features if k in ('water_area','water_area_relation')]
    area_tree=STRtree([item[3] for item in area_features]) if area_features else None
    mixed_records={(r['row'],r['column']):r for r in v4['cells']}
    if len(mixed_records)!=len(v4['cells']):raise ValueError('duplicate v4 mixed evidence')
    mixed=set(mixed_records)
    extra={(r['row'],r['column']) for r in v4['additional_dera_censors']}
    records=[]; counts=Counter(); reasons=Counter()
    for y in range(h):
        for x in range(w):
            pos=(y,x);cell=v2['cells'][y][x]; original=strict['cells'][y][x]
            surface=cell['surface'];status=cell['fuel_status'];terrain=v2['terrain_candidate'][y][x]
            rec={'row':y,'column':x,'status':'unknown','reason':None,
                 'v2_surface':surface,'source_fuel_class':cell['source_fuel_class'],
                 'terrain_candidate_available':terrain is not None}
            if surface=='sea' and pos not in extra:
                rec.update(status='mapped_sea',reason=None)
            elif pos in mixed and status=='source' and terrain is not None:
                rec.update(status='existing_v4_mixed_source',reason=None,
                           inherited_v4_evidence=copy.deepcopy(mixed_records[pos]),
                           inherited_v4_document_sha256=v4['sha256'])
            elif surface=='land' and pos not in extra and status=='source' and terrain is not None:
                rec.update(status='mapped_land_source',reason=None)
            elif terrain is None:
                rec['reason']='terrain_missing'
            elif original['surface']!='land' or not interior[y][x]:
                rec['reason']='surface_not_corroborated_inland'
            else:
                hist=cell['worldcover_samples'];osm=cell.get('osm_water_conflicts',[])
                area=[z for z in osm if z['kind'] in ('water_area','water_area_relation')]
                lines=[z for z in osm if z['kind']=='watercourse']
                river=rivers.get(pos,[])
                if (sum(hist.values())!=2500 or not set(map(int,hist))<=prior.source.LAND_CODES|{80}
                        or hist.get('80',0)>25 or not prior.source.vegetation_supported(hist)):
                    rec['reason']='worldcover_screen_failed'
                else:
                    square=box(west+x*500,north-(y+1)*500,west+(x+1)*500,north-y*500)
                    hits=[area_features[int(i)] for i in area_tree.query(square,predicate='intersects')] if area_tree else []
                    if {(a,b,c) for a,b,c,_ in hits}!={(z.get('way_id',z.get('relation_id')),z['kind'],z['geometry']) for z in area}:
                        raise ValueError('mapped-water conflict differs from pinned geometry')
                    basin=small_polygons(square,hits,metadata,dera_water) if hits else None
                    if hits and basin is None:
                        rec['reason']='water_area_geometry_unsupported'
                    elif not hits and any(g.intersects(square) for g in dera_water):
                        rec['reason']='dera_water_area_conflict'
                    elif surface=='sea':
                        rec['reason']='sea_with_conflicting_water_evidence'
                    elif hits and not (lines or river):
                        rec['reason']='water_area_without_watercourse_support'
                    elif surface=='unknown' and not (lines or river or hits):
                        rec['reason']='unknown_surface_without_mixed_water_basis'
                    else:
                        kind='mixed' if lines or river or hits or pos in extra else 'land'
                        if basin is not None:rec['mapped_water_area']=basin
                        rec.update(surface_assumption=kind,worldcover_samples=hist,
                                   osm_water_conflicts=osm,dera_watercourses=river)
                        if status=='source':
                            rec.update(status='new_mixed_source',reason=None,
                                       fuel_factor_assumptions=dict(prior.source.EXERCISE_FACTORS))
                        else:
                            samples,blocked=prior.neighbors_for(y,x,grid,support,indexes)
                            hypothesis=vote_policy(samples,mapping)
                            rec.update(source_neighbor_support=samples,water_area_blocked_neighbors=blocked,
                                full_archive_center_sample=archive[y][x],fuel_hypothesis=hypothesis)
                            if hypothesis['status']=='unknown':
                                rec['reason']='fuel_'+hypothesis['reason']
                            else:
                                rec.update(status=('new_'+kind+'_fuel_'+hypothesis['status']),reason=None)
            counts[rec['status']]+=1
            if rec['reason']:reasons[rec['reason']]+=1
            records.append(rec)
    if sum(counts.values())!=w*h or sum(reasons.values())!=counts['unknown']:
        raise AssertionError('profile partition mismatch')
    provenance_keys=('dera_rivers_response_sha256','dera_rivers_acquired','dera_rivers_source_epoch',
                     'worldcover_epoch','osm_water_epoch','source_catalog_sha256')
    if any(k not in v4 for k in provenance_keys):raise ValueError('v4 document provenance missing')
    v4_provenance={'document_sha256':v4['sha256'],**{k:copy.deepcopy(v4[k]) for k in provenance_keys}}
    result={'version':VERSION,'status':'private_synthetic_evidence_not_runtime_input','grid':grid,
      'inherited_v4_provenance':v4_provenance,
      'source_pins':pins,'policy':{'selection':'entire grid, independent of cases and outcomes',
      'surface':'v1 mapped inland land and both coastline faces; no coastal buffer or DERA water polygon; v2/v4 source retained',
      'mixed_water':'watercourse or small disjoint whole inland natural-water/basin polygons under 5% with watercourse evidence',
      'fuel':'source-only neighbors <=1000m; >=3; max 3 classes; unique >=50% or explicit tied leaders with >=2 votes each',
      'tied_vote':'separate class alternatives, no nominal winner',
      'water_area_paths':'exclude center paths touching mapped OSM or DERA water areas',
      'fuel_factors':dict(prior.source.EXERCISE_FACTORS),'crossing_proxy':'not selected by evidence profile',
      'limitations':'Not measured fuel, burnable fraction, historical water, a firebreak, access, calibration or simulator readiness.'},
      'counts':dict(sorted(counts.items())),'unknown_reasons':dict(sorted(reasons.items())), 'cells':records}
    result['sha256']=digest(result);return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['compile','validate'])
    names=('parent','fuel-archive','boundary-archive','expanded','parent-sample','coast','water',
           'worldcover','osm-manifest','osm-samples','dera-rivers','catalog','strict','water-v2','exercise-v3','exercise-v4','output')
    for key in names:p.add_argument('--'+key,type=Path,required=True)
    for key in ('strict-sha256','water-v2-sha256','exercise-v3-sha256','exercise-v4-sha256'):p.add_argument('--'+key,required=True)
    a=p.parse_args(); prior.source.require_private_path(a.output)
    paths=[getattr(a,k.replace('-','_')) for k in names if k!='output']
    prior.check_private_output(a.output,paths)
    if a.command=='compile' and a.output.exists():raise ValueError('compile requires a fresh output')
    prior.source.require_source_files(paths+([a.output] if a.command=='validate' else []))
    tiles=prior.snapshot_osm_tile_inputs(a.osm_manifest,a.osm_samples)
    protected=paths+prior.protected_osm_tile_paths(tiles);prior.check_private_output(a.output,protected)
    code={'profile':Path(__file__),'fuel_evidence':Path(prior.__file__),
      'map_compiler':Path(prior.source.__file__),'common':Path(prior.source.base.__file__).with_name('extended_map_common.py'),
      'map':Path(prior.source.base.__file__),'terrain':Path(prior.source.terrain.__file__),
      'access':Path(prior.source.access.__file__),'domain':Path(__file__).with_name('extended_domain_v1.py'),
      'drafts':Path(__file__).with_name('extended_scenario_drafts_v1.py')}
    code_pins={k:file_sha256(v) for k,v in code.items()}
    mapping=prior.mapping_from_pinned_source()
    strict,v2,v4,interior,rivers,archive,features,water,river_geoms=prior.reconstruct(a)
    features2,_,metadata=prior.load_pinned_osm_water(a.osm_manifest,a.osm_samples)
    if len(features2)!=len(features):raise ValueError('OSM feature snapshot differs')
    pins={'strict':a.strict_sha256,'water_v2':a.water_v2_sha256,'exercise_v3':a.exercise_v3_sha256,
          'exercise_v4':a.exercise_v4_sha256,'dera_rivers':prior.source.DERA_RIVER_PIN,
          'fuel_mapping':prior.MAPPING_PIN,'code_sha256':code_pins}
    result=build_profile(strict,v2,v4,interior,rivers,archive,features2,metadata,water,river_geoms,mapping,pins)
    if code_pins!={k:file_sha256(v) for k,v in code.items()}:raise ValueError('compiler changed during run')
    prior.verify_osm_tile_identities(tiles)
    if a.command=='compile':
        prior.write_private_atomic(a.output,result,protected,pre_replace_check=lambda:prior.verify_osm_tile_identities(tiles))
    elif serialize(read_json(a.output))!=serialize(result):raise ValueError('profile differs from source reconstruction')
    print({'sha256':result['sha256'],'counts':result['counts'],'unknown_reasons':result['unknown_reasons']})

if __name__=='__main__':main()
