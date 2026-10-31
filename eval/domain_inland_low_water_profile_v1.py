#!/usr/bin/env python3
"""Private domain-wide inland/low-water sensitivity evidence; no case selection.

This extends the independently reconstructed v3 profile only for unknown cells
whose entire square is covered by both land faces, lies outside both coastal
buffers and DERA water polygons, and has 1..25 WorldCover water samples out of
2,500. It is not a runnable input, measured burnable area or historical replay.
"""
import argparse
import copy
from collections import Counter
from pathlib import Path

import domain_fuel_surface_profile_v3 as base_compiler
import mixed_estimated_fuel_v1 as prior
from extended_map_common import digest, file_sha256, read_json, serialize

VERSION='wildfire-domain-inland-low-water-sensitivity-profile-v1'
BASE_PROFILE_PIN='a17d83005df0f88a5b9cd4e635472debf7246bad66aeaf26ac9539252fb31f59'
CROSSING_SCENARIOS={'low_openness':.8,'nominal_proxy':.9,'unattenuated':1.0}


def screen_low_water(original,current,interior,terrain,area_hit,dera_area_hit):
    """Fixed surface screen. Absence of mapped water features is not dryness."""
    if original['surface']!='unknown' or current['surface']!='unknown':return 'surface_not_unknown'
    if not interior:return 'both_land_faces_or_coastal_water_screen_failed'
    if terrain is None:return 'terrain_missing'
    histogram=current['worldcover_samples']
    if (type(histogram) is not dict or any(type(v) is not int or v<0 for v in histogram.values())
            or sum(histogram.values())!=2500 or not set(map(int,histogram))<=prior.source.LAND_CODES|{80}
            or not 1<=histogram.get('80',0)<=25 or not prior.source.vegetation_supported(histogram)):
        return 'worldcover_low_water_screen_failed'
    if area_hit or dera_area_hit:return 'mapped_water_area_conflict'
    return None


