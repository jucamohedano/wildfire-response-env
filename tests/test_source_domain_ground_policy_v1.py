"""Synthetic domain walking never promotes estimated or unsupported cells."""
from types import SimpleNamespace
from wildfire_source_domain_ground_v1.walk import _source_supported


def test_walk_inventory_requires_original_and_domain_source_land():
    cells=[[dict(surface='land',fuel_status='source',source_fuel_class=4) for _ in range(5)]]
    v2=dict(cells=cells,terrain_candidate=[[100.,100.,100.,100.,100.]])
    v4=dict(cells=[],additional_dera_censors=[])
    contract=SimpleNamespace(domain=((True,)*5,),surface=(('land','unknown','mixed','land','land'),),
        fire_support=((True,False,False,True,True),),fuel=((4,)*5,),elevation=((100.,100.,100.,100.,101.),))
    masks=SimpleNamespace(source_mask=((True,True,True,False,True),),estimated_mask=((False,False,False,True,False),))
    assert _source_supported(v2,v4,contract,masks)=={(0,0)}


def test_documented_candidate_reconstructed_against_domain_handover(monkeypatch):
    import copy,hashlib
    from pathlib import Path
    import pytest
    import adapter_v1 as api
    from shapely.geometry import Point,box
    from shapely.strtree import STRtree
    from wildfire_source_domain_ground_v1 import walk as bridge
    from wildfire_source_domain_ground_v1.host import DomainProfileGroundIncidentV1
    n=24;t=[500,0,0,0,-500,12000]
    cells=[[dict(surface='land',fuel_status='source',source_fuel_class=4) for _ in range(n)] for _ in range(n)]
    heights=[[100.]*n for _ in range(n)]
    v2=dict(grid=dict(width=n,height=n,crs='EPSG:25830',transform=t),cells=cells,terrain_candidate=heights)
    v4=dict(cells=[],additional_dera_censors=[])
    land=tuple(tuple('land' for _ in range(n)) for _ in range(n));truth=tuple(tuple(True for _ in range(n)) for _ in range(n))
    source=tuple(tuple((x,y)!=(0,0) for x in range(n)) for y in range(n))
    estimated=tuple(tuple((x,y)==(0,0) for x in range(n)) for y in range(n))
    contract=SimpleNamespace(width=n,height=n,crs='EPSG:25830',transform=tuple(t),domain=truth,surface=land,
        fire_support=truth,fuel=tuple(tuple(4 for _ in range(n)) for _ in range(n)),
        elevation=tuple(tuple(100. for _ in range(n)) for _ in range(n)))
    masks=SimpleNamespace(source_mask=source,estimated_mask=estimated)
    water=dict(bounds=box(-100,-100,12100,12100),land=box(-100,-100,12100,12100),
               osm=STRtree([Point(100,100).buffer(1)]),dera=STRtree([Point(100,100).buffer(1)]))
    support=bridge._source_supported(v2,v4,contract,masks);assert (0,0) not in support
    position=[5250.,5750.];fire=[[12,12]];wind=[0.,0.]
    variant=dict(resource_kind='crew',work_authorization='none',pre_handover_route='unknown',
        original_case_sha256='synthetic-original',fire_draft_sha256='synthetic-fire',initial_position=position,
        resource_budget=dict(duty_end_at=100,work_remaining=50,total_work_remaining=50,water_litres=0,capacity_litres=0))
    variant['sha256']=api._hash(variant)
    proposal=bridge.walk.plan(position,fire,wind,support,heights,v2['grid'],water);proposal['parent_sha256']=variant['sha256']
    parent={'cases':[dict(original_case_id=f'case-{i}',variants=[]) for i in range(12)]}
    parent['cases'][4]['variants']=[variant]
    cases=[dict(original_case_id=f'case-{i}',candidates=[]) for i in range(12)]
    cases[4]['candidates']=[proposal,copy.deepcopy(proposal)]
    candidate=dict(version=bridge.walk.VERSION,status='private_synthetic_hypotheses_not_host_integrated',
        pins=dict(parent=bridge.walk.PARENT_PIN,source=hashlib.sha256(Path(bridge.walk.__file__).read_bytes()).hexdigest(),
                  water_v2=bridge.staging.V2_PIN,mixed_v4=bridge.staging.V4_PIN),cases=cases)
    candidate['sha256']=api._hash(candidate)
    def host(observed_fire=fire,observed_wind=wind):
        value=object.__new__(DomainProfileGroundIncidentV1);value._ensure=lambda:None
        value._minute=0;value._calls=[];value._status='active'
        value._case=dict(id='case-4',sha256='synthetic-original');value._firecase=dict(sha256='synthetic-fire')
        value._inputs=SimpleNamespace(identity=(('water_v2',bridge.staging.V2_PIN),('overlay_v4',bridge.staging.V4_PIN)),contract=contract)
        value._domain_inputs=masks;value._handover_fire=copy.deepcopy(observed_fire)
        value._identity={'version':'synthetic-domain'};value.checkpoint=lambda:{'version':'synthetic'}
        value.situation=lambda:dict(status='active',minute=0,wind=copy.deepcopy(observed_wind))
        return value
    monkeypatch.setattr(bridge,'_document',lambda *args:None)
    monkeypatch.setattr(bridge.segment,'load_water_sources',lambda:water)
    monkeypatch.setattr(bridge.original,'_source_identity',lambda:'synthetic-parent-code')
    kwargs=dict(candidate=candidate,expected_candidate_sha256=candidate['sha256'],parent=parent,v2=v2,v4=v4,
                candidate_index=0,public_crew_id='crew',opt_in=True)
    identity=bridge.configuration_identity(host(),**kwargs)
    episode=bridge.DomainSourceWalkV1(host(),expected_identity=identity,**kwargs)
    assert (0,0) not in episode._supported and episode._proposal==proposal
    with pytest.raises(ValueError,match='does not match'):
        bridge.DomainSourceWalkV1(host(observed_fire=[[13,12]]),expected_identity=identity,**kwargs)
    with pytest.raises(ValueError):
        bridge.DomainSourceWalkV1(host(observed_wind=[-1,0]),expected_identity=identity,**kwargs)
