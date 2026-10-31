"""Pinned source staging preserves sea, unknown and unrepresentable shares."""
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def test_source_staging_contract():
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=os.pathsep.join([
        str(ROOT/'envs'),str(ROOT/'envs/wildfire_extended_inputs_v1'),str(ROOT/'envs/wildfire_response/core/src'),
        str(ROOT/'envs/wildfire_containment_v1/src'),str(ROOT/'eval'),str(ROOT)]))
    p=subprocess.run([sys.executable,__file__],cwd=ROOT,env=env,capture_output=True,text=True,timeout=30)
    assert p.returncode==0,p.stdout+p.stderr


def run():
    import copy
    import pytest
    from eval import source_two_component_adapter_v1 as m
    from eval.extended_map_common import digest
    grid={'width':3,'height':2,'crs':'EPSG:25830','transform':[500,0,0,0,-500,1000]}
    def raw(surface='land',fuel=4,hist=None,waterlines=False):
        return {'surface':surface,'fuel_status':'source' if fuel is not None else 'unknown',
          'source_fuel_class':fuel,'worldcover_samples':hist or {'10':1500,'50':1000},
          'osm_water_conflicts':[{'kind':'watercourse','geometry':'line'}] if waterlines else []}
    cells=[[raw(),raw('unknown',None,{'10':2181,'50':304,'60':15},True),raw('sea',None,{'80':2500})],
           [raw(hist={'10':2499,'0':1}),raw(),raw('unknown',None,{'10':2499,'80':1},True)]]
    strict={'version':'extended-map-resolution-evidence-v1','grid':grid,'cells':copy.deepcopy(cells),
            'source_pins':{'worldcover':m.WORLDCOVER_PIN}}
    strict['sha256']=digest(strict);m.STRICT_PIN=strict['sha256']
    water={'version':'extended-map-resolution-evidence-v2','grid':grid,'cells':copy.deepcopy(cells),
           'terrain_candidate':[[100.,100.,None],[100.,100.,100.]],'source_pins':{'worldcover':m.WORLDCOVER_PIN},
           'parent_document_sha256':strict['sha256']}
    water['sha256']=digest(water);m.WATER_PIN=water['sha256']
    records=[]
    for y,row in enumerate(cells):
        for x,c in enumerate(row):
            status='mapped_sea' if c['surface']=='sea' else 'unknown' if c['surface']=='unknown' else 'mapped_land_source'
            records.append(dict(column=x,row=y,status=status,v2_surface=c['surface'],source_fuel_class=c['source_fuel_class']))
    profile={'version':'wildfire-domain-inland-low-water-sensitivity-profile-v1','grid':grid,'cells':records,
      'parent_profile_sha256':'a17d83005df0f88a5b9cd4e635472debf7246bad66aeaf26ac9539252fb31f59',
      'source_pins':{'strict':strict['sha256'],'water_v2':water['sha256'],'fuel_mapping':m.FUEL_MAPPING_PIN}}
    profile['sha256']=digest(profile);m.PROFILE_PIN=profile['sha256']
    transfer={'vegetation>vegetation':1.,'built>built':1.,'vegetation>built':.2,'built>vegetation':.3}
    def choice(**overrides):
        c=dict(vegetation_class=4,vegetation_initial_fuel=.8,built_initial_fuel=.4,crossing_openness=1.,
               bare_assumption='unknown',water_assumption='unknown',vegetation_absence='unknown',built_absence='unknown',transfer=copy.deepcopy(transfer));c.update(overrides);return c
    selections={(0,0):choice(),(0,1):choice(built_initial_fuel=None),(1,0):choice(vegetation_class=2,crossing_openness=.9,bare_assumption='synthetic_inert'),
                (1,1):choice(transfer={**transfer,'vegetation>built':.6}),
                (2,1):choice(vegetation_class=2,built_initial_fuel=None,water_assumption='synthetic_inert')}
    def adapt(sea=frozenset({(2,0)}),opt=True,prof=profile):
        return m.adapt(prof,strict,water,bbox=(0,0,3,2),selections=selections,mapped_sea_as_inert=sea,transfer=transfer,opt_in=opt)
    out=adapt();records={(r['column'],r['row']):r for r in out['provenance']['cells']}
    accepted=out['components'][0][0]
    assert accepted['vegetation']['fraction']==.6 and accepted['built']['fraction']==.4
    assert accepted['vegetation']['terrain']=='scrub' and accepted['built']['terrain']=='built'
    assert records[(0,0)]['status']=='synthetic_two_component_assumption' and records[(0,0)]['source_fuel_class']==4
    assert records[(0,0)]['source_fuel_status']=='source' and records[(0,0)]['worldcover_epoch']=='2021'
    assert 'unrepresented_worldcover_class' in records[(0,1)]['reasons']
    assert 'built_absence_unknown' in records[(0,1)]['reasons']
    assert out['components'][0][2]=={'vegetation':None,'built':None}
    assert records[(2,0)]['status']=='mapped_sea_synthetic_inert'
    for x,y in ((1,0),(0,1),(1,1),(2,1)):
        assert out['components'][y][x]=={'vegetation':'unknown','built':'unknown'}
    reasons=records[(1,0)]['reasons']
    assert 'profile_cell_unknown' in reasons and 'v1_cannot_represent_inert_share' in reasons
    assert 'v1_cannot_represent_mapped_water_crossing' in reasons
    assert records[(1,0)]['sample_shares']['bare']==15/2500
    assert records[(1,0)]['caller_assumptions']['bare_assumption']=='synthetic_inert'
    assert 'v1_cannot_represent_per_cell_transfer' in records[(1,1)]['reasons']
    assert 'v1_cannot_represent_inert_share' in records[(2,1)]['reasons']
    out2=adapt(sea=frozenset());assert out2['components'][0][2]=={'vegetation':'unknown','built':'unknown'}
    with pytest.raises(ValueError):adapt(sea=frozenset({(0,0)}))
    with pytest.raises(ValueError):adapt(opt=False)
    previous=m.FUEL_MAPPING[4]['terrain'];m.FUEL_MAPPING[4]['terrain']='field'
    try:
        with pytest.raises(ValueError,match='fuel mapping source'):adapt()
    finally:m.FUEL_MAPPING[4]['terrain']=previous
    explicit=copy.deepcopy(selections);explicit[(0,0)]['built_absence']='synthetic_absent'
    conflicted=m.adapt(profile,strict,water,bbox=(0,0,3,2),selections=explicit,mapped_sea_as_inert=frozenset(),transfer=transfer,opt_in=True)
    assert 'built_absence_conflicts_with_samples' in conflicted['provenance']['cells'][0]['reasons']
    assert conflicted['components'][0][0]=={'vegetation':'unknown','built':'unknown'}
    tampered=copy.deepcopy(profile);tampered['cells'][0]['status']='unknown'
    with pytest.raises(ValueError):adapt(prof=tampered)
    tampered_water=copy.deepcopy(water);tampered_water['cells'][0][0]['worldcover_samples']={'10':2500}
    with pytest.raises(ValueError):m.adapt(profile,strict,tampered_water,bbox=(0,0,3,2),selections=selections,mapped_sea_as_inert=frozenset(),transfer=transfer,opt_in=True)
    with pytest.raises(ValueError):m.adapt(profile,strict,water,bbox=(0,0,84,59),selections={},mapped_sea_as_inert=frozenset(),transfer=transfer,opt_in=True)

if __name__=='__main__':run()