def extend_profile(base,strict,v2,v4,interior,rivers,archive,features,dera_water,dera_rivers,mapping,code_sha):
    """Add assumptions only to the entire domain's previously unknown cells."""
    from shapely.geometry import box
    if (base.get('version')!=base_compiler.VERSION or base.get('sha256')!=BASE_PROFILE_PIN
            or digest({k:v for k,v in base.items() if k!='sha256'})!=BASE_PROFILE_PIN):
        raise ValueError('independently reconstructed parent profile differs')
    grid=v2['grid'];width,height=grid['width'],grid['height']; west,north=grid['transform'][2],grid['transform'][5]
    if base['grid']!=grid or len(base['cells'])!=width*height:raise ValueError('parent grid differs')
    source_support=prior.source_support(v2,v4)
    indexes=prior.make_spatial_indexes(features,dera_water,dera_rivers)
    result=copy.deepcopy(base);records=result['cells'];assessment=Counter()
    for record in records:
        if record['status']!='unknown':continue
        x,y=record['column'],record['row']; original=strict['cells'][y][x];current=v2['cells'][y][x]
        square=box(west+x*500,north-(y+1)*500,west+(x+1)*500,north-y*500)
        # The area index contains pinned OSM water polygons and DERA water areas;
        # intersection includes boundary touches and conservatively rejects both.
        area_hit=bool(indexes[0] is not None and len(indexes[0].query(square,predicate='intersects')))
        dera_area_hit=any(z.intersects(square) for z in dera_water)
        reason=screen_low_water(original,current,interior[y][x],v2['terrain_candidate'][y][x],area_hit,dera_area_hit)
        if reason:
            assessment[reason]+=1;continue
        osm=current.get('osm_water_conflicts',[])
        if any(z['kind'] in ('water_area','water_area_relation') for z in osm):
            raise ValueError('water geometry index and conflict record disagree')
        samples,blocked=prior.neighbors_for(y,x,grid,source_support,indexes)
        votes=Counter(t['source_class'] for t in samples)
        no_cross=[t for t in samples if not t['crosses_osm_watercourse'] and not t['crosses_dera_watercourse']]
        no_cross_votes=Counter(t['source_class'] for t in no_cross)
        source=current['source_fuel_class']
        if current['fuel_status']=='source':
            if type(source) is not int or source not in mapping:raise ValueError('invalid retained source fuel')
            status='new_low_water_mixed_source';hypothesis=None;classes=[source]
        elif current['fuel_status']=='unknown' and source is None:
            hypothesis=base_compiler.vote_policy(samples,mapping)
            if hypothesis['status']=='unknown':
                assessment['fuel_'+hypothesis['reason']]+=1
                record['low_water_screen']={'surface_screen_passed':True,'fuel_reason':hypothesis['reason'],
                    'source_neighbor_support':samples,'no_mapped_crossing_votes':{str(k):v for k,v in sorted(no_cross_votes.items())}}
                continue
            status='new_low_water_mixed_fuel_'+hypothesis['status'];classes=sorted(votes)
        else:raise ValueError('unexpected source fuel state')
        assumptions=[{'assumed_fuel_class':k,'assumed_terrain':mapping[k]['terrain'],
                      'exercise_base_initial_fuel':mapping[k]['initial_fuel'],
                      'fuel_factor_assumptions':dict(prior.source.EXERCISE_FACTORS)} for k in classes]
        record.update(status=status,reason=None,surface_assumption='mixed_synthetic_sensitivity',
            low_water_evidence={'both_independent_mapped_land_faces_cover_cell':True,
                'outside_both_250m_coast_buffers':True,'dera_water_polygon_intersection':False,
                'osm_water_area_intersection':False,'worldcover_epoch':'2021',
                'worldcover_samples':copy.deepcopy(current['worldcover_samples']),
                'osm_watercourse_epoch':'2026','osm_water_conflicts':copy.deepcopy(osm),
                'dera_watercourses':copy.deepcopy(rivers.get((y,x),[])),
                'dera_river_acquisition':'2026-10-30','dera_river_source_epoch':'unverified'},
            source_neighbor_support=samples,water_area_blocked_neighbors=blocked,
            no_mapped_watercourse_crossing_votes={str(k):v for k,v in sorted(no_cross_votes.items())},
            no_mapped_watercourse_crossing_support_count=len(no_cross),
            full_archive_center_sample=archive[y][x],fuel_hypothesis=hypothesis,
            synthetic_fuel_class_alternatives=assumptions,
            cross_water_sensitivity={'crossing_openness_scenarios':dict(CROSSING_SCENARIOS),
                'default':None,'strict_censor_alternative':True,
                'limitations':'Uniform factors are exercise assumptions, not measured widths, crossings or probabilities; 1.0 does not establish absent water.'})
        assessment['eligible_'+('source' if hypothesis is None else hypothesis['status'])]+=1
    counts=Counter(r['status'] for r in records);unknown_reasons=Counter(r['reason'] for r in records if r['status']=='unknown')
    if sum(counts.values())!=width*height or sum(unknown_reasons.values())!=counts['unknown']:
        raise AssertionError('profile partition mismatch')
    result.update(version=VERSION,parent_profile_sha256=BASE_PROFILE_PIN,
        extension_code_sha256=code_sha,counts=dict(sorted(counts.items())),
        unknown_reasons=dict(sorted(unknown_reasons.items())),low_water_assessment=dict(sorted(assessment.items())))
    result['policy']['inland_low_water_extension']={
        'selection':'all parent unknown cells; no case frontiers, outcomes or target lists',
        'surface':'both mapped land faces cover whole cell, outside 250m shoreline buffers, no DERA or OSM mapped water area',
        'worldcover':'2500 samples; 1-25 permanent-water samples; >=2250 vegetation samples; supported terrestrial/water classes only',
        'fuel':'retained source class or original source-only 1km spatial vote; separate source-supported classes',
        'watercrossing':'separate 0.8, 0.9, 1.0 synthetic openness assumptions or continued strict censor',
        'limitations':'2021 samples and 2026 mapped streams do not establish 2021 water, whole-cell dryness, fuel, access, crossing, or calibration.'}
    result['sha256']=digest({k:v for k,v in result.items() if k!='sha256'})
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['compile','validate'])
    names=('parent','fuel-archive','boundary-archive','expanded','parent-sample','coast','water','worldcover',
           'osm-manifest','osm-samples','dera-rivers','catalog','strict','water-v2','exercise-v3','exercise-v4','base-profile','output')
    for key in names:p.add_argument('--'+key,type=Path,required=True)
    for key in ('strict-sha256','water-v2-sha256','exercise-v3-sha256','exercise-v4-sha256'):p.add_argument('--'+key,required=True)
    a=p.parse_args();prior.source.require_private_path(a.output);prior.source.require_private_path(a.base_profile)
    paths=[getattr(a,k.replace('-','_')) for k in names if k!='output']
    prior.check_private_output(a.output,paths)
    if a.command=='compile' and a.output.exists():raise ValueError('compile requires a fresh output')
    prior.source.require_source_files(paths+([a.output] if a.command=='validate' else []))
    base=read_json(a.base_profile)
    if base.get('sha256')!=BASE_PROFILE_PIN or digest({k:v for k,v in base.items() if k!='sha256'})!=BASE_PROFILE_PIN:
        raise ValueError('base profile pin differs')
    tiles=prior.snapshot_osm_tile_inputs(a.osm_manifest,a.osm_samples)
    protected=paths+prior.protected_osm_tile_paths(tiles);prior.check_private_output(a.output,protected)
    code={'profile':Path(base_compiler.__file__),'fuel_evidence':Path(prior.__file__),
      'map_compiler':Path(prior.source.__file__),'common':Path(prior.source.base.__file__).with_name('extended_map_common.py'),
      'map':Path(prior.source.base.__file__),'terrain':Path(prior.source.terrain.__file__),
      'access':Path(prior.source.access.__file__),'domain':Path(__file__).with_name('extended_domain_v1.py'),
      'drafts':Path(__file__).with_name('extended_scenario_drafts_v1.py')}
    code_pins={k:file_sha256(v) for k,v in code.items()};extension_pin=file_sha256(__file__)
    if code_pins!=base['source_pins']['code_sha256']:raise ValueError('parent compiler source identity differs')
    mapping=prior.mapping_from_pinned_source()
    strict,v2,v4,interior,rivers,archive,features,water,river_geoms=prior.reconstruct(a)
    features2,_,metadata=prior.load_pinned_osm_water(a.osm_manifest,a.osm_samples)
    if len(features2)!=len(features):raise ValueError('OSM feature snapshot differs')
    rebuilt=base_compiler.build_profile(strict,v2,v4,interior,rivers,archive,features2,metadata,water,river_geoms,mapping,base['source_pins'])
    if serialize(rebuilt)!=serialize(base):raise ValueError('parent profile differs from full source reconstruction')
    result=extend_profile(base,strict,v2,v4,interior,rivers,archive,features2,water,river_geoms,mapping,extension_pin)
    if code_pins!={k:file_sha256(v) for k,v in code.items()} or file_sha256(__file__)!=extension_pin:
        raise ValueError('compiler source changed during run')
    prior.verify_osm_tile_identities(tiles)
    if a.command=='compile':prior.write_private_atomic(a.output,result,protected,pre_replace_check=lambda:prior.verify_osm_tile_identities(tiles))
    elif serialize(read_json(a.output))!=serialize(result):raise ValueError('sensitivity profile differs from source reconstruction')
    print({'sha256':result['sha256'],'counts':result['counts'],'unknown_reasons':result['unknown_reasons'],
           'low_water_assessment':result['low_water_assessment']})

if __name__=='__main__':main()
