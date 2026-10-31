"""Shared operations with a real fresh component model and synthetic source lineage."""
import copy
from types import SimpleNamespace
import adapter_v1 as api
import pytest
from envs.wildfire_response.incident_day.events import EventBus
from envs.wildfire_response.incident_day.operations import Operations
from eval import fresh_two_component_weather_v2 as legacy
from eval import fresh_two_component_operations_v1 as component
from eval.extended_map_common import digest
from wildfire_core.incident_day import fixture_inputs
from test_domain_population_host_v2 import make as old_make
import wildfire_fresh_component_shared_v1 as shared
from wildfire_fresh_component_shared_v1 import source_host,hold,population


def fresh_owner(monkeypatch,*,boundary_unknown=False):
    monkeypatch.setattr(legacy,'_ensure_code',lambda:None)
    parent=fixture_inputs()[0];grid=dict(width=84,height=59,crs='EPSG:25830',transform=[500,0,0,0,-500,12000])
    profile={'version':'synthetic','grid':grid};profile['sha256']=api._hash(profile)
    monkeypatch.setattr(legacy,'PROFILE_PIN',profile['sha256'])
    record=dict(minute=0,wind=[0.,0.],temperature_c=25,relative_humidity_pct=35,rain_mm_per_hour=0,received_at=0)
    case=dict(id='synthetic',seed=3,initial_fire=[[12,12]],warmup_minutes=0,wind=[0.,0.],events=[],weather_schedule=[record]);case['sha256']=api._hash(case)
    monkeypatch.setattr(legacy,'build_drafts',lambda *args:{'cases':[case],'sha256':'d'*64})
    catalog={'version':'synthetic','inputs_sha256':parent['sha256']};catalog['sha256']=api._hash(catalog)
    transfer={'vegetation>vegetation':1.,'vegetation>built':.2,'built>vegetation':.3,'built>built':1.}
    inert={'vegetation':None,'built':None,'inert':{'fraction':1.,'assumption':'synthetic_inert'}}
    cells=[[copy.deepcopy(inert) for _ in range(84)] for _ in range(59)]
    veg=dict(terrain='field',fraction=1.,fuel=.8,initial_fuel=.8,heat=0.,age=0,wet=0,burned=0.)
    for y in range(16):
        for x in range(16):
            if (x,y)!=(13,12) or boundary_unknown:cells[y][x]={'vegetation':copy.deepcopy(veg),'built':None,'inert':None}
    if boundary_unknown:cells[12][14]={'vegetation':'unknown','built':'unknown','inert':'unknown'}
    heights=[[100.]*84 for _ in range(59)];crossings=[[1.]*84 for _ in range(59)]
    transfers=[[copy.deepcopy(transfer) for _ in range(84)] for _ in range(59)]
    audit={'bbox':[0,0,84,59],'secret':'private-evidence'}
    ident={'profile_sha256':profile['sha256'],'model_sha256':component.STAGING_MODEL_PIN,
        'stage_sha256':component.staging_adapter.STAGE_PIN,'adapter_sha256':component.STAGING_ADAPTER_PIN,
        'bbox':[0,0,84,59],'provenance_sha256':digest(audit)}
    for name,value in (('components',cells),('heights',heights),('crossings',crossings),('transfers',transfers)):ident[name+'_sha256']=digest(value)
    staged=dict(version=component.staging_adapter.VERSION,role='private_v2_grid_staging_not_source_fire_owner',grid=grid,
        components=cells,heights=heights,crossings=crossings,transfers=transfers,private_audit=audit,identity=ident)
    config=dict(work_gain=5,max_strength=10,decay=0,wind_factor=0,report_delay=2,wear_factor=0)
    f=component.FreshTwoComponentOperationsV1(staged,profile,parent,catalog,expected_staging_identity=digest(ident),
        expected_parent_sha256=parent['sha256'],expected_catalog_sha256=catalog['sha256'],case_id='synthetic',
        ignition_components={(12,12):'vegetation'},containment_config=config,pressure_scale=.01,day_horizon=1440,opt_in=True)
    doc=dict(version='wildfire-extended-inputs-v1',grid=grid,domain_mask=[[True]*84 for _ in range(59)],
        surface=[['land']*84 for _ in range(59)],fuel=[[4]*84 for _ in range(59)],elevation=[[100.]*84 for _ in range(59)],
        access=[['unknown']*84 for _ in range(59)],source_pins={k:c*64 for k,c in zip(('surface','fuel','elevation','access'),'abcd')})
    contract=api.load_inputs(doc,api._hash(doc),expected_source_pins=doc['source_pins'],opt_in=True)
    return f,contract


