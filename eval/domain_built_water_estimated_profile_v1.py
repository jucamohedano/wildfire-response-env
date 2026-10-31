#!/usr/bin/env python3
"""Private domain-wide source-fuel, built/vegetated watercourse hypotheses.

Case outcomes and frontier locations are never inputs. Samples are not surveyed
footprints or fuel measurements. All component, inert and crossing values are
uncalibrated sensitivity assumptions; the parent profile remains unchanged.
"""
import argparse
import copy
from collections import Counter
from pathlib import Path

import domain_built_water_profile_v2 as inherited
import domain_inland_low_water_profile_v1 as low
import domain_fuel_surface_profile_v3 as v3
import mixed_estimated_fuel_v1 as prior
from extended_map_common import digest, file_sha256, read_json, serialize

VERSION='wildfire-domain-built-water-estimated-sensitivity-profile-v1'
BUILT_PARENT_PIN='8ee323b84a6b5e1fbf4dce518e9052b90e39fe8966e36d6d1f4af833ed82ce77'
BUILT_COMPILER_PIN='603e8257ff6a6f9b29a58139f18f7ef23347b2431e23845901295cbb0bd867d9'
PARENT_PIN='a81762b1ace2351af87eba03b8378d7b933a834291b32f1bdd2bb40eb9a50447'
LOW_COMPILER_PIN='bcfb60bf376a48a9ddfac421ff8d14c2bc559a6433889acf240372d7b88a67b1'
VEGETATION=('10','20','30','40')
ALLOWED=set(VEGETATION)|{'50','60'}


def extend_missing(parent,strict,water,v4,interior,rivers,features,dera_water,dera_rivers,mapping,code_sha):
    """Fixed domain-wide rule: source-only 1 km votes for corroborated mixed land."""
    from shapely.geometry import box
    if parent.get('sha256')!=BUILT_PARENT_PIN or digest({k:v for k,v in parent.items() if k!='sha256'})!=BUILT_PARENT_PIN:
        raise ValueError('pinned built-water parent differs')
    grid=water['grid'];west,north=grid['transform'][2],grid['transform'][5]
    support=prior.source_support(water,v4);indexes=prior.make_spatial_indexes(features,dera_water,dera_rivers)
    result=copy.deepcopy(parent);assessment=Counter()
    for rec in result['cells']:
        if rec['status']!='unknown':continue
        x,y=rec['column'],rec['row'];current=water['cells'][y][x]
        if current['fuel_status']!='unknown' or current['source_fuel_class'] is not None:continue
        square=box(west+x*500,north-(y+1)*500,west+(x+1)*500,north-y*500)
        area=bool(indexes[0] is not None and len(indexes[0].query(square,predicate='intersects')))
        # Only bypass the prior source-fuel check; all geographic, terrain,
        # land-cover and mapped watercourse screens remain unchanged.
        surface_copy=dict(current,fuel_status='source',source_fuel_class=1)
        reason=inherited.screen(strict['cells'][y][x],surface_copy,interior[y][x],water['terrain_candidate'][y][x],area,rivers.get((y,x),[]))
        if reason:assessment[reason]+=1;continue
        samples,blocked=prior.neighbors_for(y,x,grid,support,indexes)
        hypothesis=v3.vote_policy(samples,mapping)
        if hypothesis['status']=='unknown':assessment['fuel_'+hypothesis['reason']]+=1;continue
        votes=hypothesis['source_votes'];classes=sorted(int(k) for k in votes)
        hist=current['worldcover_samples'];veg=sum(hist.get(k,0) for k in VEGETATION);built=hist['50'];bare=hist.get('60',0)
        rec.update(status='new_built_watercourse_estimated_sensitivity',reason=None,surface_assumption='mixed_synthetic_sensitivity',
          sample_proportions={'vegetation':veg/2500,'built_up':built/2500,'bare':bare/2500},
          evidence={'strict_inland_land':True,'both_land_faces_and_coastal_water_screen':True,
            'source_fuel_class':None,'source_fuel_provenance':copy.deepcopy(current['fuel_provenance']),
            'worldcover_epoch':'2021','worldcover_samples':copy.deepcopy(hist),'osm_water_epoch':'2026',
            'osm_water_conflicts':copy.deepcopy(current.get('osm_water_conflicts',[])),
            'dera_watercourses':copy.deepcopy(rivers.get((y,x),[])),'dera_river_acquisition':'2026-10-30',
            'dera_river_source_epoch':'unverified','mapped_water_area_intersection':False},
          fuel_hypothesis=hypothesis,source_neighbor_support=samples,water_area_blocked_neighbors=blocked,
          synthetic_fuel_class_alternatives=[{'assumed_fuel_class':k,'assumed_terrain':mapping[k]['terrain'],
             'exercise_base_initial_fuel':mapping[k]['initial_fuel'],'factor_alternatives':[.8,.9,1.]} for k in classes],
          sensitivity={'built_initial_fuel':[.2,.4,.8],'inert_assumptions':['synthetic_inert','unknown_censor'],
             'crossing_openness':[.8,.9,1.], 'crossing_default':None,'strict_censor_alternative':True,
             'limitations':'Neighbor classes, sample shares, fuel and crossing are synthetic sensitivities; no survey or calibration.'})
        assessment['eligible_'+hypothesis['status']]+=1
    counts=Counter(r['status'] for r in result['cells']);reasons=Counter(r['reason'] for r in result['cells'] if r['status']=='unknown')
    result.update(version=VERSION,parent_profile_sha256=BUILT_PARENT_PIN,extension_code_sha256=code_sha,
       inherited_built_compiler_sha256=BUILT_COMPILER_PIN,counts=dict(sorted(counts.items())),
       unknown_reasons=dict(sorted(reasons.items())),built_water_estimate_assessment=dict(sorted(assessment.items())))
    result['policy']['built_water_estimated_extension']={'selection':'all parent unknown cells; no case, frontier or outcome input',
       'evidence':'source-only 1 km neighbor votes using v3 vote policy; water-area crossings excluded and watercourse crossings recorded',
       'surface':'same corroborated inland mixed vegetation/built and mapped watercourse screen as built-water v2',
       'limits':'uncalibrated synthetic alternatives, sample counts are not surveyed footprints; unknown remains censored'}
    result['sha256']=digest({k:v for k,v in result.items() if k!='sha256'});return result


