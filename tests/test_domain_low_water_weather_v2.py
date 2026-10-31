"""Disjoint low-water classes, retained source fuel, explicit subset and replay."""
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def test_per_cell_low_water_choices():
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=os.pathsep.join([
        str(ROOT/'envs'),str(ROOT/'envs/wildfire_extended_inputs_v1'),str(ROOT/'envs/wildfire_response/core/src'),
        str(ROOT/'envs/wildfire_containment_v1/src'),str(ROOT/'eval'),str(ROOT)]))
    p=subprocess.run([sys.executable,__file__],cwd=ROOT,env=env,capture_output=True,text=True,timeout=90)
    assert p.returncode==0,p.stdout+p.stderr


def run():
    import copy
    import pytest
    import adapter_v1 as api
    from eval import domain_low_water_weather_v2 as m
    from eval.domain_profile_weather_v2 import convert_profile
    from wildfire_expanded_incident_v1.mixed_v2 import MixedInputsV2,_VERIFIED
    n=7;pins={k:'a'*64 for k in ('surface','fuel','elevation','access')}
    grid={'width':n,'height':n,'crs':'EPSG:25830','transform':[500,0,0,0,-500,3500]}
    doc={'version':api._contract_module.VERSION,'grid':grid,'domain_mask':[[True]*n for _ in range(n)],
         'surface':[['land']*n for _ in range(n)],'fuel':[[4]*n for _ in range(n)],
         'elevation':[[100.]*n for _ in range(n)],'access':[['unknown']*n for _ in range(n)],'source_pins':pins}
    doc['surface'][1][1]='sea';doc['fuel'][1][1]=doc['elevation'][1][1]=None
    for x,y in ((4,3),(4,4),(3,4),(5,5),(5,1)):
        doc['surface'][y][x]='unknown';doc['fuel'][y][x]=None
    doc['fuel'][4][3]=2
    contract=api.load_inputs(doc,api._hash(doc),expected_source_pins=pins,opt_in=True)
    identity=dict(strict='s',water_v2='w',overlay_v3='3',overlay_v4='4',dera_rivers='r',catalog='c',crossing_proxy=.9)
    base=MixedInputsV2(contract,(),tuple(sorted(identity.items())),_VERIFIED)
    candidates={(4,3),(4,4),(3,4),(5,5)};cells=[];strict=[];water=[]
    for y in range(n):
        sr=[];wr=[]
        for x in range(n):
            surface=doc['surface'][y][x];fuel=doc['fuel'][y][x]
            status='mapped_sea' if surface=='sea' else 'unknown' if surface=='unknown' else 'mapped_land_source'
            cells.append(dict(column=x,row=y,status=status,reason='unresolved' if status=='unknown' else None,
                v2_surface=surface,source_fuel_class=fuel,terrain_candidate_available=doc['elevation'][y][x] is not None))
            obj=dict(surface=surface,fuel_status='source' if fuel is not None else 'unknown',source_fuel_class=fuel,
                     worldcover_samples={'10':2499,'80':1} if (x,y) in candidates else {'10':2500})
            sr.append(copy.deepcopy(obj));wr.append(copy.deepcopy(obj))
        strict.append(sr);water.append(wr)
    parent=dict(version='wildfire-domain-fuel-surface-sensitivity-profile-v3',status='private_synthetic_evidence_not_runtime_input',
        grid=grid,cells=cells,source_pins=dict(strict='s',water_v2='w',exercise_v3='3',exercise_v4='4',dera_rivers='r'))
    parent['sha256']=api._hash(parent);m.PARENT_PIN=parent['sha256']
    parent_inputs=convert_profile(parent,base,expected_profile_sha256=parent['sha256'],opt_in=True)
    new=copy.deepcopy(parent);new['version']=m.compiler.VERSION;new['parent_profile_sha256']=parent['sha256']
    definitions={(4,3):([2,4],[2,4,4]),(4,4):([10],[10,10,10]),(3,4):([2],[]),(5,5):([4],[4,4,4])}
    for r in new['cells']:
        pos=(r['column'],r['row'])
        if pos not in candidates:continue
        classes,votes=definitions[pos];source=r['source_fuel_class'];alts=[]
        for klass in classes:
            mapped=m.estimated.fire_v1.FUEL_MAPPING[klass]
            alts.append(dict(assumed_fuel_class=klass,assumed_terrain=mapped['terrain'],exercise_base_initial_fuel=mapped['initial_fuel'],
                             fuel_factor_assumptions=dict(m.compiler.prior.source.EXERCISE_FACTORS)))
        counts={str(k):votes.count(k) for k in sorted(set(votes))}
        r.update(status='new_low_water_mixed_source' if source is not None else 'new_low_water_mixed_fuel_estimated_hypothesis',
          reason=None,surface_assumption='mixed_synthetic_sensitivity',source_neighbor_support=[{'source_class':k} for k in votes],
          fuel_hypothesis=None if source is not None else {'status':'estimated_hypothesis','source_votes':counts},
          synthetic_fuel_class_alternatives=alts,
          low_water_evidence={'both_independent_mapped_land_faces_cover_cell':True,'outside_both_250m_coast_buffers':True,
              'dera_water_polygon_intersection':False,'osm_water_area_intersection':False,'worldcover_samples':{'10':2499,'80':1}},
          cross_water_sensitivity={'crossing_openness_scenarios':m.compiler.CROSSING_SCENARIOS,'default':None,'strict_censor_alternative':True})
    new['sha256']=api._hash({k:v for k,v in new.items() if k!='sha256'});m.PROFILE_PIN=new['sha256']
    choices={(4,3):4,(4,4):10,(3,4):None}
    def convert(selection=choices,cross=.9,opt=True,profile=new):
        return m.convert_verified(profile,parent,parent_inputs,{'cells':strict},{'cells':water},candidate_classes=selection,crossing_proxy=cross,opt_in=opt)
    inputs=convert();assert (4,3,4,.9,.9) in inputs.estimates and (4,4,10,.9,.9) in inputs.estimates
    assert (3,4,.9,.9) in inputs.base.factors and inputs.base.contract.fuel[4][3]==2
    assert inputs.base.contract.surface[5][5]=='unknown' and not inputs.estimated_mask[5][5]
    assert dict(inputs.identity)['selected_cell_count']==3
    for invalid in ({(4,3):10},{(3,4):4},{(4,4):None},{(0,0):4},{}):
        with pytest.raises(ValueError):convert(invalid)
    with pytest.raises(ValueError):convert(cross=.7)
    with pytest.raises(ValueError):convert(opt=False)
    alt=convert({(4,3):2,(4,4):10,(3,4):None},cross=.8)
    assert dict(alt.identity)['added_choices_sha256']!=dict(inputs.identity)['added_choices_sha256']
    case=dict(id='synthetic',seed=7,initial_fire=[[3,3]],warmup_minutes=2,wind=[1,0],events=[],
       weather_schedule=[dict(minute=0,wind=[1,0],temperature_c=25,relative_humidity_pct=35,rain_mm_per_hour=0)])
    case['sha256']=api._hash(case);drafts=dict(version='wildfire-extended-scenario-drafts-v1',status='unqualified-not-runnable',source_catalog_sha256='c',cases=[case]);drafts['sha256']=api._hash(drafts)
    config=dict(work_gain=2,max_strength=5,decay=.5,wind_factor=1,report_delay=2,wear_factor=.1)
    def host(i):return m.LowWaterWeatherFireV2(i,drafts,expected_drafts_sha256=drafts['sha256'],case_id='synthetic',containment_config=config,pressure_scale=1,opt_in_estimated_weather=True)
    first=host(inputs);first.step();saved=first.checkpoint();other=host(inputs);other.restore(saved)
    for _ in range(5):assert first.step()==other.step() and api._encoded(first.checkpoint())==api._encoded(other.checkpoint())
    with pytest.raises(ValueError):host(alt).restore(saved)
    assert not host(inputs)._fire.change({'ignite':[[5,5]]})
    assert not host(inputs)._fire.change({'ignite':[[1,1]]})

if __name__=='__main__':run()
