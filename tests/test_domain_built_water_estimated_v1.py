"""Estimated mixed cells need source votes and retain explicit uncertainty."""
import copy
import sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'eval'))
import domain_built_water_estimated_profile_v1 as compiler
from eval import source_built_water_estimated_adapter_v1 as adapter
from extended_map_common import digest


def test_domain_vote_and_unsupported_cells(monkeypatch):
    grid={'width':2,'height':1,'transform':[500,0,0,0,-500,500]}
    hist={'10':2000,'50':400,'60':100}
    water={'grid':grid,'cells':[[{'surface':'unknown','fuel_status':'unknown','source_fuel_class':None,
      'fuel_provenance':{'kind':'unknown'},'worldcover_samples':hist,'osm_water_conflicts':[{'kind':'watercourse'}]} for _ in range(2)]],
      'terrain_candidate':[[1,1]]}
    strict={'cells':[[{'surface':'land'},{'surface':'land'}]]}
    records=[{'column':x,'row':0,'status':'unknown','reason':'unknown'} for x in range(2)]
    parent={'grid':grid,'cells':records,'policy':{}};parent['sha256']=digest(parent)
    monkeypatch.setattr(compiler,'BUILT_PARENT_PIN',parent['sha256'])
    monkeypatch.setattr(compiler.prior,'source_support',lambda *a:{})
    monkeypatch.setattr(compiler.prior,'make_spatial_indexes',lambda *a:(None,None,None))
    def neighbors(y,x,*args):
        return ([{'source_class':v} for v in ([2,2,10,10] if x==0 else [2,10])],0)
    monkeypatch.setattr(compiler.prior,'neighbors_for',neighbors)
    mapping={2:{'terrain':'field','initial_fuel':.75},10:{'terrain':'woodland','initial_fuel':.9}}
    result=compiler.extend_missing(parent,strict,water,{},[[True,True]],{},[],[],[],mapping,'test')
    assert result['cells'][0]['fuel_hypothesis']['scenario_classes']==[2,10]
    assert result['cells'][0]['sensitivity']['crossing_default'] is None
    assert result['cells'][1]==parent['cells'][1] and result['counts']['unknown']==1
    assert parent['cells']==records


def test_selected_classes_and_both_fuels_are_guarded():
    rec={'synthetic_fuel_class_alternatives':[{'assumed_fuel_class':2,'exercise_base_initial_fuel':.75,'factor_alternatives':[.8,.9,1.]}],
      'fuel_hypothesis':{'source_votes':{'2':2}},'sensitivity':{'built_initial_fuel':[.2,.4,.8]}}
    choice={'vegetation_class':2,'vegetation_initial_fuel':.675,'built_initial_fuel':.4}
    adapter.validate_estimated_choice(rec,choice)
    adapter.validate_estimated_choice(rec,{**choice,'vegetation_initial_fuel':None})
    for change in ({'vegetation_class':10},{'vegetation_initial_fuel':.7},{'built_initial_fuel':.3}):
        with pytest.raises(ValueError):adapter.validate_estimated_choice(rec,{**choice,**change})
