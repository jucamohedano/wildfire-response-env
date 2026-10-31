"""Guarded road-only weather/fire, ground movement and population exercise.

This is a private single-driver component; it is not a complete scored day.
"""
import copy
import hashlib
import math
from pathlib import Path

import adapter_v1 as api
from envs.wildfire_response.incident_day import events as events_module, operations as operations_module
from envs.wildfire_response.incident_day.events import EventBus
from envs.wildfire_response.incident_day.operations import Operations, _same_position
from eval import expanded_guarded_routes_v1 as guarded_module
from eval import expanded_origin_guard_v1 as origin_module
from eval import expanded_road_runtime_v1 as road_module
from eval import incident_weather_v1 as weather
from envs.wildfire_response.incident_day import routing as routing_module
from envs.wildfire_response.regional.ops import generator as generator_module
from wildfire_core import mobility_routing as mobility_module
from eval import extended_access_v1 as access_module
from wildfire_expanded_incident_v1 import weather_fire_v1 as weather_fire_module
from wildfire_expanded_incident_v1.weather_fire_v1 import WeatherMixedFireDiagnosticV1

VERSION = 'wildfire-expanded-guarded-incident-v1'
CATALOG_VERSION = 'wildfire-expanded-guarded-synthetic-catalog-v1'
_REVIEWED_PAIR_SHA256 = 'bbfcf2f257bca8e793f9fb8d864fb37de9bc3609c998dd94ed744e7734a8c548'
_PAIR_VERIFIER = guarded_module._verify_reviewed_pair
_PAIR_VERIFIER_IMPL = guarded_module._verify_pair_against_anchor
PROFILE = dict(mobilization_minutes=1,ground_setup_minutes=5,duty_minutes=240,rest_minutes=60,
               engine_litres_per_minute=500,revalidate_setup_safety=True,
               order_delivery_minutes=2,order_ack_minutes=2,order_execution_minutes=2)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _check_pair_binding():
    """Bind the fixed reviewed pair and the actual loaded verifier functions."""
    root = Path(__file__).resolve().parents[2]
    path = (root/'eval/expanded_guarded_routes_v1.py').resolve()
    if not getattr(guarded_module, '__file__', None) or Path(guarded_module.__file__).resolve() != path:
        raise RuntimeError('reviewed-pair module origin differs')
    for name, function in (('_verify_reviewed_pair', _PAIR_VERIFIER),
                           ('_verify_pair_against_anchor', _PAIR_VERIFIER_IMPL)):
        if (getattr(guarded_module, name, None) is not function
                or getattr(function, '__module__', None) != guarded_module.__name__
                or getattr(function, '__qualname__', None) != name
                or not getattr(function, '__code__', None)
                or Path(function.__code__.co_filename).resolve() != path):
            raise RuntimeError('reviewed-pair verifier binding or origin differs')
    if (_PAIR_VERIFIER.__defaults__ != (_REVIEWED_PAIR_SHA256,)
            or guarded_module.TRUSTED_PAIR_SHA256 != _REVIEWED_PAIR_SHA256
            or generator_module.digest(guarded_module.REVIEWED_PAIR_MANIFEST) != _REVIEWED_PAIR_SHA256):
        raise RuntimeError('reviewed-pair trust anchor differs')


def _check_pair_identity(routes):
    if routes.get('reviewed_pair_sha256') != _REVIEWED_PAIR_SHA256:
        raise RuntimeError('guarded routes do not match the reviewed pair')


