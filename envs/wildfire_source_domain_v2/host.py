"""Opt-in source-catalog incident using pinned domain-profile estimated fuel.

Unscored synthetic sensitivity exercise; ground and evacuation unavailable.
"""
import copy
import hashlib
import math
from datetime import datetime,timedelta
from pathlib import Path

import adapter_v1 as api
from envs.wildfire_response.incident_day.events import EventBus
from envs.wildfire_response.incident_day.operations import Operations
from envs.wildfire_response.incident_day import clock as clock_module
from wildfire_core import mobility_routing as mobility_module
from wildfire_core.mobility_routing import make_journey,make_leg
from eval import incident_weather_v1 as weather
from wildfire_expanded_guarded_v1 import host as core_pins
from eval import domain_profile_weather_v2 as domain
from eval.domain_profile_weather_v2 import DomainProfileWeatherFireV2
from wildfire_expanded_incident_v1.estimated_fuel_v1 import EstimatedInputsV1, EstimatedMixedFireV1, _VERIFIED_ESTIMATE
from envs.wildfire_response.regional.prepared.data import FUEL_MAPPING
from wildfire_source_road_v1.catalog import SourceCaseCatalogV1

VERSION='wildfire-source-domain-profile-incident-v2'
APPROVED_PROFILE_SHA256='a17d83005df0f88a5b9cd4e635472debf7246bad66aeaf26ac9539252fb31f59'
APPROVED_CONVERTER_SHA256='b48d42d3995f0b254959cff089d2d0890c8b24130528f7a99b42739434282d95'
APPROVED_CHOICE='unique-nominal-tied-lowest-class-factor-0.9-crossing-0.9-v1'
_DOMAIN_WEATHER=DomainProfileWeatherFireV2
SUPPORTED=frozenset(('wind','center_unavailable','aircraft_unavailable','destination_closed','access_restriction'))
_SOLAR_DAYLIGHT=clock_module.solar_daylight


def _source():return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _clock_source():
    root=Path(__file__).resolve().parents[2]
    expected=root/'envs/wildfire_response/core/src/envs/wildfire_response/incident_day/clock.py'
    if (Path(clock_module.__file__).resolve()!=expected.resolve()
        or clock_module.solar_daylight is not _SOLAR_DAYLIGHT
        or getattr(_SOLAR_DAYLIGHT,'__qualname__',None)!='solar_daylight'
        or not getattr(_SOLAR_DAYLIGHT,'__code__',None)
        or Path(_SOLAR_DAYLIGHT.__code__.co_filename).resolve()!=expected.resolve()
        or make_journey is not mobility_module.make_journey or make_leg is not mobility_module.make_leg
        or Operations is not core_pins.operations_module.Operations
        or EventBus is not core_pins.events_module.EventBus):
        raise RuntimeError('aircraft lifecycle or solar clock origin differs')
    return hashlib.sha256(expected.read_bytes()).hexdigest()



def input_identity_sha256(inputs):
    """Candidate digest of all effective estimated inputs; independently approve it."""
    if type(inputs) is not EstimatedInputsV1 or inputs._verified is not _VERIFIED_ESTIMATE:
        raise ValueError('verified domain-profile inputs required')
    c=inputs.base.contract
    return api._hash(dict(version=VERSION,identity=dict(inputs.identity),base_identity=dict(inputs.base.identity),
        input_sha256=c.document_sha256,source_pins=list(c.source_pins),grid=[c.width,c.height,c.crs,list(c.transform)],
        domain=c.domain,surface=c.surface,fuel=c.fuel,elevation=c.elevation,access=c.access,fire_support=c.fire_support,
        mixed_factors=inputs.base.factors,estimates=inputs.estimates,source_mask=inputs.source_mask,
        estimated_mask=inputs.estimated_mask))


def _domain_source():
    root=Path(__file__).resolve().parents[2]
    expected=root/'eval/domain_profile_weather_v2.py'
    actual=hashlib.sha256(expected.read_bytes()).hexdigest()
    if (Path(domain.__file__).resolve()!=expected.resolve() or domain.DomainProfileWeatherFireV2 is not _DOMAIN_WEATHER
            or domain.VERSION!='wildfire-domain-profile-weather-input-v2' or actual!=APPROVED_CONVERTER_SHA256
            or domain.PROFILE_PIN!=APPROVED_PROFILE_SHA256 or domain.CHOICE!=APPROVED_CHOICE):
        raise RuntimeError('domain profile code or fixed policy differs')
    return actual


