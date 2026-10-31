"""Fixed source/estimated policy is domain-wide and preserves uncertainty."""
import copy
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def test_policy():
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=os.pathsep.join([
        str(ROOT/'envs'),str(ROOT/'envs/wildfire_extended_inputs_v1'),str(ROOT/'envs/wildfire_response/core/src'),
        str(ROOT/'envs/wildfire_containment_v1/src'),str(ROOT/'eval'),str(ROOT)]))
    p=subprocess.run([sys.executable,__file__],cwd=ROOT,env=env,capture_output=True,text=True,timeout=30)
    assert p.returncode==0,p.stdout+p.stderr


def run():
    import pytest
    from eval import domain_component_policy_v2 as m
    from eval.extended_map_common import digest
    def item(klass):
        f=m.fuel_data.FUEL_MAPPING[klass]
        return dict(assumed_fuel_class=klass,assumed_terrain=f['terrain'],exercise_base_initial_fuel=f['initial_fuel'],
                    fuel_factor_assumption=.9,modeled_initial_fuel=round(f['initial_fuel']*.9,6))
    weak=dict(status='ambiguous_sensitivity_hypothesis',confidence='low_uncalibrated',source_votes={'2':2,'4':1},
              nominal_class_assumption=2,scenarios={'nominal':item(2)})
    tie=dict(status='tied_sensitivity_only',confidence='low_uncalibrated',source_votes={'2':2,'4':2},scenario_classes=[2,4],nominal_class=None)
    water={'version':'extended-map-resolution-evidence-v2','grid':{'width':5,'height':1},'cells':[[
        {'surface':'land','source_fuel_class':4,'worldcover_samples':{'10':2400,'50':100}},
        {'surface':'unknown','source_fuel_class':None,'worldcover_samples':{'10':2500}},
        {'surface':'unknown','source_fuel_class':None,'worldcover_samples':{'10':2500}},
        {'surface':'sea','source_fuel_class':None,'worldcover_samples':{'80':2500}},
        {'surface':'unknown','source_fuel_class':None,'worldcover_samples':{'10':2499,'80':1}}]]}
    water['sha256']=digest(water);m.WATER_PIN=water['sha256']
    records=[dict(column=0,row=0,status='mapped_land_source',source_fuel_class=4,v2_surface='land'),
      dict(column=1,row=0,status='new_mixed_fuel_ambiguous_sensitivity_hypothesis',source_fuel_class=None,v2_surface='unknown',fuel_hypothesis=weak),
      dict(column=2,row=0,status='new_mixed_fuel_tied_sensitivity_only',source_fuel_class=None,v2_surface='unknown',fuel_hypothesis=tie),
      dict(column=3,row=0,status='mapped_sea',source_fuel_class=None,v2_surface='sea'),
      dict(column=4,row=0,status='new_low_water_mixed_fuel_estimated_hypothesis',source_fuel_class=None,v2_surface='unknown')]
    profile={'version':'wildfire-domain-inland-low-water-sensitivity-profile-v1','grid':water['grid'],'cells':records,
             'source_pins':{'water_v2':water['sha256'],'fuel_mapping':m.MAPPING_FILE_PIN}}
    profile['sha256']=digest(profile);m.PROFILE_PIN=profile['sha256']
    choices,sea,manifest=m.build_policy(profile,water)
    assert set(choices)=={(0,0),(1,0),(2,0)} and set(sea)=={(3,0)}
    assert choices[(0,0)]['vegetation_class']==4 and choices[(0,0)]['vegetation_initial_fuel']==.8
    assert choices[(1,0)]['vegetation_class']==2 and choices[(1,0)]['vegetation_initial_fuel']==.675
    assert choices[(2,0)]['vegetation_class']==2 and choices[(2,0)]['vegetation_initial_fuel']==.675
    assert choices[(1,0)]['crossing_openness']==.9 and choices[(0,0)]['crossing_openness']==1.
    assert choices[(2,0)]['built_absence']=='synthetic_absent' and manifest['counts']['estimated_tied_sensitivity_only']==1
    altered=copy.deepcopy(profile);altered['cells'][1]['fuel_hypothesis']['nominal_class_assumption']=4
    with pytest.raises(ValueError):m.build_policy(altered,water)
    mismatch=copy.deepcopy(weak);mismatch['nominal_class_assumption']=4
    with pytest.raises(ValueError):m._estimate({'fuel_hypothesis':mismatch})

if __name__=='__main__':run()