def _sources():
    _check_pair_binding()
    # Bind runtime imports as well as bytes: a same-named dependency loaded
    # from a different checkout is not the reviewed implementation.
    root = Path(__file__).resolve().parents[2]
    modules = dict(host=__import__(__name__,fromlist=['']), guarded=guarded_module,
        origin=origin_module, road=road_module, routing=routing_module,
        operations=operations_module, events=events_module, mobility=mobility_module,
        generator=generator_module, access=access_module, weather=weather,
        weather_fire=weather_fire_module)
    paths = dict(host='envs/wildfire_expanded_guarded_v1/host.py',
        guarded='eval/expanded_guarded_routes_v1.py', origin='eval/expanded_origin_guard_v1.py',
        road='eval/expanded_road_runtime_v1.py', access='eval/extended_access_v1.py',
        weather='eval/incident_weather_v1.py',
        weather_fire='envs/wildfire_expanded_incident_v1/weather_fire_v1.py',
        routing='envs/wildfire_response/core/src/envs/wildfire_response/incident_day/routing.py',
        operations='envs/wildfire_response/core/src/envs/wildfire_response/incident_day/operations.py',
        events='envs/wildfire_response/core/src/envs/wildfire_response/incident_day/events.py',
        mobility='envs/wildfire_response/core/src/wildfire_core/mobility_routing.py',
        generator='envs/wildfire_response/core/src/envs/wildfire_response/regional/ops/generator.py')
    if any(not getattr(module, '__file__', None) or Path(module.__file__).resolve() != (root/paths[name]).resolve()
           for name,module in modules.items()):
        raise RuntimeError('guarded runtime dependency loaded from unexpected path')
    if (Operations is not operations_module.Operations or EventBus is not events_module.EventBus
        or _same_position is not operations_module._same_position
        or operations_module.journey_position is not mobility_module.journey_position
        or routing_module.point is not mobility_module.point
        or routing_module.split_line is not mobility_module.split_line
        or origin_module.RoadNetwork is not mobility_module.RoadNetwork
        or origin_module.indexes is not road_module.indexes
        or guarded_module.GuardedOriginRouter is not origin_module.GuardedOriginRouter
        or guarded_module.digest is not generator_module.digest
        or mobility_module.digest is not generator_module.digest
        or WeatherMixedFireDiagnosticV1 is not weather_fire_module.WeatherMixedFireDiagnosticV1
        or weather_fire_module.weather is not weather):
        raise RuntimeError('guarded runtime dependency binding differs')
    return {name:_sha(module.__file__) for name,module in modules.items()}


def _view(unit):
    return {k:copy.deepcopy(unit[k]) for k in ('id','kind','position','base_position','state',
             'water_litres','capacity_litres','work_remaining','available_at')}


def _group(group):
    return {k:copy.deepcopy(group[k]) for k in ('id','count','position','status','exposed')}