def _check_domain(inputs,selector,expected):
    if type(expected) is not str or len(expected)!=64 or any(c not in '0123456789abcdef' for c in expected):
        raise ValueError('independently approved estimated input digest required')
    if input_identity_sha256(inputs)!=expected:raise ValueError('estimated input digest differs')
    profile=dict(inputs.identity);base=dict(inputs.base.identity);original=selector._identity['input_identity']
    changed={'input_sha256','parent_input_sha256','domain_profile_sha256','domain_choice','domain_backend_sha256'}
    if (profile.get('version')!=domain.VERSION or profile.get('profile_sha256')!=APPROVED_PROFILE_SHA256
            or profile.get('choice')!=APPROVED_CHOICE or profile.get('backend_sha256')!=_domain_source()
            or profile.get('base')!=base or base.get('domain_profile_sha256')!=APPROVED_PROFILE_SHA256
            or base.get('domain_choice')!=APPROVED_CHOICE or base.get('domain_backend_sha256')!=_domain_source()
            or base.get('parent_input_sha256')!=original.get('input_sha256')
            or base.get('input_sha256')!=inputs.base.contract.document_sha256
            or {k:v for k,v in base.items() if k not in changed}!={k:v for k,v in original.items() if k!='input_sha256'}
            or any(cell!='unknown' for row in inputs.base.contract.access for cell in row)):
        raise ValueError('domain profile and source lineage differ')
    # Reject incompatible conversion output before constructing or warming fire.
    # In particular, a v4 mixed factor cannot authorize a profile-unknown cell.
    c=inputs.base.contract;seen=set()
    for row in inputs.base.factors:
        if type(row) not in (tuple,list) or len(row)!=4:raise ValueError('invalid mixed input evidence')
        x,y,factor,crossing=row
        if (type(x) is not int or type(y) is not int or not 0<=x<c.width or not 0<=y<c.height
                or (x,y) in seen or not c.domain[y][x] or c.surface[y][x]!='mixed'
                or c.fuel[y][x] not in FUEL_MAPPING or c.elevation[y][x] is None
                or type(factor) not in (int,float) or not math.isfinite(factor) or not 0<factor<=1
                or type(crossing) not in (int,float) or not math.isfinite(crossing) or not 0<crossing<=1):
            raise ValueError('invalid mixed input evidence')
        seen.add((x,y))


def _check_live_inputs(inputs,backend):
    """Bind declared fuel/crossing evidence to live fire and censor state."""
    c=inputs.base.contract;fire=backend._fire
    rows=inputs.estimates;estimated={};openness={}
    for row in rows:
        if type(row) not in (tuple,list) or len(row)!=5:raise ValueError('invalid estimated cell')
        x,y,klass,factor,crossing=row
        if (type(x) is not int or type(y) is not int or not 0<=x<c.width or not 0<=y<c.height
                or (x,y) in estimated or type(klass) is not int or klass not in FUEL_MAPPING
                or type(factor) not in (int,float) or not math.isfinite(factor) or not 0<factor<=1
                or type(crossing) not in (int,float) or not math.isfinite(crossing) or not 0<crossing<=1
                or c.surface[y][x]!='unknown' or c.fuel[y][x] is not None or c.elevation[y][x] is None):
            raise ValueError('invalid estimated fuel evidence')
        estimated[(x,y)]=(klass,factor,crossing);openness[(x,y)]=crossing
        mapped=FUEL_MAPPING[klass];cell=fire.cells[y][x]
        if cell['terrain']!=mapped['terrain'] or cell['initial_fuel']!=mapped['initial_fuel']*factor:
            raise ValueError('estimated fire cell differs')
    mixed={};mixed_open={}
    for x,y,factor,crossing in inputs.base.factors:
        if (type(x) is not int or type(y) is not int or not 0<=x<c.width or not 0<=y<c.height
                or (x,y) in mixed or (x,y) in estimated or c.surface[y][x]!='mixed'
                or c.fuel[y][x] not in FUEL_MAPPING or c.elevation[y][x] is None
                or type(factor) not in (int,float) or not math.isfinite(factor) or not 0<factor<=1
                or type(crossing) not in (int,float) or not math.isfinite(crossing) or not 0<crossing<=1):
            raise ValueError('invalid mixed fuel evidence')
        mapped=FUEL_MAPPING[c.fuel[y][x]];cell=fire.cells[y][x]
        if cell['terrain']!=mapped['terrain'] or cell['initial_fuel']!=mapped['initial_fuel']*factor:
            raise ValueError('mixed fire cell differs')
        mixed[(x,y)]=factor;mixed_open[(x,y)]=crossing
    if (backend._inputs is not c or backend._censor._contract is not c
            or backend._estimated!=estimated or backend._censor._estimated!=frozenset(estimated)
            or backend._mixed_factors!=mixed or backend._mixed_openness!=mixed_open
            or backend._censor._allowed!=frozenset(mixed)
            or fire._mixed_openness!={**mixed_open,**openness}
            or backend._mixed_identity!=dict(inputs.base.identity) or backend._estimate_identity!=dict(inputs.identity)):
        raise ValueError('effective estimated fuel bindings differ')
    if (len(inputs.estimated_mask)!=c.height or any(len(row)!=c.width for row in inputs.estimated_mask)
            or any(type(inputs.estimated_mask[y][x]) is not bool or
                   inputs.estimated_mask[y][x]!=((x,y) in estimated)
                   for y in range(c.height) for x in range(c.width))):
        raise ValueError('estimated inventory mask differs')
    if (len(inputs.source_mask)!=c.height or any(len(row)!=c.width for row in inputs.source_mask)
            or any(type(inputs.source_mask[y][x]) is not bool or
                   inputs.source_mask[y][x]!=(c.fuel[y][x] is not None)
                   for y in range(c.height) for x in range(c.width))):
        raise ValueError('source inventory mask differs')


def _view(unit):
    return {k:copy.deepcopy(unit[k]) for k in ('id','kind','position','base_position','state',
        'water_litres','capacity_litres','fuel_minutes','fuel_capacity_minutes','work_remaining',
        'total_work_remaining','available_at','clearance') if k in unit}


