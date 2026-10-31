"""Low-water source and estimated fuel remain explicit, without source substitution."""
import copy
import pytest
from eval import domain_built_water_estimated_policy_v2 as policy
from eval.extended_map_common import digest


def test_low_water_source_estimate_and_unknown(monkeypatch):
    source={'surface':'unknown','fuel_status':'source','source_fuel_class':4,'worldcover_samples':{'10':2499,'80':1}}
    estimate={'surface':'unknown','fuel_status':'unknown','source_fuel_class':None,'worldcover_samples':{'10':2498,'60':1,'80':1}}
    cells=[{'column':0,'row':0,'status':'new_low_water_mixed_source','source_fuel_class':4},
      {'column':1,'row':0,'status':'new_low_water_mixed_fuel_tied_sensitivity_only','source_fuel_class':None,
       'fuel_hypothesis':{'status':'tied_sensitivity_only','confidence':'low_uncalibrated','source_votes':{'2':2,'10':2},
                          'scenario_classes':[2,10],'nominal_class':None}},
      {'column':2,'row':0,'status':'unknown','source_fuel_class':None}]
    profile={'version':'wildfire-domain-built-water-estimated-sensitivity-profile-v1','cells':cells};profile['sha256']=digest(profile)
    water={'cells':[[source,estimate,copy.deepcopy(estimate)]]}
    monkeypatch.setattr(policy,'PROFILE_PIN',profile['sha256'])
    monkeypatch.setattr(policy.parent_policy,'build_policy',lambda a,b:({}, {}, {'sha256':policy.PARENT_POLICY_PIN}))
    selections,sea,manifest=policy.build_policy(profile,{},water)
    assert set(selections)=={(0,0),(1,0)} and not sea
    assert selections[(0,0)]['vegetation_class']==4 and selections[(0,0)]['vegetation_initial_fuel']==.8
    assert selections[(1,0)]['vegetation_class']==2 and selections[(1,0)]['vegetation_initial_fuel']==.675
    assert all(v['crossing_openness']==.9 and v['water_assumption']=='synthetic_inert' for v in selections.values())
    assert manifest['counts']=={'low_water_estimated':1,'low_water_source':1}
    altered=copy.deepcopy(water);altered['cells'][0][0]['source_fuel_class']=2
    with pytest.raises(ValueError,match='retained source class'):policy.build_policy(profile,{},altered)