class GuardedIncidentV1:
    """A source-road-adapted *synthetic* exercise, not an approved source day.

    Existing input and road component pins are mandatory. No off-road movement,
    partial-edge replanning, ground suppression, aircraft or reward is offered.
    """
    def __init__(self, inputs, drafts, *, expected_drafts_sha256, case_id,
                 graph, expected_graph_sha256, source, expected_source_sha256,
                 cell_evidence, expected_evidence_sha256, allowed_edge_ids,
                 catalog, expected_catalog_sha256, containment_config, pressure_scale, opt_in=False):
        if opt_in is not True:
            raise ValueError('explicit guarded exercise opt-in required')
        source_identity = _sources()
        diagnostic = WeatherMixedFireDiagnosticV1(inputs,drafts,expected_drafts_sha256=expected_drafts_sha256,
            case_id=case_id,containment_config=containment_config,pressure_scale=pressure_scale,opt_in_weather_fire=True)
        if any(e.get('kind') != 'wind' for e in diagnostic._case['events']) or diagnostic._case.get('incoming_reports'):
            raise ValueError('unsupported non-weather event or incoming report')
        contract=inputs.contract
        evidence=guarded_module.check_evidence(cell_evidence,expected_evidence_sha256)
        grid=dict(width=contract.width,height=contract.height,crs=contract.crs,transform=list(contract.transform))
        if evidence['grid'] != grid:
            raise ValueError('cell evidence grid differs from fire inputs')
        for y in range(contract.height):
            for x in range(contract.width):
                if evidence['access'][y][x]=='accessible' and not (
                    contract.domain[y][x] and contract.surface[y][x]=='land'
                    and contract.access[y][x]=='accessible' and evidence['surface'][y][x]=='land'):
                    raise ValueError('road evidence cannot promote unknown fire-input access')
        _check_pair_binding()
        self._routes=guarded_module.GuardedRoadRoutes(graph,expected_graph_sha256,source,expected_source_sha256,
            evidence,expected_evidence_sha256,[list(row) for row in contract.elevation],
            allowed_edge_ids=allowed_edge_ids,opt_in=True)
        _check_pair_identity(self._routes.identity)
        self._inputs=inputs
        roster=copy.deepcopy(catalog)
        if (type(roster) is not dict or set(roster)!={'version','role','case_id','inputs_sha256',
                'graph_sha256','resources','groups','destinations'} or roster['version']!=CATALOG_VERSION
                or roster['role']!='synthetic_source_adapted_not_historical'
                or api._hash(roster)!=expected_catalog_sha256 or roster['case_id']!=case_id
                or roster['inputs_sha256']!=contract.document_sha256 or roster['graph_sha256']!=expected_graph_sha256
                or any(type(roster[k]) is not list for k in ('resources','groups','destinations'))):
            raise ValueError('synthetic exercise roster or pin differs')
        self._roster=roster
        self._case=copy.deepcopy(diagnostic._case)
        self._positions=self._routes._positions
        resources=[]
        for item in roster['resources']:
            if (type(item) is not dict or set(item)!={'id','kind','position','work_remaining','water_litres'}
                or type(item['id']) is not str or not item['id'] or item['kind'] not in ('crew','engine')
                or type(item['work_remaining']) is not int or item['work_remaining']<=0
                or type(item['water_litres']) is not int or item['water_litres']<0
                or (item['kind']=='crew' and item['water_litres']!=0)
                or (item['kind']=='engine' and item['water_litres']<=0)):
                raise ValueError('invalid synthetic ground resource')
            self._node(item['position'])
            resources.append(dict(item,base_position=copy.deepcopy(item['position']),home_base=self._case['centers'][0],
                                  capacity_litres=item['water_litres'],state='available',available_at=0))
        groups=[]
        for item in roster['groups']:
            if (type(item) is not dict or set(item)!={'id','count','position','initial_exposed','handover_exposure'}
                or type(item['id']) is not str or not item['id'] or type(item['count']) is not int or item['count']<=0
                or type(item['initial_exposed']) is not bool
                or (item['handover_exposure'] is not None and type(item['handover_exposure']) is not bool)
                or (item['handover_exposure'] is not None and item['handover_exposure'] != item['initial_exposed'])):
                raise ValueError('invalid synthetic population cohort')
            self._node(item['position'])
            groups.append(dict(id=item['id'],count=item['count'],position=copy.deepcopy(item['position']),
                home=copy.deepcopy(item['position']),status='at_home',exposed=item['initial_exposed']))
        destinations=[]
        for item in roster['destinations']:
            if (type(item) is not dict or set(item)!={'id','capacity','position'} or type(item['id']) is not str
                or not item['id'] or type(item['capacity']) is not int or item['capacity']<0):
                raise ValueError('invalid synthetic destination')
            self._node(item['position'])
            destinations.append(dict(item,status='open'))
        self._ops=Operations(resources,destinations,groups,PROFILE)
        self._weather=diagnostic
        while diagnostic.situation()['status']=='active' and diagnostic._weather_kind=='warm': diagnostic.step()
        if diagnostic._warmup_done==0 and diagnostic._minute==0:
            diagnostic._fire._fire.wind=tuple(weather.conditions(diagnostic._profile,0)['wind'])
        self._fire=diagnostic._fire
        self._minute=0
        self._censored=diagnostic._status=='censored'
        self._complete=False
        if not self._censored:
            burning={tuple(p) for p in self._fire.burning()}
            if any(tuple(self._routes._guard._router._cell(g['position'])) in burning and not g['initial_exposed']
                   for g in roster['groups']):
                raise ValueError('burning handover cell requires initially exposed cohort')
        self._bus=EventBus(self._case['seed'],[self._case['centers'][0]])
        self._known={k:_view(v) for k,v in self._ops.resources.items()}
        self._known_at={k:0 for k in self._known}
        self._groups={g['id']:dict(_group(self._ops.groups[g['id']]),exposed=g['handover_exposure'])
                      for g in roster['groups']}
        self._group_at={k:0 for k in self._groups}
        self._group_rank={k:-1 for k in self._groups}
        self._exposed_at={g['id']:0 if g['handover_exposure'] is True else None for g in roster['groups']}
        self._dose={k:0 for k in self._groups}
        self._reports_seen=0
        self._calls=[]
        self._recording=True
        self._poisoned=False
        if _sources() != source_identity:
            raise RuntimeError('guarded dependencies changed during construction')
        self._identity=dict(version=VERSION,implementation=source_identity,reviewed_pair_sha256=_REVIEWED_PAIR_SHA256,
            routes=copy.deepcopy(self._routes.identity),
            fire=copy.deepcopy(diagnostic._identity),catalog=expected_catalog_sha256)
        if not self._censored:
            self._bus.emit('handover','handover',{'cells':copy.deepcopy(self._case['initial_fire']),
                'text':'Synthetic source-adapted exercise; observations may be stale.'},0,role='center',delay=0)
            self._emit_weather(0)
            self._guard_bases(self._ops,lambda *a,**k:self._bus.emit(*a,0,**k))
            self._bus.tick(0,{})
            self._sync()

    def _node(self, position):
        if (type(position) is not list or len(position)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) for v in position)
                or len(self._positions.get(tuple(position),[]))!=1):
            raise ValueError('position must be exactly one graph node')

    def _ensure(self):
        if self._poisoned: raise RuntimeError('guarded host indeterminate; restore trusted checkpoint')
        self._weather._ensure()
        _check_pair_identity(self._routes.identity)
        if (_sources()!=self._identity['implementation'] or self._routes.identity!=self._identity['routes']
                or self._identity['reviewed_pair_sha256'] != _REVIEWED_PAIR_SHA256
                or self._weather._identity!=self._identity['fire'] or api._hash(self._roster)!=self._identity['catalog']):
            self._poisoned=True;raise RuntimeError('guarded host identity changed')

    def _active(self):
        self._ensure()
        if self._censored: raise api.CensoredError('episode censored')
        if self._complete: raise RuntimeError('episode complete')

    def situation(self):
        self._ensure()
        if self._censored: return dict(version=VERSION,minute=self._minute,status='censored')
        return dict(version=VERSION,minute=self._minute,status='complete' if self._complete else 'active',
            scope='synthetic source-adapted exercise; access and deployments are not historical',
            resources=copy.deepcopy(list(self._known.values())),population=copy.deepcopy(list(self._groups.values())),
            destinations=copy.deepcopy(list(self._ops.destinations.values())),
            known_fire=copy.deepcopy(self._case['initial_fire']),fire_observed_at=0,reports_cursor=len(self._bus.reports))

    def reports(self, *, after_cursor=0, limit=20):
        self._ensure()
        if self._censored: raise api.CensoredError('reports suppressed after censoring')
        return self._bus.get_reports(after_cursor=after_cursor,limit=limit)

    def _public_options(self, result):
        return dict(status=result['status'],options=[dict(id=o['id'],travel_minutes=o['travel_minutes'])
                    for o in result['options']],scope='synthetic exercise; access unverified')

    def route_options(self, resource_id, destination):
        self._active()
        if resource_id not in self._known: raise ValueError('unknown reported resource')
        result=self._routes.dispatch_options(self._known[resource_id]['position'],destination,
            mode=self._known[resource_id]['kind'],burning=self._case['initial_fire'],wind=self._case['wind'])
        return self._public_options(result)

    def return_options(self, resource_id):
        self._active()
        if resource_id not in self._known: raise ValueError('unknown reported resource')
        unit=self._known[resource_id]
        return self._public_options(self._routes.return_options(unit['position'],unit['base_position'],
            mode=unit['kind'],burning=self._case['initial_fire'],wind=self._case['wind']))

    def evacuation_options(self, group_id, destination_id):
        self._active()
        if group_id not in self._groups or destination_id not in self._ops.destinations:
            raise ValueError('choose a reported cohort and destination')
        return self._public_options(self._routes.evacuation_options(self._groups[group_id]['position'],
            self._ops.destinations[destination_id]['position'],burning=self._case['initial_fire'],wind=self._case['wind']))

    def _record(self, name, args, kwargs, result):
        if self._recording:
            self._calls.append(dict(operation=name,args=copy.deepcopy(args),kwargs=copy.deepcopy(kwargs),result=copy.deepcopy(result)))
        return result

    def dispatch(self, resource_id, destination, option_id):
        self._active()
        if resource_id not in self._known: raise ValueError('unknown reported resource')
        options=self._routes.dispatch_options(self._known[resource_id]['position'],destination,
            mode=self._known[resource_id]['kind'],burning=self._case['initial_fire'],wind=self._case['wind'])['options']
        chosen=next((x for x in options if x['id']==option_id),None)
        if chosen is None: raise ValueError('unknown or stale guarded route')
        self._step(dict(kind='dispatch',resource=resource_id,journey=chosen['journey']))
        return self._record('dispatch',[resource_id,copy.deepcopy(destination),option_id],{},self.situation())

    def release(self, resource_id):
        self._active()
        if resource_id not in self._known: raise ValueError('unknown reported resource')
        self._step(dict(kind='release',resource=resource_id))
        return self._record('release',[resource_id],{},self.situation())

    def protect(self, group_id, action, *, destination_id=None, option_id=None):
        self._active()
        if group_id not in self._groups or action not in ('shelter','evacuate'):
            raise ValueError('invalid cohort or protection action')
        journey=None
        if action=='evacuate':
            if destination_id not in self._ops.destinations: raise ValueError('unknown destination')
            options=self._routes.evacuation_options(self._groups[group_id]['position'],self._ops.destinations[destination_id]['position'],
                burning=self._case['initial_fire'],wind=self._case['wind'])['options']
            chosen=next((x for x in options if x['id']==option_id),None)
            if chosen is None: raise ValueError('unknown or stale guarded evacuation route')
            journey=chosen['journey']
        elif destination_id is not None or option_id is not None:
            raise ValueError('shelter takes no destination or route')
        self._step(dict(kind='protect',group=group_id,action=action,destination=destination_id,journey=journey))
        result={'status':'submitted'} if not self._censored else self.situation()
        return self._record('protect',[group_id,action],dict(destination_id=destination_id,option_id=option_id),result)

    def advance(self, minutes=1):
        self._active()
        if type(minutes) is not int or not 1<=minutes<=60: raise ValueError('advance requires 1..60 minutes')
        for _ in range(minutes):
            self._step()
            if self._censored or self._complete: break
        return self._record('advance',[minutes],{},self.situation())

    def _wind(self):
        return weather.conditions(self._weather._profile,self._minute)['wind']

    def _safe(self, position, kind):
        return self._routes._guard._router.safe_position(position,self._fire.burning(),self._wind(),
            mode='engine',excluded_cells=self._routes._excluded)

    def _valid_journey(self, journey, mode):
        if mode not in ('engine','crew') or type(journey) is not dict: return False
        try:
            origin,destination=journey['origin'],journey['destination']
            if (len(self._positions.get(tuple(origin),[]))!=1 or len(self._positions.get(tuple(destination),[]))!=1
                or self._routes._guard.assess_origin(origin)['status']=='ambiguous'
                or self._routes._guard.assess_origin(destination)['status']=='ambiguous'
                or not journey['legs'] or journey.get('emergency_egress')): return False
            cursor=origin
            for leg in journey['legs']:
                edge=self._routes._network.edges.get(leg.get('edge_id'))
                if (edge is None or leg.get('mode')!='road' or edge['id'] not in self._routes._allowed
                    or mode not in edge['allowed_modes'] or 'engine' not in edge['allowed_modes']
                    or leg.get('coordinates')!=edge['coordinates'] or leg['coordinates'][0]!=cursor
                    or leg.get('destination_node')!=edge['target']): return False
                cursor=leg['coordinates'][-1]
            return cursor==destination
        except (KeyError,TypeError,ValueError,IndexError):
            return False

    def _movement(self, journey, minute, kind):
        mode='engine' if kind=='group' else kind
        if not self._valid_journey(journey,mode): return False
        return self._routes._guard._router.safe_journey_segment(journey,minute-1,self._fire.burning(),self._wind(),
            mode='engine',excluded_cells=self._routes._excluded)

    def _return_plan(self, unit, minute):
        result=self._routes.return_options(unit['position'],unit['base_position'],mode=unit['kind'],
            burning=self._fire.burning(),wind=self._wind())
        return copy.deepcopy(result['options'][0]['journey']) if result['options'] else None

    def _guard_bases(self, ops, emit):
        for unit in ops.resources.values():
            state=unit['state']
            if (state in ('available','on_scene','working','refilling','resting','unavailable')
                and _same_position(unit['position'],unit['base_position']) and not self._safe(unit['position'],unit['kind'])):
                if state=='refilling': ops._cancel_refill(unit)
                unit.update(state='needs_assistance',journey=None)
                unit.pop('blocked_state',None)
                emit(unit['id'],'resource_status',{'resource':_view(unit)},actionable=True)

    def _emit_weather(self, minute):
        for record in self._weather._profile.records:
            if record.minute==minute:
                self._bus.emit('weather','weather_update',dict(wind=list(record.wind),temperature_c=record.temperature_c,
                    relative_humidity_pct=record.relative_humidity_pct,rain_mm_per_hour=record.rain_mm_per_hour,
                    text='Synthetic exercise weather; uncalibrated proxy.'),minute,
                    delay=record.received_at-record.minute)

    def _sync(self):
        for record in self._bus.reports[self._reports_seen:]:
            payload=record['payload']
            resource=payload.get('resource')
            if isinstance(resource,dict) and resource.get('id') in self._known:
                key=resource['id']
                if record['observed_at']>=self._known_at[key]:
                    self._known[key]=copy.deepcopy(resource);self._known_at[key]=record['observed_at']
            group=payload.get('group')
            if not isinstance(group,dict) or group.get('id') not in self._groups: continue
            key=group['id'];observed=record['observed_at'];rank=1 if record['kind']=='resident_update' else 0
            if group.get('exposed') is True:
                old=self._exposed_at[key]
                self._exposed_at[key]=observed if old is None else min(old,observed)
            old_exposure=self._groups[key]['exposed']
            if (observed,rank)>(self._group_at[key],self._group_rank[key]):
                self._groups[key]=copy.deepcopy(group);self._group_at[key]=observed;self._group_rank[key]=rank
            if self._exposed_at[key] is not None: self._groups[key]['exposed']=True
            elif old_exposure is False or group.get('exposed') is False: self._groups[key]['exposed']=False
        self._reports_seen=len(self._bus.reports)

    def _step(self, command=None):
        try:
            if not self._fire._check():
                self._weather._status='censored';self._censored=True;return
            self._minute+=1
            ops=Operations.restore(self._ops.checkpoint())
            pending=[]
            def emit(source,kind,payload,**kwargs):
                payload=copy.deepcopy(payload)
                # Order acknowledgments do not themselves observe hidden
                # population exposure or position. Only resident updates
                # carry a population snapshot after the physical tick.
                pending.append((source,kind,payload,copy.deepcopy(kwargs)))
            if command:
                kind=command['kind']
                if kind=='dispatch':
                    u=ops.resources[command['resource']];journey=command['journey']
                    candidates=self._routes.dispatch_options(u['position'],journey['destination'],mode=u['kind'],
                        burning=self._fire.burning(),wind=self._wind())['options']
                    path=[leg['edge_id'] for leg in journey['legs']]
                    safe=(self._valid_journey(journey,u['kind']) and self._safe(u['position'],u['kind'])
                        and any([leg['edge_id'] for leg in o['journey']['legs']]==path for o in candidates))
                    accepted=bool(safe and ops.dispatch(u['id'],'incident',
                        list(self._routes._guard._router._cell(journey['destination'])),journey,self._minute))
                    emit('operations','dispatch_result',dict(resource_id=u['id'],accepted=accepted))
                elif kind=='release':
                    u=ops.resources[command['resource']]
                    accepted=ops.release(u['id'],self._return_plan(u,self._minute),self._minute)
                    emit('operations','release_result',dict(resource_id=u['id'],accepted=bool(accepted)))
                else:
                    accepted=True
                    try: ops.protect(command['group'],command['action'],command['destination'],command['journey'],self._minute)
                    except ValueError: accepted=False
                    emit('operations','protection_request_result',dict(group_id=command['group'],accepted=accepted))
            previous={k:u['state'] for k,u in ops.resources.items()}
            groups_before={k:_group(g) for k,g in ops.groups.items()}
            ops.tick(self._minute,wind=self._wind(),daylight=True,hazard_check=self._safe,
                     movement_check=self._movement,suppress=lambda unit:False,emit=emit,return_planner=self._return_plan)
            self._guard_bases(ops,emit)
            state=self._weather.step()
            self._guard_bases(ops,emit)
            self._ops=ops
            if state['status']=='censored': self._censored=True;return
            burning=self._fire.burning()
            for identifier,group in ops.groups.items():
                cell=self._routes._guard._router._cell(group['position'])
                if any(math.dist(cell,p)<=1.5 for p in burning):
                    self._dose[identifier]+=2 if group['status']=='sheltering' else 10
                if list(cell) in burning or self._dose[identifier]>=180: group['exposed']=True
                if _group(group)!=groups_before[identifier]:
                    emit(identifier,'resident_update',{'group':_group(group)},role='resident',channel='radio')
            for identifier,unit in ops.resources.items():
                if previous[identifier]!=unit['state'] or self._minute%30==0:
                    emit(identifier,'resource_status',{'resource':_view(unit)},actionable=True)
            for source,kind,payload,kwargs in pending:
                self._bus.emit(source,kind,payload,self._minute,**kwargs)
            self._emit_weather(self._minute)
            self._bus.tick(self._minute,{})
            self._sync()
            self._complete=state['status']=='diagnostic_end'
        except BaseException:
            self._poisoned=True
            raise

    def private_handover_exposure(self):
        """Declared inherited whole-cohort counts for a trusted scorer only."""
        self._ensure()
        return {g['id']:g['count'] if g['initial_exposed'] else 0 for g in self._roster['groups']}

    def transcript(self):
        """Public call history for a trusted evaluator; actor arguments may be sensitive."""
        self._ensure()
        return dict(version=VERSION,identity_sha256=api._hash(self._identity),calls=copy.deepcopy(self._calls))

    def replay(self, transcript, *, expected_sha256, expected_identity_sha256):
        self._active()
        if self._minute!=0 or self._calls: raise ValueError('replay requires a fresh host')
        if (type(transcript) is not dict or set(transcript)!={'version','identity_sha256','calls'}
            or transcript['version']!=VERSION or api._hash(transcript)!=expected_sha256
            or transcript['identity_sha256']!=expected_identity_sha256
            or expected_identity_sha256!=api._hash(self._identity) or type(transcript['calls']) is not list):
            raise ValueError('transcript or independently trusted identity differs')
        self._recording=False
        try:
            for entry in transcript['calls']:
                if (type(entry) is not dict or set(entry)!={'operation','args','kwargs','result'}
                    or entry['operation'] not in ('dispatch','release','protect','advance')
                    or type(entry['args']) is not list or type(entry['kwargs']) is not dict):
                    raise ValueError('unsupported transcript call')
                result=getattr(self,entry['operation'])(*entry['args'],**entry['kwargs'])
                if api._encoded(result)!=api._encoded(entry['result']): raise ValueError('public replay differs')
            self._calls=copy.deepcopy(transcript['calls'])
            return self.situation()
        except BaseException:
            self._poisoned=True;raise
        finally:
            self._recording=True

    def checkpoint(self):
        self._ensure()
        value=dict(version=VERSION,identity=copy.deepcopy(self._identity),minute=self._minute,censored=self._censored,
            complete=self._complete,weather=self._weather.checkpoint(),operations=self._ops.checkpoint(),
            communications=self._bus.checkpoint(),known=copy.deepcopy(self._known),known_at=copy.deepcopy(self._known_at),
            groups=copy.deepcopy(self._groups),group_at=copy.deepcopy(self._group_at),group_rank=copy.deepcopy(self._group_rank),
            exposed_at=copy.deepcopy(self._exposed_at),dose=copy.deepcopy(self._dose),reports_seen=self._reports_seen,
            calls=copy.deepcopy(self._calls))
        value['sha256']=api._hash(value)
        return value

    def restore(self, checkpoint):
        try:
            value=copy.deepcopy(checkpoint)
            keys={'version','identity','minute','censored','complete','weather','operations','communications','known',
                  'known_at','groups','group_at','group_rank','exposed_at','dose','reports_seen','calls','sha256'}
            if (type(value) is not dict or set(value)!=keys or value['version']!=VERSION
                or api._encoded(value['identity'])!=api._encoded(self._identity)
                or _sources()!=self._identity['implementation'] or self._routes.identity!=self._identity['routes']
                or value['sha256']!=api._hash({k:v for k,v in value.items() if k!='sha256'})
                or type(value['minute']) is not int or not 0<=value['minute']<=1440
                or type(value['censored']) is not bool or type(value['complete']) is not bool
                or value['complete']!=(value['minute']==1440 and not value['censored'])):
                raise ValueError('guarded checkpoint identity or clock differs')
            _check_pair_identity(self._routes.identity)
            if self._identity['reviewed_pair_sha256'] != _REVIEWED_PAIR_SHA256:
                raise ValueError('guarded reviewed-pair identity differs')
            self._weather.restore(value['weather']);self._fire=self._weather._fire
            ops=Operations.restore(value['operations']);bus=EventBus.restore(value['communications'])
            if (ops.profile!=PROFILE or ops.last_minute!=(value['minute'] if value['minute'] else None)
                or self._weather._minute!=value['minute'] or (self._weather._status=='censored')!=value['censored']
                or (self._weather._status=='diagnostic_end')!=value['complete'] or bus.seed!=self._case['seed']
                or bus._last_tick>value['minute'] or type(value['reports_seen']) is not int
                or value['reports_seen']!=len(bus.reports)):
                raise ValueError('guarded clocks or communications differ')
            roster_resources={u['id']:u for u in self._roster['resources']}
            roster_groups={g['id']:g for g in self._roster['groups']}
            roster_dest={d['id']:dict(d,status='open') for d in self._roster['destinations']}
            if (set(ops.resources)!=set(roster_resources) or set(ops.groups)!=set(roster_groups)
                or ops.destinations!=roster_dest or set(value['known'])!=set(roster_resources)
                or set(value['known_at'])!=set(roster_resources) or set(value['groups'])!=set(roster_groups)
                or set(value['group_at'])!=set(roster_groups) or set(value['group_rank'])!=set(roster_groups)
                or set(value['exposed_at'])!=set(roster_groups) or set(value['dose'])!=set(roster_groups)
                or type(value['calls']) is not list): raise ValueError('guarded roster differs')
            for key,unit in ops.resources.items():
                original=roster_resources[key]
                if unit['kind']!=original['kind'] or unit['base_position']!=original['position']:
                    raise ValueError('resource identity differs')
                if unit.get('journey') and not self._valid_journey(unit['journey'],unit['kind']):
                    raise ValueError('unverified resource journey')
            for key,group in ops.groups.items():
                original=roster_groups[key]
                if (group['count']!=original['count'] or group['home']!=original['position']
                    or (original['initial_exposed'] and group['exposed'] is not True)):
                    raise ValueError('cohort identity or exposure differs')
            for order in ops.orders.values():
                if order.get('journey') and not self._valid_journey(order['journey'],'engine'):
                    raise ValueError('unverified evacuation journey')
            if (any(type(x) is not int or x<0 for x in value['dose'].values())
                or any(type(x) is not int or not 0<=x<=value['minute'] for x in (*value['known_at'].values(),*value['group_at'].values()))
                or any(type(x) is not int or x not in (-1,0,1) for x in value['group_rank'].values())
                or any(x is not None and (type(x) is not int or not 0<=x<=value['minute']) for x in value['exposed_at'].values())):
                raise ValueError('invalid delivered knowledge')
            self._ops,self._bus=ops,bus
            self._minute,self._censored,self._complete=value['minute'],value['censored'],value['complete']
            self._known,self._known_at=value['known'],value['known_at']
            self._groups,self._group_at,self._group_rank=value['groups'],value['group_at'],value['group_rank']
            self._exposed_at,self._dose=value['exposed_at'],value['dose']
            self._reports_seen,self._calls=value['reports_seen'],value['calls']
            self._poisoned=False
            if api._encoded(self.checkpoint())!=api._encoded(value): raise ValueError('guarded restore differs')
        except BaseException:
            self._poisoned=True;raise