class DomainProfileSourceIncidentV2:
    """Private domain-profile incident with aircraft and reports, without scoring."""
    def __init__(self, inputs, source_catalog, parent_inputs, *, case_id, containment_config, pressure_scale,
                 expected_input_sha256, opt_in=False):
        if opt_in is not True or type(source_catalog) is not SourceCaseCatalogV1:
            raise ValueError('explicit opt-in and exact source case catalog required')
        source_catalog._ensure();selected=source_catalog.private_case(case_id)
        _check_domain(inputs,source_catalog,expected_input_sha256)
        if (type(parent_inputs) is not dict or parent_inputs.get('sha256')!=source_catalog._identity['parent']
            or api._hash({k:v for k,v in parent_inputs.items() if k!='sha256'})!=source_catalog._identity['parent']):
            raise ValueError('parent input pin differs')
        original=selected['original'];firecase=selected['expanded_fire']
        if (not original.get('synthetic') or original.get('historical_replay') is not False
                or original['sha256']!=firecase['parent_scenario_sha256']):
            raise ValueError('invalid source synthetic lineage')
        # The original incident grid's pinned lon/lat is used only by the
        # core's approximate solar-day proxy; no target is moved.
        x,y=original['sector_grid']
        try: longitude,latitude=parent_inputs['grid']['centre_lon_lat'][y][x]
        except (KeyError,IndexError,TypeError,ValueError) as exc:raise ValueError('missing pinned sector lon/lat') from exc
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in (longitude,latitude)):
            raise ValueError('invalid pinned sector lon/lat')
        try:start=datetime.fromisoformat(original['start_utc'].replace('Z','+00:00'))
        except (ValueError,TypeError,KeyError) as exc:raise ValueError('invalid scenario start time') from exc
        if start.utcoffset() is None or start.utcoffset().total_seconds()!=0:
            raise ValueError('scenario start must be UTC')
        aircraft=[copy.deepcopy(r) for r in original['resources'] if r.get('kind')=='aircraft']
        if not aircraft:raise ValueError('pinned scenario has no aircraft')
        profile=copy.deepcopy(original['profile'])
        speed=profile.get('flight_speed_kph')
        if type(speed) not in (int,float) or not math.isfinite(speed) or not 0<speed<=500:
            raise ValueError('invalid synthetic aircraft speed')
        for unit in aircraft:
            facility=profile.get('facilities',{}).get(unit.get('facility_id'))
            if (type(unit.get('id')) is not str or not unit['id'] or unit.get('state') not in ('available','unavailable')
                or not isinstance(facility,dict) or 'aircraft' not in facility.get('allowed_kinds',[])
                or facility.get('position')!=unit.get('base_position') or unit.get('position')!=unit.get('base_position')
                or type(unit.get('water_litres')) not in (int,float) or type(unit.get('capacity_litres')) not in (int,float)
                or not 0<unit['water_litres']<=unit['capacity_litres']
                or type(unit.get('work_remaining')) is not int or unit['work_remaining']<=0
                or type(unit.get('fuel_minutes')) not in (int,float) or type(unit.get('fuel_capacity_minutes')) not in (int,float)
                or not 0<=unit['fuel_minutes']<=unit['fuel_capacity_minutes']):
                raise ValueError('unsupported aircraft handover or facility')
        for e in original['events']:
            if e['kind']=='aircraft_unavailable' and (set(e)!={'kind','minute','until'}
                    or type(e['until']) is not int or not e['minute']<e['until']<=1440):
                raise ValueError('invalid aircraft outage')
            if e['kind']=='center_unavailable' and (set(e)!={'kind','minute','until','center_id'}
                    or e['center_id'] not in original['centers'] or type(e['until']) is not int
                    or not e['minute']<e['until']<=1440):raise ValueError('invalid center outage')
        for e in original['events']:
            if e['kind']=='destination_closed' and (set(e)!={'kind','minute','destination_id'}
                    or e['destination_id'] not in [d['id'] for d in original['destinations']]):
                raise ValueError('invalid destination closure')
            if e['kind']=='access_restriction' and (set(e)!={'kind','minute','resource_kind'}
                    or e['resource_kind'] not in ('crew','engine')):
                raise ValueError('invalid access restriction')
        self._selector=source_catalog;self._case=original;self._firecase=firecase
        self._domain_inputs=inputs;self._input_sha=expected_input_sha256;self._domain_source=_domain_source()
        self._inputs=inputs.base;self._start=start;self._longitude=longitude;self._latitude=latitude
        self._speed=float(speed);self._source=_source();self._dependencies=core_pins._sources();self._clock=_clock_source()
        self._weather=DomainProfileWeatherFireV2(inputs,source_catalog._fire,
            expected_drafts_sha256=source_catalog._identity['fire'],case_id=firecase['id'],
            containment_config=containment_config,pressure_scale=pressure_scale,opt_in_estimated_weather=True)
        while self._weather._status=='active' and self._weather._weather_kind=='warm':self._weather.step()
        if self._weather._warmup_done==0 and self._weather._minute==0:
            self._weather._fire._fire.wind=tuple(weather.conditions(self._weather._profile,0)['wind'])
        self._fire=self._weather._fire
        self._ops=Operations(aircraft,copy.deepcopy(original['destinations']),copy.deepcopy(original['groups']),profile,return_planner=self._return_plan)
        self._dose={k:0 for k in self._ops.groups}
        # Population positions are retained exactly; no connector or access is inferred.
        c=inputs.base.contract;t=c.transform
        burning={tuple(p) for p in self._fire.burning()} if self._weather._status=='active' else set()
        for g in self._ops.groups.values():
            pos=g['position'];x=math.floor((pos[0]-t[2])/t[0]);y=math.floor((pos[1]-t[5])/t[4])
            if not (0<=x<c.width and 0<=y<c.height):
                raise ValueError('population position outside source fire grid')
            if (x,y) in burning and not g['exposed']:
                raise ValueError('burning handover population requires declared inherited exposure')
        self._bus=EventBus(original['seed'],original['centers'])
        self._known={key:_view(unit) for key,unit in self._ops.resources.items()}
        self._known_at={key:0 for key in self._known}
        self._minute=0;self._closed={};self._air_until=0;self._seen=0
        self._status='censored' if self._weather._status=='censored' else 'active'
        # Match the core's synthetic partial handover observation. Do not
        # publish the private full ignition list or later true fire state.
        burning=sorted(self._fire.burning()) if self._status=='active' else []
        self._handover_fire=copy.deepcopy(burning[:max(1,len(burning)//2)])
        self._reason=None;self._unsupported_kind=None;self._calls=[];self._drop_audit=[];self._recording=True;self._poisoned=False
        self._identity=dict(version=VERSION,source=self._source,core=self._dependencies,clock=self._clock,
            catalog=copy.deepcopy(source_catalog._identity),case=case_id,original=original['sha256'],
            expanded=firecase['sha256'],fire=copy.deepcopy(self._weather._identity),
            domain_input=expected_input_sha256,domain_source=self._domain_source)
        if self._status=='active':
            self._bus.emit('handover','handover',{'cells':copy.deepcopy(self._handover_fire),
                'text':'Domain-profile synthetic exercise; initial fire observations may be stale.'},0,role='center',delay=0)
            self._weather_reports(0);self._incoming(0);self._bus.tick(0,{})
            self._sync()

    def _ensure(self):
        if self._poisoned:raise RuntimeError('domain host indeterminate; restore trusted checkpoint')
        try:
            self._selector._ensure();self._weather._ensure()
            if (_source()!=self._source or core_pins._sources()!=self._dependencies or _clock_source()!=self._clock
                    or self._selector._identity!=self._identity['catalog'] or self._weather._identity!=self._identity['fire']
                    or api._hash({k:v for k,v in self._case.items() if k!='sha256'})!=self._identity['original']
                    or api._hash({k:v for k,v in self._firecase.items() if k!='sha256'})!=self._identity['expanded']
                    or type(self._weather) is not _DOMAIN_WEATHER or self._weather._inputs is not self._domain_inputs
                    or self._weather._fire is not self._fire or type(self._fire) is not EstimatedMixedFireV1
                    or self._domain_inputs.base is not self._inputs or _domain_source()!=self._domain_source
                    or self._input_sha!=self._identity['domain_input'] or self._domain_source!=self._identity['domain_source']
                    or input_identity_sha256(self._domain_inputs)!=self._input_sha):
                raise RuntimeError('domain host implementation or identity changed')
            _check_live_inputs(self._domain_inputs,self._fire)
        except BaseException:
            self._poisoned=True
            raise

    def _active(self):
        self._ensure()
        if self._status=='censored':raise api.CensoredError('episode censored')
        if self._status!='active':raise RuntimeError('aircraft slice cannot advance')

    def situation(self):
        self._ensure()
        if self._status=='censored':return dict(version=VERSION,status='censored',minute=self._minute)
        return dict(version=VERSION,status=self._status,minute=self._minute,reason=self._reason,
            scope='domain-profile estimated-fuel synthetic exercise; ground and evacuation unavailable; unscored',
            resources=copy.deepcopy(list(self._known.values())),known_fire=copy.deepcopy(self._handover_fire),
            limitations=['estimated fuel and surface assumptions','unknown cells remain censored','ground dispatch unavailable: access unverified','evacuation unavailable: access unverified','score unavailable'],
            fire_observed_at=0,wind=copy.deepcopy(getattr(self,'_known_wind',None)),
            reports_cursor=len(self._bus.reports))

    def reports(self,*,after_cursor=0,limit=20):
        self._ensure()
        if self._status=='censored':raise api.CensoredError('reports suppressed after censoring')
        return self._bus.get_reports(after_cursor=after_cursor,limit=limit)

    def _target_native(self,target):
        c=self._inputs.contract
        if (type(target) not in (list,tuple) or len(target)!=2 or any(type(x) is not int for x in target)
            or not 0<=target[0]<c.width or not 0<=target[1]<c.height):raise ValueError('invalid target grid')
        x,y=target;t=c.transform
        return [t[2]+(x+.5)*t[0],t[5]+(y+.5)*t[4]]

    def _flight(self,origin,destination):
        if math.dist(origin,destination)<.01:return None
        return make_journey([make_leg('flight',[origin,destination],self._speed)],origin,destination,
                            'Synthetic land-loaded aircraft flight; not operational clearance')

    def _return_plan(self,unit,minute):
        if math.dist(unit['position'],unit['base_position'])<.01:
            # Operations requires a valid one-minute, zero-leg recovery plan
            # while a mobilizing aircraft is still physically at its base.
            return make_journey([],unit['position'],unit['base_position'],
                                'Synthetic recovery already at base')
        return self._flight(unit['position'],unit['base_position'])

    def _reported_fire_targets(self):
        # Generic resident reports are not fire observations. Only an explicit
        # pinned fire category with a delivered grid location authorizes a target.
        targets=copy.deepcopy(self._handover_fire)
        for report in self._bus.reports:
            if (report.get('kind')=='incoming_report' and report.get('payload',{}).get('category')=='fire'
                    and type(report.get('location')) is list):
                target=report['location']
                try:self._target_native(target)
                except ValueError:continue
                if target not in targets:targets.append(copy.deepcopy(target))
        return targets

    def route_options(self,resource_id,target):
        self._active()
        if resource_id not in self._known or target not in self._reported_fire_targets():
            raise ValueError('choose a reported aircraft and delivered fire cell')
        destination=self._target_native(target);journey=self._flight(self._known[resource_id]['position'],destination)
        if journey is None:return dict(status='unmapped',options=[])
        identifier=api._hash(dict(resource=resource_id,target=target,origin=self._known[resource_id]['position'],
                                 identity=self._identity['expanded'],speed=self._speed))[:20]
        return dict(status='reachable',options=[dict(id=identifier,travel_minutes=journey['duration_minutes'])])

    def _record(self,operation,args,kwargs,result):
        if self._recording:self._calls.append(dict(operation=operation,args=copy.deepcopy(args),
                                                   kwargs=copy.deepcopy(kwargs),result=copy.deepcopy(result)))
        return result

    def clear_aircraft(self,resource_id):
        self._active()
        if resource_id not in self._known:raise ValueError('unknown reported aircraft')
        self._step(dict(kind='clear',resource=resource_id))
        result={'status':'submitted'} if self._status=='active' else self.situation()
        return self._record('clear_aircraft',[resource_id],{},result)

    def dispatch(self,resource_id,target,option_id):
        self._active();options=self.route_options(resource_id,target)['options']
        if not any(o['id']==option_id for o in options):raise ValueError('unknown or stale flight option')
        journey=self._flight(self._known[resource_id]['position'],self._target_native(target))
        self._step(dict(kind='dispatch',resource=resource_id,target=copy.deepcopy(target),journey=journey))
        result={'status':'submitted'} if self._status=='active' else self.situation()
        return self._record('dispatch',[resource_id,copy.deepcopy(target),option_id],{},result)

    def release(self,resource_id):
        self._active()
        if resource_id not in self._known:raise ValueError('unknown reported aircraft')
        self._step(dict(kind='release',resource=resource_id))
        result={'status':'submitted'} if self._status=='active' else self.situation()
        return self._record('release',[resource_id],{},result)

    def protect(self,group_id,action,destination_id=None):
        raise ValueError('population orders require an approved public roster')

    def _submit_shelter(self,group_id,action,destination_id=None):
        self._active()
        if group_id not in self._ops.groups or action!='shelter' or destination_id is not None:
            raise ValueError('only shelter orders are supported; evacuation access is unknown')
        self._step(dict(kind='shelter',group=group_id))
        result={'status':'submitted'} if self._status=='active' else self.situation()
        return self._record('protect',[group_id,action,destination_id],{},result)

    def request_status(self,center_id):
        self._active()
        if center_id not in self._case['centers']:raise ValueError('unknown center')
        self._step(dict(kind='status',center=center_id))
        result={'status':'submitted'} if self._status=='active' else self.situation()
        return self._record('request_status',[center_id],{},result)

    def advance(self,minutes=1):
        self._active()
        if type(minutes) is not int or not 1<=minutes<=60:raise ValueError('advance requires 1..60 minutes')
        for _ in range(minutes):
            self._step()
            if self._status!='active':break
        return self._record('advance',[minutes],{},self.situation())

    def _weather_reports(self,minute):
        for record in self._weather._profile.records:
            if record.minute==minute:
                self._bus.emit('weather','weather_update',dict(wind=list(record.wind),temperature_c=record.temperature_c,
                    relative_humidity_pct=record.relative_humidity_pct,rain_mm_per_hour=record.rain_mm_per_hour,
                    text='Synthetic exercise weather; uncalibrated proxy.'),minute,delay=record.received_at-record.minute)

    def _incoming(self,minute):
        for report in self._firecase.get('incoming_reports',[]):
            if report['minute']==minute:
                self._bus.emit(report['source'],'incoming_report',dict(text=report['text'],coordinate_frame='expanded_grid',
                    category=report.get('category')),
                    minute,role=report['role'],channel=report['channel'],location=copy.deepcopy(report['location']),
                    delay=report['delay'])

    def _sync(self):
        for record in self._bus.reports[self._seen:]:
            if record['kind']=='resource_status':
                unit=record['payload']['resource'];key=unit['id']
                if key in self._known and record['observed_at']>=self._known_at[key]:
                    self._known[key]=copy.deepcopy(unit);self._known_at[key]=record['observed_at']
            elif record['kind']=='weather_update' and record['observed_at']>getattr(self,'_wind_at',-1):
                self._known_wind=copy.deepcopy(record['payload']['wind']);self._wind_at=record['observed_at']
        self._seen=len(self._bus.reports)

    def _step(self,command=None):
        try:
            if not self._fire._check():
                self._weather._status='censored';self._status='censored';return
            due=[e for e in self._case['events'] if e['minute']==self._minute+1]
            unsupported=[e for e in due if e['kind'] not in SUPPORTED]
            if unsupported:
                self._status='unsupported';self._reason='unsupported_scheduled_event'
                self._unsupported_kind=unsupported[0]['kind'];return
            minute=self._minute+1
            closed=copy.deepcopy(self._closed);air_until=self._air_until
            ops=Operations.restore(self._ops.checkpoint(),return_planner=self._return_plan)
            bus=EventBus.restore(self._bus.checkpoint())
            pending=[];drops=[]
            previous={k:_view(u) for k,u in ops.resources.items()}
            def emit(source,kind,payload,**kwargs):pending.append((source,kind,copy.deepcopy(payload),copy.deepcopy(kwargs)))
            for e in due:
                if e['kind']=='center_unavailable':closed[e['center_id']]=max(closed.get(e['center_id'],0),e['until'])
                elif e['kind']=='aircraft_unavailable':
                    air_until=max(air_until,e['until']);emit('regional','aircraft_window',{'until':air_until})
                elif e['kind']=='destination_closed':
                    d=ops.destinations[e['destination_id']];d['status']='closed'
                    emit('destination','destination_threatened',{'destination':d},urgent=True)
                elif e['kind']=='access_restriction':
                    emit('road-patrol','access_restriction',{'resource_kind':e['resource_kind']},urgent=True)
            conditions=weather.conditions(self._weather._profile,minute)
            daylight=clock_module.solar_daylight(self._start+timedelta(minutes=minute),self._longitude,self._latitude)
            daylight=bool(daylight and minute>=air_until)
            ops.set_flight_conditions(conditions['wind'],daylight)
            if command:
                if command['kind']=='shelter':
                    try:
                        ops.protect(command['group'],'shelter',None,None,minute);accepted=True
                    except ValueError:
                        accepted=False
                    emit('operations','request_result',{'kind':'shelter','accepted':accepted})
                elif command['kind']=='clear':
                    accepted=ops.clear_aircraft(command['resource'],minute)
                    emit('operations','clearance_result',dict(resource_id=command['resource'],accepted=bool(accepted)))
                elif command['kind']=='dispatch':
                    u=ops.resources[command['resource']]
                    accepted=ops.dispatch(u['id'],'incident',command['target'],command['journey'],minute)
                    emit('operations','dispatch_result',dict(resource_id=u['id'],accepted=bool(accepted)))
                elif command['kind']=='release':
                    u=ops.resources[command['resource']]
                    accepted=ops.release(u['id'],self._return_plan(u,minute),minute)
                    if accepted and u['state']=='returning':
                        # This command starts at the previous decision boundary.
                        # The current Operations tick must move the first minute
                        # of the return when it charges that minute of fuel.
                        journey=u['journey']
                        journey['departed_at']=minute-1
                        journey['arrives_at']=journey['departed_at']+journey['duration_minutes']
                    emit('operations','release_result',dict(resource_id=u['id'],accepted=bool(accepted)))
                elif command['kind']=='status':bus.request_status(command['center'],'situation',self._minute)
            shadow={tuple(p):copy.deepcopy(self._fire._fire.cells[p[1]][p[0]]) for p in self._fire.burning()}
            def drop(unit):
                target=unit.get('target_grid')
                for point in sorted(shadow):
                    cell=shadow[point]
                    if (target is not None and math.dist(point,target)<=3 and cell['heat']>0 and cell['fuel']>0):
                        before=cell['heat'];cell['heat']=max(0.,before-.15);cell['wet']=max(cell.get('wet',0),5)
                        drops.append(dict(resource_id=unit['id'],cell=list(point),before=before,after=cell['heat'],
                            work_before=unit['work_remaining'],water_before=unit['water_litres'],
                            total_before=unit.get('total_work_remaining')))
                        return True
                return False
            # Operations charges a travel minute before checking the flight
            # window. If it aborts without moving, that charged minute belongs
            # to the return reserve: recovery starts on the following tick.
            pre_tick={key:dict(state=unit['state'],blocked_state=unit.get('blocked_state'),
                               fuel=unit.get('fuel_minutes'),position=copy.deepcopy(unit['position']))
                      for key,unit in ops.resources.items() if unit['kind']=='aircraft'}
            ops.tick(minute,wind=conditions['wind'],daylight=daylight,hazard_check=lambda pos,kind:True,
                     movement_check=lambda journey,m,kind:True,suppress=drop,emit=emit,return_planner=self._return_plan)
            aborted={payload['resource_id'] for _,kind,payload,_ in pending if kind=='aircraft_aborted'}
            for identifier in sorted(aborted):
                prior=pre_tick[identifier];unit=ops.resources[identifier]
                movement=prior['blocked_state'] if prior['state']=='blocked' else prior['state']
                if movement=='travel' and prior['fuel'] is not None:
                    if unit['position']!=prior['position'] or unit['fuel_minutes']!=prior['fuel']-1:
                        raise RuntimeError('aircraft abort fuel or position differs')
                    unit['fuel_minutes']=prior['fuel']
                    # If the abort occurred at base with full tanks, the
                    # temporary debit may have spuriously entered refuelling.
                    # Cancel that reservation and give the slot to the next
                    # legitimately queued aircraft in the same tick.
                    if (unit['position']==unit['base_position']
                            and unit['fuel_minutes']==unit['fuel_capacity_minutes']
                            and unit['water_litres']==unit['capacity_litres']):
                        ops._cancel_refill(unit)
                        ops.air_refill_queue=[key for key in ops.air_refill_queue if key!=identifier]
                        if ops.air_refill_active==identifier:ops.air_refill_active=None
                        ops._at_base(unit,minute)
                        if ops.air_refill_active is None and ops.air_refill_queue:
                            next_id=ops.air_refill_queue.pop(0);next_unit=ops.resources[next_id]
                            if not ops._reserve_refill(next_unit):next_unit['state']='needs_assistance'
                            else:
                                ops.air_refill_active=next_id
                                next_unit.update(state='refilling',due_at=minute+ops.profile.get('air_refill_minutes',15))
            for item in drops:
                unit=ops.resources[item['resource_id']]
                if (unit['work_remaining']!=item['work_before']-1 or unit['water_litres']!=0
                    or (item['total_before'] is not None and unit.get('total_work_remaining')!=item['total_before']-1)):
                    raise RuntimeError('aircraft work or water charge differs')
            # Apply only to supported, currently burning cells; no unknown or
            # sea sentinel may be changed. Any interruption poisons the host.
            for item in drops:
                x,y=item['cell']
                if not self._fire._is_land(x,y):raise RuntimeError('unsupported aircraft drop cell')
                cell=self._fire._fire.cells[y][x]
                if cell['heat']!=item['before'] or cell['fuel']<=0:raise RuntimeError('aircraft drop state differs')
                cell['heat']=item['after'];cell['wet']=max(cell.get('wet',0),5)
            if not self._fire._check():raise RuntimeError('aircraft mutation censored before spread')
            result=self._weather.step()
            self._ops,self._bus=ops,bus
            self._minute,self._closed,self._air_until=minute,closed,air_until
            self._drop_audit.extend(dict(minute=minute,**item) for item in drops)
            if result['status']=='censored':self._status='censored';return
            t=self._inputs.contract.transform
            burning=self._fire.burning()
            for key,group in ops.groups.items():
                x=math.floor((group['position'][0]-t[2])/t[0]);y=math.floor((group['position'][1]-t[5])/t[4])
                cell=(x,y)
                if any(math.dist(cell,p)<=1.5 for p in burning):
                    self._dose[key]+=2 if group['status']=='sheltering' else 10
                if list(cell) in burning or self._dose[key]>=180:group['exposed']=True
            for item in drops:
                emit(item['resource_id'],'drop_completed',dict(resource_id=item['resource_id'],
                    target_grid=copy.deepcopy(ops.resources[item['resource_id']].get('target_grid'))))
            for key,unit in ops.resources.items():
                if previous[key]!=_view(unit) or minute%30==0:
                    emit(key,'resource_status',{'resource':_view(unit)},actionable=True)
            for source,kind,payload,kwargs in pending:self._bus.emit(source,kind,payload,minute,**kwargs)
            self._weather_reports(minute);self._incoming(minute)
            views={c:({'available':False} if closed.get(c,0)>minute else
                     {'situation':{'observed_at':minute,'text':'Domain-profile synthetic exercise'}})
                   for c in self._case['centers']}
            self._bus.tick(minute,views);self._sync()
            if result['status']=='diagnostic_end':self._status='end_unscored'
        except BaseException:self._poisoned=True;raise

    def transcript(self):
        self._ensure()
        return dict(version=VERSION,identity_sha256=api._hash(self._identity),calls=copy.deepcopy(self._calls))

    def checkpoint(self):
        self._ensure()
        value=dict(version=VERSION,identity=copy.deepcopy(self._identity),minute=self._minute,status=self._status,
            reason=self._reason,unsupported_kind=self._unsupported_kind,weather=self._weather.checkpoint(),operations=self._ops.checkpoint(),
            communications=self._bus.checkpoint(),known=copy.deepcopy(self._known),known_at=copy.deepcopy(self._known_at),
            known_wind=copy.deepcopy(getattr(self,'_known_wind',None)),wind_at=getattr(self,'_wind_at',-1),
            handover_fire=copy.deepcopy(self._handover_fire),
            closed=copy.deepcopy(self._closed),air_until=self._air_until,seen=self._seen,
            drop_audit=copy.deepcopy(self._drop_audit),dose=copy.deepcopy(self._dose),calls=copy.deepcopy(self._calls))
        value['sha256']=api._hash(value)
        return value

    def restore(self,checkpoint,*,expected_checkpoint_sha256):
        try:
            v=copy.deepcopy(checkpoint)
            if (type(expected_checkpoint_sha256) is not str or len(expected_checkpoint_sha256)!=64
                or any(c not in '0123456789abcdef' for c in expected_checkpoint_sha256)
                or api._hash(v)!=expected_checkpoint_sha256):raise ValueError('trusted checkpoint digest differs')
            keys={'version','identity','minute','status','reason','unsupported_kind','weather','operations','communications','known','known_at',
                  'known_wind','wind_at','handover_fire','closed','air_until','seen','drop_audit','dose','calls','sha256'}
            if (type(v) is not dict or set(v)!=keys or v['version']!=VERSION
                or api._encoded(v['identity'])!=api._encoded(self._identity) or _source()!=self._source
                or _clock_source()!=self._clock or core_pins._sources()!=self._dependencies
                or v['sha256']!=api._hash({k:x for k,x in v.items() if k!='sha256'})
                or v['handover_fire']!=self._handover_fire
                or type(v['minute']) is not int or not 0<=v['minute']<=1440
                or v['status'] not in ('active','unsupported','censored','end_unscored')
                or (v['status']=='unsupported') != (v['reason']=='unsupported_scheduled_event' and type(v['unsupported_kind']) is str)
                or (v['status']=='end_unscored')!=(v['minute']==1440 and v['status']!='censored')):
                raise ValueError('aircraft checkpoint identity or clock differs')
            self._selector._ensure();_check_domain(self._domain_inputs,self._selector,self._input_sha)
            if _domain_source()!=self._domain_source:raise ValueError('domain implementation differs')
            self._weather.restore(v['weather'])
            ops=Operations.restore(v['operations'],return_planner=self._return_plan);bus=EventBus.restore(v['communications'])
            original={r['id']:r for r in self._case['resources'] if r['kind']=='aircraft'}
            if (set(ops.resources)!=set(original)
                or set(ops.groups)!={g['id'] for g in self._case['groups']}
                or set(ops.destinations)!={d['id'] for d in self._case['destinations']}
                or set(v['dose'])!=set(ops.groups)
                or any(type(x) is not int or x<0 for x in v['dose'].values())
                or set(v['known'])!=set(original) or set(v['known_at'])!=set(original)
                or any(ops.resources[k]['base_position']!=original[k]['base_position'] or
                       ops.resources[k]['facility_id']!=original[k]['facility_id'] for k in original)
                or ops.last_minute!=(v['minute'] if v['minute'] else None)
                or self._weather._minute!=v['minute'] or (self._weather._status=='censored')!=(v['status']=='censored')
                or bus.seed!=self._case['seed'] or bus._last_tick>v['minute'] or v['seen']!=len(bus.reports)
                or type(v['air_until']) is not int or not 0<=v['air_until']<=1440
                or type(v['closed']) is not dict or not set(v['closed'])<=set(self._case['centers'])
                or type(v['wind_at']) is not int or not -1<=v['wind_at']<=v['minute']
                or any(type(t) is not int or not 0<=t<=v['minute'] for t in v['known_at'].values())
                or type(v['calls']) is not list or type(v['drop_audit']) is not list):
                raise ValueError('aircraft checkpoint roster or clock differs')
            self._fire=self._weather._fire;self._ops,self._bus=ops,bus
            self._minute,self._status,self._reason=v['minute'],v['status'],v['reason']
            self._unsupported_kind=v['unsupported_kind']
            self._known,self._known_at=v['known'],v['known_at']
            self._known_wind,self._wind_at=v['known_wind'],v['wind_at']
            self._closed,self._air_until,self._seen=v['closed'],v['air_until'],v['seen']
            self._drop_audit,self._dose,self._calls=v['drop_audit'],v['dose'],v['calls'];self._poisoned=False
            if api._encoded(self.checkpoint())!=api._encoded(v):raise ValueError('aircraft restore differs')
        except BaseException:self._poisoned=True;raise

    def replay(self,transcript,*,expected_sha256,expected_identity_sha256,expected_final_checkpoint_sha256):
        self._active()
        if self._minute!=0 or self._calls:raise ValueError('replay requires fresh aircraft host')
        if (type(transcript) is not dict or set(transcript)!={'version','identity_sha256','calls'}
            or transcript['version']!=VERSION or api._hash(transcript)!=expected_sha256
            or transcript['identity_sha256']!=expected_identity_sha256
            or expected_identity_sha256!=api._hash(self._identity) or type(transcript['calls']) is not list):
            raise ValueError('aircraft replay identity differs')
        self._recording=False
        try:
            for entry in transcript['calls']:
                if (type(entry) is not dict or set(entry)!={'operation','args','kwargs','result'}
                    or entry['operation'] not in ('clear_aircraft','dispatch','release','protect','request_status','advance')
                    or type(entry['args']) is not list or type(entry['kwargs']) is not dict):
                    raise ValueError('invalid aircraft replay call')
                method=self._submit_shelter if entry['operation']=='protect' else getattr(self,entry['operation'])
                result=method(*entry['args'],**entry['kwargs'])
                if api._encoded(result)!=api._encoded(entry['result']):raise ValueError('aircraft replay differs')
            self._calls=copy.deepcopy(transcript['calls'])
            if api._hash(self.checkpoint())!=expected_final_checkpoint_sha256:
                raise ValueError('aircraft replay checkpoint differs')
            return self.situation()
        except BaseException:self._poisoned=True;raise
        finally:self._recording=True
