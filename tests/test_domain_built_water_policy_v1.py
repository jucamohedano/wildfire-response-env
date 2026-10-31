"""Fixed extension selects all eligible source classes without case inputs."""
import copy
import sys
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'eval'))
import domain_built_water_policy_v1 as policy
from extended_map_common import digest


def test_fixed_policy_retains_source_and_selects_every_extension(monkeypatch):
    grid={'width':3,'height':1};water={'cells':[[{'source_fuel_class':2,'worldcover_samples':{'10':2200,'50':300}},
        {'source_fuel_class':4,'worldcover_samples':{'10':2000,'50':400,'60':100}},
        {'source_fuel_class':None,'worldcover_samples':{'10':2500}}]]}
    records=[{'column':0,'row':0,'status':'new_built_watercourse_source_sensitivity','source_fuel_class':2},
             {'column':1,'row':0,'status':'new_built_watercourse_source_sensitivity','source_fuel_class':4},
             {'column':2,'row':0,'status':'unknown','source_fuel_class':None}]
    parent={'grid':grid};profile={'version':'wildfire-domain-built-water-source-sensitivity-profile-v1',
        'parent_profile_sha256':policy.parent_policy.PROFILE_PIN,'grid':grid,'cells':records}
    profile['sha256']=digest(profile);monkeypatch.setattr(policy,'PROFILE_PIN',profile['sha256'])
    monkeypatch.setattr(policy.parent_policy,'build_policy',lambda a,b:({}, {}, {'sha256':policy.PARENT_POLICY_PIN}))
    choices,sea,manifest=policy.build_policy(profile,parent,water)
    assert set(choices)=={(0,0),(1,0)} and not sea and manifest['counts']['source_mixed_selected']==2
    assert choices[(0,0)]['vegetation_class']==2 and choices[(1,0)]['vegetation_class']==4
    assert all(c['crossing_openness']==.9 and c['bare_assumption']=='synthetic_inert' for c in choices.values())
    bad=copy.deepcopy(water);bad['cells'][0][0]['source_fuel_class']=4
    with pytest.raises(ValueError):policy.build_policy(profile,parent,bad)