def make(monkeypatch,*,aircraft=False,boundary_unknown=False):
    old,routes=old_make(monkeypatch);walk=old._walk;h=walk._host;fresh,contract=fresh_owner(monkeypatch,boundary_unknown=boundary_unknown)
    h._fresh=fresh;h._inputs=SimpleNamespace(contract=contract)
    h._domain_inputs=SimpleNamespace(base=h._inputs,source_mask=tuple(tuple(True for _ in range(84)) for _ in range(59)),
        estimated_mask=tuple(tuple(False for _ in range(84)) for _ in range(59)))
    h._legacy_fire_support=frozenset((x,y) for y in range(59) for x in range(84))
    h._fire=source_host._FireView(h);h._weather=source_host._WeatherView(h)
    h._handover_fire=copy.deepcopy(h._fire.burning());h._minute=0;h._status='active'
    h._ops.profile['facilities']={}
    if aircraft:
        unit=dict(id='air',kind='aircraft',position=[6250.,5750.],base_position=[6250.,5750.],state='working',
                  target_grid=[12,12],clearance=True,water_litres=1000,capacity_litres=1000,work_remaining=5,
                  total_work_remaining=5,fuel_minutes=30,fuel_capacity_minutes=30,facility_id='base')
        profile=copy.deepcopy(h._ops.profile);profile['facilities']={'base':dict(position=unit['base_position'],
            allowed_kinds=['aircraft'],water_litres=10000,refuel=True)}
        h._ops=Operations([unit],list(h._ops.destinations.values()),list(h._ops.groups.values()),profile,return_planner=h._return_plan)
        h._known={'air':copy.deepcopy(unit)}
    def checkpoint():
        return dict(minute=h._minute,status=h._status,fresh=h._fresh.checkpoint(),ops=h._ops.checkpoint(),bus=h._bus.checkpoint(),
            dose=copy.deepcopy(h._dose),closed=copy.deepcopy(h._closed),air=h._air_until,drops=copy.deepcopy(h._drop_audit),seen=h._seen)
    def restore(v,*,expected_checkpoint_sha256):
        assert api._hash(v)==expected_checkpoint_sha256
        h._fresh.restore(v['fresh'],expected_checkpoint_sha256=v['fresh']['sha256'])
        h._ops=Operations.restore(v['ops'],return_planner=h._return_plan);h._bus=EventBus.restore(v['bus'])
        h._minute=v['minute'];h._status=v['status'];h._dose=copy.deepcopy(v['dose']);h._closed=copy.deepcopy(v['closed'])
        h._air_until=v['air'];h._drop_audit=copy.deepcopy(v['drops']);h._seen=v['seen']
    h.checkpoint=checkpoint;h.restore=restore;walk._seal=api._hash(h.checkpoint())
    monkeypatch.setattr(hold,'FreshComponentWalkV1',type(walk))
    anchor=old._anchors;Anchor=type(anchor);Routes=type(routes)
    monkeypatch.setattr(shared,'PopulationAnchorAdapterV1',Anchor);monkeypatch.setattr(shared,'PopulationSegmentMovementV1',Routes)
    monkeypatch.setattr(population,'FreshComponentSourceHostV1',type(h))
    monkeypatch.setattr(population,'PopulationAnchorAdapterV1',Anchor);monkeypatch.setattr(population,'PopulationSegmentMovementV1',Routes)
    monkeypatch.setattr(population,'_BOUND',(type(h),Operations,Anchor,Routes,population.weather.conditions,population.clock_module.solar_daylight))
    ident=hold.configuration_identity(walk,opt_in=True);hld=hold.FreshComponentHoldV1(walk,expected_identity=ident,opt_in=True)
    return shared.FreshComponentSharedV1(hld,anchor,routes,opt_in=True),routes


def test_shared_population_and_component_fire_one_tick(monkeypatch):
    env,_=make(monkeypatch);env.dispatch_crew('crew');env.protect('cohort-1','shelter')
    assert env._host._minute==2==env._host._fresh._minute==env._host._ops.last_minute
    assert env._host._fresh._model.fire.tick==2
    assert 'private-group' not in str(env.situation())
    env.advance(4)
    assert env._host._ops.groups['private-group']['status']=='sheltering'


def test_shared_evacuation_revalidates_and_checkpoint_continues(monkeypatch):
    env,routes=make(monkeypatch);option=env.evacuation_options('cohort-1','destination-1')['options'][0]['id']
    env.protect('cohort-1','evacuate','destination-1',option);env.advance(3)
    before=copy.deepcopy(env._host._ops.groups['private-group']['position']);routes.blocked=True;env.advance()
    assert env._host._ops.groups['private-group']['position']==before
    routes.blocked=False;env.advance();assert env._host._ops.groups['private-group']['position']!=before
    snapshot=env.checkpoint();expected=env.advance(1);clone,_=make(monkeypatch)
    clone.restore(snapshot,expected_checkpoint_sha256=api._hash(snapshot));assert clone.advance(1)==expected