def inherited_hashes():
    """Hash every inherited compiler and pinned fuel mapping used in rebuild."""
    code={'profile':Path(v3.__file__),'fuel_evidence':Path(prior.__file__),
      'map_compiler':Path(prior.source.__file__),'common':Path(prior.source.base.__file__).with_name('extended_map_common.py'),
      'map':Path(prior.source.base.__file__),'terrain':Path(prior.source.terrain.__file__),
      'access':Path(prior.source.access.__file__),'domain':Path(__file__).with_name('extended_domain_v1.py'),
      'drafts':Path(__file__).with_name('extended_scenario_drafts_v1.py'),
      'low_extension':Path(low.__file__),'fuel_mapping_file':prior.MAPPING_PATH, 'built_compiler':Path(inherited.__file__)}
    return {k:file_sha256(path) for k,path in code.items()}


def reconstruct_with_source_guard(expected,operation):
    """Fail if inherited source differs before or after reconstruction."""
    if inherited_hashes()!=expected:raise ValueError('inherited compiler source differs before reconstruction')
    result=operation()
    if inherited_hashes()!=expected:raise ValueError('inherited compiler source changed during reconstruction')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['compile','validate'])
    names=('parent','fuel-archive','boundary-archive','expanded','parent-sample','coast','water','worldcover','osm-manifest','osm-samples','dera-rivers','catalog','strict','water-v2','exercise-v3','exercise-v4','base-profile','built-profile','output')
    for name in names:p.add_argument('--'+name,type=Path,required=True)
    for name in ('strict-sha256','water-v2-sha256','exercise-v3-sha256','exercise-v4-sha256'):p.add_argument('--'+name,required=True)
    a=p.parse_args();prior.source.require_private_path(a.output);prior.source.require_private_path(a.base_profile)
    paths=[getattr(a,k.replace('-','_')) for k in names if k!='output'];prior.check_private_output(a.output,paths)
    if a.command=='compile' and a.output.exists():raise ValueError('compile requires a fresh output')
    prior.source.require_source_files(paths+([a.output] if a.command=='validate' else []))
    parent=read_json(a.base_profile)
    if parent.get('sha256')!=PARENT_PIN or digest({k:v for k,v in parent.items() if k!='sha256'})!=PARENT_PIN:raise ValueError('parent profile differs')
    if file_sha256(low.__file__)!=LOW_COMPILER_PIN:raise ValueError('low-water compiler source differs')
    tiles=prior.snapshot_osm_tile_inputs(a.osm_manifest,a.osm_samples);protected=paths+prior.protected_osm_tile_paths(tiles)
    prior.check_private_output(a.output,protected);code_pin=file_sha256(__file__)
    expected_inherited=dict(parent['source_pins']['code_sha256'],low_extension=LOW_COMPILER_PIN,
                            fuel_mapping_file=parent['source_pins']['fuel_mapping'],built_compiler=BUILT_COMPILER_PIN)
    def rebuild_from_sources():
        mapping=prior.mapping_from_pinned_source()
        strict,water,v4,interior,rivers,archive,features,water_geoms,river_geoms=prior.reconstruct(a)
        features2,_,metadata=prior.load_pinned_osm_water(a.osm_manifest,a.osm_samples)
        if len(features2)!=len(features):raise ValueError('OSM feature snapshot differs')
        v3pins=parent['source_pins']
        rebuild=v3.build_profile(strict,water,v4,interior,rivers,archive,features2,metadata,water_geoms,river_geoms,mapping,v3pins)
        low_rebuilt=low.extend_profile(rebuild,strict,water,v4,interior,rivers,archive,features2,water_geoms,river_geoms,mapping,LOW_COMPILER_PIN)
        if serialize(low_rebuilt)!=serialize(parent):raise ValueError('parent source reconstruction differs')
        built=inherited.extend_profile(parent,strict,water,interior,rivers,features2,water_geoms,river_geoms,BUILT_COMPILER_PIN)
        if serialize(built)!=serialize(read_json(a.built_profile)):raise ValueError('built parent reconstruction differs')
        return extend_missing(built,strict,water,v4,interior,rivers,features2,water_geoms,river_geoms,mapping,code_pin)
    result=reconstruct_with_source_guard(expected_inherited,rebuild_from_sources)
    if file_sha256(__file__)!=code_pin or file_sha256(low.__file__)!=LOW_COMPILER_PIN:raise ValueError('compiler changed')
    prior.verify_osm_tile_identities(tiles)
    if a.command=='compile':prior.write_private_atomic(a.output,result,protected,pre_replace_check=lambda:prior.verify_osm_tile_identities(tiles))
    elif serialize(read_json(a.output))!=serialize(result):raise ValueError('built-water profile differs')
    print({'sha256':result['sha256'],'counts':result['counts'],'unknown_reasons':result['unknown_reasons'],'assessment':result['built_water_estimate_assessment']})

if __name__=='__main__':main()
