"""Pinned staging to v2 grids without actor observations or silent inference."""
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def test_source_to_v2_staging():
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=os.pathsep.join([
        str(ROOT/'envs'),str(ROOT/'envs/wildfire_extended_inputs_v1'),str(ROOT/'envs/wildfire_response/core/src'),
        str(ROOT/'envs/wildfire_containment_v1/src'),str(ROOT/'eval'),str(ROOT/'tests'),str(ROOT)]))
    p=subprocess.run([sys.executable,__file__],cwd=ROOT,env=env,capture_output=True,text=True,timeout=90)
    assert p.returncode==0,p.stdout+p.stderr


def fixtures(width=4,height=2):
    import copy
    from eval import source_two_component_adapter_v1 as old
    from eval.extended_map_common import digest
    grid={'width':width,'height':height,'crs':'EPSG:25830','transform':[500,0,0,0,-500,height*500]}
    def raw(surface='land',fuel=4,hist=None,stream=False):
        return {'surface':surface,'fuel_status':'source' if fuel is not None else 'unknown','source_fuel_class':fuel,
                'worldcover_samples':hist or {'10':1500,'50':1000},
                'osm_water_conflicts':[{'kind':'watercourse','geometry':'line'}] if stream else []}
    cells=[[raw() for _ in range(width)] for _ in range(height)]
    if width==4:
        cells[0][0]=raw('unknown',4,{'10':1500,'50':900,'60':100},True)
        cells[0][1]=raw('land',4,{'10':1500,'50':1000})
        cells[0][2]=raw('sea',None,{'80':2500})
        cells[0][3]=raw('unknown',None,{'10':2499,'80':1},True)
        cells[1][1]=raw('unknown',None,{'10':1600,'50':875,'80':25},True)
        cells[1][2]=raw('land',4,{'50':2500})
    strict={'version':'extended-map-resolution-evidence-v1','grid':grid,'cells':copy.deepcopy(cells),'source_pins':{'worldcover':old.WORLDCOVER_PIN}}
    strict['sha256']=digest(strict);old.STRICT_PIN=strict['sha256']
    water={'version':'extended-map-resolution-evidence-v2','grid':grid,'cells':copy.deepcopy(cells),
           'terrain_candidate':[[None if c['surface']=='sea' else 100. for c in row] for row in cells],
           'source_pins':{'worldcover':old.WORLDCOVER_PIN},'parent_document_sha256':strict['sha256']}
    water['sha256']=digest(water);old.WATER_PIN=water['sha256']
    records=[]
    for y,row in enumerate(cells):
        for x,c in enumerate(row):
            status='mapped_sea' if c['surface']=='sea' else 'unknown' if c['surface']=='unknown' else 'mapped_land_source'
            if width==4 and (x,y)==(0,0):status='existing_v4_mixed_source'
            if width==4 and (x,y)==(1,1):status='new_low_water_mixed_fuel_tied_sensitivity_only'
            r=dict(column=x,row=y,status=status,v2_surface=c['surface'],source_fuel_class=c['source_fuel_class'])
            if width==4 and (x,y)==(1,1):r['fuel_hypothesis']={'source_votes':{'2':2,'4':2}}
            records.append(r)
    profile={'version':'wildfire-domain-inland-low-water-sensitivity-profile-v1','grid':grid,'cells':records,
             'parent_profile_sha256':'a17d83005df0f88a5b9cd4e635472debf7246bad66aeaf26ac9539252fb31f59',
             'source_pins':{'strict':strict['sha256'],'water_v2':water['sha256'],'fuel_mapping':old.FUEL_MAPPING_PIN}}
    profile['sha256']=digest(profile);old.PROFILE_PIN=profile['sha256']
    from eval import source_two_component_adapter_v2 as new
    new.PROFILE_PIN=old.PROFILE_PIN;new.STRICT_PIN=old.STRICT_PIN;new.WATER_PIN=old.WATER_PIN
    return profile,strict,water


