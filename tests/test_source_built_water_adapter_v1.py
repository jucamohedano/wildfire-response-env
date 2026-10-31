"""Mixed source extension preserves parent masks and explicit unknowns."""
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def test_private_adapter():
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=os.pathsep.join([
      str(ROOT/'envs'),str(ROOT/'envs/wildfire_extended_inputs_v1'),str(ROOT/'envs/wildfire_response/core/src'),
      str(ROOT/'envs/wildfire_containment_v1/src'),str(ROOT/'eval'),str(ROOT/'tests'),str(ROOT)]))
    p=subprocess.run([sys.executable,__file__],cwd=ROOT,env=env,capture_output=True,text=True,timeout=30)
    assert p.returncode==0,p.stdout+p.stderr


def run():
    import copy
    import pytest
    from eval import source_built_water_adapter_v1 as m
    from eval import source_two_component_adapter_v1 as s1,source_two_component_adapter_v2 as s2
    from eval.extended_map_common import digest
    from test_source_two_component_adapter_v2 import fixtures
    parent,strict,water=fixtures();strict['cells'][0][0]['surface']='land'
    strict['cells'][0][0]['fuel_provenance']={'kind':'source'}
    strict['sha256']=digest({k:v for k,v in strict.items() if k!='sha256'});s1.STRICT_PIN=s2.STRICT_PIN=strict['sha256']
    water['cells'][0][0]['fuel_provenance']={'kind':'source'};water['parent_document_sha256']=strict['sha256']
    water['sha256']=digest({k:v for k,v in water.items() if k!='sha256'});s1.WATER_PIN=s2.WATER_PIN=water['sha256']
    parent['source_pins']['strict']=strict['sha256'];parent['source_pins']['water_v2']=water['sha256']
    parent['cells'][0]['status']='unknown';parent['sha256']=digest({k:v for k,v in parent.items() if k!='sha256'})
    s1.PROFILE_PIN=s2.PROFILE_PIN=m.PARENT_PIN=parent['sha256']
    profile=copy.deepcopy(parent);profile['version']='wildfire-domain-built-water-source-sensitivity-profile-v1'
    profile['parent_profile_sha256']=parent['sha256'];profile['extension_code_sha256']=m.COMPILER_PIN
    record=profile['cells'][0];record.update(status='new_built_watercourse_source_sensitivity',reason=None,
      sample_proportions={'vegetation':.6,'built_up':.36,'bare':.04},
      evidence={'source_fuel_class':4,'source_fuel_provenance':{'kind':'source'},'worldcover_samples':water['cells'][0][0]['worldcover_samples'],
                'osm_water_conflicts':water['cells'][0][0]['osm_water_conflicts'],'strict_inland_land':True,
                'both_land_faces_and_coastal_water_screen':True,'mapped_water_area_intersection':False},
      sensitivity={'crossing_openness':[.8,.9,1.]})
    profile['sha256']=digest({k:v for k,v in profile.items() if k!='sha256'});m.PROFILE_PIN=profile['sha256']
    transfer={'vegetation>vegetation':1.,'built>built':1.,'vegetation>built':.2,'built>vegetation':.2}
    choice=dict(vegetation_class=4,vegetation_initial_fuel=.8,built_initial_fuel=.4,crossing_openness=.9,
      bare_assumption='synthetic_inert',water_assumption='synthetic_inert',vegetation_absence='unknown',built_absence='unknown',transfer=transfer)
    def adapt(selected=choice):return m.adapt(profile,parent,strict,water,bbox=(0,0,4,2),selections={(0,0):selected},sea_options={},staging_transfer_anchor=transfer,max_cells=8,opt_in=True)
    result=adapt();c=result['components'][0][0]
    assert c['vegetation']['fraction']==.6 and c['built']['fraction']==.36
    assert c['vegetation']['terrain']=='scrub' and c['built']['terrain']=='built'
    assert c['inert']=={'fraction':.04,'assumption':'synthetic_inert'} and result['crossings'][0][0]==.9
    assert result['components'][1][3]=={'vegetation':'unknown','built':'unknown','inert':'unknown'}
    audit=result['private_audit']['parent_audit']['cells'][0]
    assert audit['v3_status']=='synthetic_built_water_sensitivity' and audit['extension_evidence']['evidence']['source_fuel_class']==4
    uncertain=adapt({**choice,'bare_assumption':'unknown'})
    assert uncertain['components'][0][0]['inert']=='unknown'
    with pytest.raises(ValueError):adapt({**choice,'vegetation_class':2})
    with pytest.raises(ValueError):adapt({**choice,'crossing_openness':.7})
    with pytest.raises(ValueError):m.adapt(profile,parent,strict,water,bbox=(0,0,4,2),selections={(0,0):choice},sea_options={},staging_transfer_anchor=transfer,max_cells=8)

if __name__=='__main__':run()