def test_aircraft_drop_charges_once_and_uses_component_tick(monkeypatch):
    env,_=make(monkeypatch,aircraft=True);env.advance()
    unit=env._host._ops.resources['air']
    assert unit['work_remaining']==4 and unit['total_work_remaining']==4 and unit['water_litres']==0
    assert env._host._minute==env._host._fresh._minute==env._host._ops.last_minute==1
    assert len(env._host._drop_audit)==1 and env._host._fresh._model._components[12][12]['vegetation']['wet']>0


def test_ground_work_and_population_share_one_tick(monkeypatch):
    env,_=make(monkeypatch);walk=env._walk;hld=env._hold
    # Trusted synthetic fixture places the crew at its documented site; the
    # production constructor still requires the validated walk and arrival.
    walk._ground.update(phase='front_no_work',position=copy.deepcopy(walk._proposal['site_position']))
    walk._ground_seal=api._hash(walk._ground);hld._hold.update(phase='holding',due_at=None)
    hld._hold_seal=api._hash(hld._hold);env._seal=api._hash(hld.checkpoint())
    before=walk._ground['work_remaining'];env.protect('cohort-1','shelter')
    assert walk._ground['work_remaining']==before-1 and hld._hold['work_minutes']==1
    assert len(env._host._fresh._last_costs)==1 and env._host._fresh._model.fire._containment._minute==1


def test_actual_component_censor_discards_shared_operations_and_restores(monkeypatch):
    from eval import incident_weather_v1 as weather
    env,_=make(monkeypatch,boundary_unknown=True);f=env._host._fresh;model=f._model
    conditions=weather.conditions(f._profile,1);row=copy.deepcopy(conditions);row.update(minute=0,received_at=0)
    selected=weather.WeatherProfile.from_records([row]);previous=weather.initial_state(selected,moisture_proxy=f._weather_state['moisture_proxy'])
    updated=weather.advance(selected,previous,1);model.fire.wind=tuple(conditions['wind'])
    model.fire.rules['spread_factor']=weather.spread_factor(updated,base_factor=.5)
    model.fire.rules['ignition_probability_per_eligible_cell']=100.
    model._components[12][12]['vegetation']['age']=model.fire.spread_interval(1,0)-1
    model.fire.rules['spread_factor']=.5;model._sync_projection()
    env._walk._seal=api._hash(env._host.checkpoint());env._seal=api._hash(env._hold.checkpoint())
    saved=env.checkpoint();ops=env._host._ops.checkpoint();rng=model.fire.rng.getstate()
    result=env.protect('cohort-1','shelter')
    assert result['status']=='censored' and result['minute']==0
    assert env._host._ops.checkpoint()==ops and model.fire.rng.getstate()==rng
    with pytest.raises(api.CensoredError):env.reports()
    env.restore(saved,expected_checkpoint_sha256=api._hash(saved));assert env.checkpoint()==saved


def test_center_response_envelope_is_publicly_aliased(monkeypatch):
    env,_=make(monkeypatch);bus=env._host._bus
    bus.emit('center','status_response',{'text':'synthetic'},0,role='center',delay=0)
    bus.tick(0,{})
    env._walk._seal=api._hash(env._host.checkpoint());env._seal=api._hash(env._hold.checkpoint())
    raw=[r for r in bus.reports if r['kind']=='status_response']
    public=[r for r in env.reports(limit=200) if r['kind']=='status_response']
    assert raw and raw[0]['source']=='center' and public[0]['source']==env.situation()['centers'][0]


def test_modeled_fire_support_does_not_promote_unknown_access(monkeypatch):
    from dataclasses import replace
    from wildfire_fresh_component_shared_v1 import scored
    env,_=make(monkeypatch);h=env._host;c=h._inputs.contract
    surface=[list(row) for row in c.surface];surface[12][12]='unknown'
    h._inputs=SimpleNamespace(contract=replace(c,surface=tuple(tuple(row) for row in surface)))
    h._domain_inputs.base=h._inputs
    assert h._fire._is_land(12,12)
    assert (12,12) in env._stage._excluded()
    assert 12*84+12 not in scored.fixed_mask(env)


def test_aircraft_extinguishing_front_stops_ground_work(monkeypatch):
    env,_=make(monkeypatch,aircraft=True);walk=env._walk;hld=env._hold;model=env._host._fresh._model
    model._components[12][12]['vegetation']['heat']=.1;model._sync_projection()
    walk._ground.update(phase='front_no_work',position=copy.deepcopy(walk._proposal['site_position']))
    walk._ground_seal=api._hash(walk._ground);hld._hold.update(phase='holding',due_at=None)
    hld._hold_seal=api._hash(hld._hold);walk._seal=api._hash(env._host.checkpoint());env._seal=api._hash(hld.checkpoint())
    env.protect('cohort-1','shelter')
    assert hld._hold['phase']=='stopped' and hld._hold['work_minutes']==0
    assert env._host._fresh._last_costs==[] and env._host._ops.resources['air']['work_remaining']==4