def run():
    import copy
    import pytest
    from eval import source_two_component_adapter_v2 as m
    from eval.two_component_fire_v2 import TwoComponentFireV2
    from test_two_component_fire_v1 import fire
    t={'vegetation>vegetation':1.,'built>built':1.,'vegetation>built':.2,'built>vegetation':.3}
    def choice(**kw):
        value=dict(vegetation_class=4,vegetation_initial_fuel=.8,built_initial_fuel=.4,crossing_openness=.9,
                   bare_assumption='synthetic_inert',water_assumption='synthetic_inert',
                   vegetation_absence='unknown',built_absence='unknown',transfer=copy.deepcopy(t))
        value.update(kw);return value
    profile,strict,water=fixtures()
    other={**t,'vegetation>built':.7}
    choices={(0,0):choice(transfer=other),(0,1):choice(),(1,0):choice(bare_assumption='unknown',water_assumption='unknown'),
             (1,1):choice(vegetation_class=2), (3,0):choice(),
             (2,1):choice(vegetation_class=None,vegetation_initial_fuel=None,vegetation_absence='synthetic_absent')}
    result=m.adapt(profile,strict,water,bbox=(0,0,4,2),selections=choices,
                   sea_options={(2,0):{'crossing_openness':1.,'transfer':t}},staging_transfer_anchor=t,max_cells=8,opt_in=True)
    x=result['components'][0][0]
    assert x['vegetation']['fraction']==.6 and x['built']['fraction']==.36
    assert x['inert']=={'fraction':.04,'assumption':'synthetic_inert'}
    assert result['crossings'][0][0]==.9 and result['transfers'][0][0]==other
    assert result['components'][0][1]['inert']=='unknown'
    assert result['components'][0][2]=={'vegetation':None,'built':None,'inert':{'fraction':1.,'assumption':'synthetic_inert'}}
    assert result['components'][0][3]=={'vegetation':'unknown','built':'unknown','inert':'unknown'}
    assert result['components'][1][2]['vegetation']=='unknown'
    assert result['components'][1][2]['built']['terrain']=='built'
    assert 'retained_source_fuel_conflicts_with_vegetation_absence' in next(r for r in result['private_audit']['cells'] if (r['column'],r['row'])==(2,1))['v2_reasons']
    assert result['components'][1][3]=={'vegetation':'unknown','built':'unknown','inert':'unknown'}
    assert result['crossings'][1][3]=='unknown' and result['transfers'][1][3]=='unknown'
    assert result['components'][1][1]['inert']=={'fraction':.01,'assumption':'synthetic_inert'}
    assert result['private_audit']['profile_sha256']==profile['sha256'] and 'actor_observation' not in result
    with pytest.raises(ValueError):m.adapt(profile,strict,water,bbox=(0,0,4,2),selections={(9,9):choice()},sea_options={},staging_transfer_anchor=t,max_cells=8,opt_in=True)
    with pytest.raises(ValueError):m.adapt(profile,strict,water,bbox=(0,0,4,2),selections={},sea_options={(1,0):{'crossing_openness':1.,'transfer':t}},staging_transfer_anchor=t,max_cells=8,opt_in=True)
    with pytest.raises(ValueError):m.adapt(profile,strict,water,bbox=(0,0,4,2),selections={},sea_options={},staging_transfer_anchor=t,max_cells=8)
    old_pin=m.stage.PROFILE_PIN;m.stage.PROFILE_PIN='0'*64
    try:
        with pytest.raises(ValueError,match='source differs'):
            m.adapt(profile,strict,water,bbox=(0,0,4,2),selections={},sea_options={},staging_transfer_anchor=t,max_cells=8,opt_in=True)
    finally:m.stage.PROFILE_PIN=old_pin
    # Code changing after an earlier tile must not be attributed to its old pin.
    from unittest.mock import patch
    original_read=Path.read_bytes
    for target in (Path(m.__file__).resolve(),Path(m.stage.__file__).resolve(),Path(m.model.__file__).resolve()):
        seen=[0]
        def changed(path):
            raw=original_read(path)
            if path.resolve()==target:
                seen[0]+=1
                if seen[0]>=2:return raw+b'\n# simulated mutation'
            return raw
        with patch.object(Path,'read_bytes',changed):
            with pytest.raises(ValueError,match='source changed|source differs'):
                m.adapt(profile,strict,water,bbox=(0,0,4,2),selections=choices,
                        sea_options={},staging_transfer_anchor=t,max_cells=8,opt_in=True)
        assert seen[0]>=2
    # The output is directly shape-valid for the model, but the owner here is
    # purely synthetic; it is not a source fire/weather host.
    f=fire();f.width=4;f.height=2;f.heights=copy.deepcopy(result['heights'])
    f.cells=[[dict(terrain=None,fuel=0.,initial_fuel=0.,heat=0.,age=0,wet=0,burned=0.) for _ in range(4)] for _ in range(2)]
    model=TwoComponentFireV2(f,result['components'],transfers=result['transfers'],crossings=result['crossings'],max_cells=8,opt_in=True)
    assert model.step() and model.fire._containment._minute==1
    # Full-size source-shaped staging works without turning unselected cells into sea.
    profile,strict,water=fixtures(84,59)
    full=m.adapt(profile,strict,water,bbox=(0,0,84,59),selections={},sea_options={},staging_transfer_anchor=t,max_cells=5000,opt_in=True)
    assert full['grid']['width']*full['grid']['height']==4956
    assert all(c=={'vegetation':'unknown','built':'unknown','inert':'unknown'} for row in full['components'] for c in row)
    assert len(full['private_audit']['stage_identities'])==2
    # The final source check also runs after both 4,096-cell staging tiles.
    target=Path(m.__file__).resolve();seen=[0]
    def changed_after_entry(path):
        raw=original_read(path)
        if path.resolve()==target:
            seen[0]+=1
            if seen[0]>=2:return raw+b'\n# mid-call mutation'
        return raw
    with patch.object(Path,'read_bytes',changed_after_entry):
        with pytest.raises(ValueError,match='adapter source changed'):
            m.adapt(profile,strict,water,bbox=(0,0,84,59),selections={},sea_options={},staging_transfer_anchor=t,max_cells=5000,opt_in=True)
    assert seen[0]==2

if __name__=='__main__':run()
