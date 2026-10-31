"""Private single-minute population Operations stage for a future domain host.

This component does not advance fire, emit public reports, commit state or score.
All returned Operations state, journeys and events are trusted-driver material.
"""
import copy
from datetime import timedelta
import hashlib
from pathlib import Path

import adapter_v1 as api
from envs.wildfire_response.incident_day.operations import Operations
from envs.wildfire_response.incident_day import clock as clock_module
from eval import incident_weather_v1 as weather
from wildfire_source_domain_ground_v1.host import DomainProfileGroundIncidentV1
from wildfire_source_domain_v3.host import SUPPORTED as _SUPPORTED_EVENTS
from wildfire_source_population_anchor_v1.adapter import PopulationAnchorAdapterV1
from wildfire_source_joint_v1.segment_movement import PopulationSegmentMovementV1

VERSION='wildfire-source-population-operations-stage-v2'
_SUPPORTED_EVENTS=frozenset(_SUPPORTED_EVENTS)


class UnsupportedScheduledEvent(RuntimeError):
    """The bound host must stop before this minute; details stay private."""
_ROOT=Path(__file__).resolve().parents[2]
_BOUND=(DomainProfileGroundIncidentV1,Operations,PopulationAnchorAdapterV1,PopulationSegmentMovementV1,weather.conditions,clock_module.solar_daylight)


def _source():
    path=_ROOT/'envs/wildfire_source_population_tick_v2/__init__.py'
    if (Path(__file__).resolve()!=path or (DomainProfileGroundIncidentV1,Operations,PopulationAnchorAdapterV1,PopulationSegmentMovementV1,weather.conditions,clock_module.solar_daylight)!=_BOUND):
        raise RuntimeError('population stage bindings differ')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _identity(host,anchors,routes,case_id):
    if type(host) is not DomainProfileGroundIncidentV1 or type(anchors) is not PopulationAnchorAdapterV1 or type(routes) is not PopulationSegmentMovementV1:
        raise ValueError('exact source components required')
    host._ensure();anchors._ensure();routes._ensure()
    case=anchors.private_case(case_id)
    route_identity=routes._identity['route']
    if (anchors._selector is not host._selector or anchors._identity['selector']!=host._selector._identity
            or case.get('original_case_id')!=host._case['id']
            or case.get('original_case_sha256')!=host._case['sha256']
            or route_identity['anchors_sha256']!=anchors._identity['candidate']
            or route_identity['graph_sha256']!=host._selector._identity['graph']
            or route_identity['source_sha256']!=host._selector._identity['source']):
        raise ValueError('population source lineage differs')
    return dict(version=VERSION,source=_source(),host=copy.deepcopy(host._identity),
                anchors=copy.deepcopy(anchors._identity),routes=copy.deepcopy(routes._identity),
                case_id=case_id,adaptation_sha256=api._hash(case))


def configuration_identity(host,anchors,routes,case_id):
    """Candidate for independent review; this does not authorize a release."""
    return _identity(host,anchors,routes,case_id)


class PopulationOperationsStageV1:
    """Stage a single detached Operations tick; the owner must commit atomically."""
    def __init__(self,host,anchors,routes,*,case_id,expected_identity,opt_in=False):
        if opt_in is not True:raise ValueError('explicit population exercise opt-in required')
        identity=_identity(host,anchors,routes,case_id)
        if type(expected_identity) is not dict or api._encoded(identity)!=api._encoded(expected_identity):
            raise ValueError('independently approved population identity differs')
        self._host=host;self._anchors=anchors;self._routes=routes;self._identity=copy.deepcopy(identity)
        self._case=anchors.private_case(case_id);self._source=_source()
        self._groups={g['id']:copy.deepcopy(g) for g in self._case['adapted_groups']}
        self._destinations={d['id']:copy.deepcopy(d) for d in self._case['adapted_destinations']}
        self._original_case=self._case['original_case_id']
        self._ensure()

    def _ensure(self):
        if (_source()!=self._source or _identity(self._host,self._anchors,self._routes,self._identity['case_id'])!=self._identity
                or api._hash(self._case)!=self._identity['adaptation_sha256']):
            raise RuntimeError('population stage identity changed')
        ops=self._host._ops
        if set(ops.groups)!=set(self._groups) or set(ops.destinations)!=set(self._destinations):
            raise ValueError('anchored population roster is not installed')
        for key,group in ops.groups.items():
            source=self._groups[key]
            if group['count']!=source['count'] or group['home']!=source['home']:
                raise ValueError('population identity differs from anchor')
            if self._host._minute==0 and any(group.get(k)!=source.get(k) for k in ('position','status','exposed','destination_id')):
                raise ValueError('population handover differs from anchor')
        for key,destination in ops.destinations.items():
            source=self._destinations[key]
            if any(destination.get(k)!=source.get(k) for k in ('position','capacity')):
                raise ValueError('destination identity differs from anchor')

    def _excluded(self):
        # The exact-edge exception never promotes domain estimates or unknown
        # cells to source-backed route evidence.
        inputs=self._host._domain_inputs;c=inputs.base.contract
        return frozenset((x,y) for y in range(c.height) for x in range(c.width)
            if not c.domain[y][x] or c.surface[y][x] not in ('land','mixed')
            or c.elevation[y][x] is None or inputs.source_mask[y][x] is not True
            or inputs.estimated_mask[y][x] is not False or not self._host._fire._is_land(x,y))

    def stage_tick(self,*,minute,command=None,suppress,other_hazard,other_movement,other_action=None):
        """Stage one tick while requiring the owner's state to stay unchanged."""
        self._ensure();host=self._host
        before=api._hash(host.checkpoint())
        pending=copy.deepcopy(getattr(host._fire,'_pending',None))
        try:
            return self._stage_tick(minute=minute,command=command,suppress=suppress,
                other_hazard=other_hazard,other_movement=other_movement,other_action=other_action)
        finally:
            try:
                unchanged=(api._hash(host.checkpoint())==before
                           and getattr(host._fire,'_pending',None)==pending)
            except BaseException:unchanged=False
            if not unchanged:
                host._poisoned=True
                raise RuntimeError('population staging changed the live owner')

    def _stage_tick(self,*,minute,command=None,suppress,other_hazard,other_movement,other_action):
        """Return detached `(operations, events)`; never expose either to actors.

        The caller must stage fire work, reconcile costs and atomically commit
        this state on the same minute, or discard it. This function never
        advances the physical fire, EventBus or host clock.
        """
        self._ensure();host=self._host
        if host._status!='active' or type(minute) is not int or minute!=host._minute+1 or minute>1440:
            raise ValueError('next active host minute required')
        if (not callable(suppress) or not callable(other_hazard) or not callable(other_movement)
                or (other_action is not None and not callable(other_action))):
            raise ValueError('explicit Operations callbacks required')
        if command is not None and (type(command) is not dict or command.get('kind') not in ('shelter','evacuate')):
            raise ValueError('invalid population command')
        # Match the bound source host: unsupported events stop before any
        # physical check or Operations staging, without disclosing the kind.
        if any(event['minute']==minute and event['kind'] not in _SUPPORTED_EVENTS for event in host._case['events']):
            raise UnsupportedScheduledEvent('unsupported scheduled event')
        if not host._fire._check():raise api.CensoredError('population stage censored')
        burning=host._fire.burning();wind=weather.conditions(host._weather._profile,minute)['wind']
        excluded=self._excluded()
        blocked=any(event['kind']=='access_restriction' and event.get('resource_kind')=='engine'
                    and event['minute']<=minute for event in host._case['events'])
        staged=Operations.restore(host._ops.checkpoint(),return_planner=host._return_plan)
        air_until=host._air_until
        for event in host._case['events']:
            if event['minute']!=minute:continue
            if event['kind']=='destination_closed':
                staged.destinations[event['destination_id']]['status']='closed'
            elif event['kind']=='aircraft_unavailable':
                air_until=max(air_until,event['until'])
        daylight=bool(clock_module.solar_daylight(host._start+timedelta(minutes=minute),
                                                 host._longitude,host._latitude) and minute>=air_until)
        pending=[]
        def emit(source,kind,payload,**kwargs):pending.append((source,kind,copy.deepcopy(payload),copy.deepcopy(kwargs)))
        staged.set_flight_conditions(wind,daylight)
        if other_action is not None:other_action(staged,emit,minute)
        if command is not None:
            kind=command['kind'];group=command.get('group_id');accepted=False
            required={'kind','group_id'} if kind=='shelter' else {'kind','group_id','destination_id','option_id'}
            if set(command)!=required or group not in staged.groups:
                raise ValueError('invalid population request')
            destination=command.get('destination_id');journey=None
            if kind=='evacuate':
                if destination not in staged.destinations or type(command['option_id']) is not str:
                    raise ValueError('invalid population request')
                if not blocked:
                    options=self._routes.options(self._original_case,group,destination,staged.groups[group]['position'],
                        burning=burning,wind=wind,excluded_cells=excluded)
                    choices=[o for o in options.get('options',[]) if o.get('id')==command['option_id']]
                    if len(choices)==1:journey=choices[0]['journey']
            if kind=='shelter' or journey is not None:
                try:staged.protect(group,kind,destination,journey,minute);accepted=True
                except ValueError:pass
            emit('operations','request_result',{'kind':kind,'accepted':accepted})
        router=self._routes._route._guard._router
        hazards=router._hazards(burning,wind) | excluded
        def hazard(position,kind):
            if kind=='group':return router._safe_cell(router._cell(position),hazards,'engine')
            return other_hazard(position,kind) is True
        def movement(journey,m,kind):
            if kind!='group':return other_movement(journey,m,kind) is True
            if blocked:return False
            matches=[]
            for gid,group in staged.groups.items():
                for order_id in (group.get('order_id'),group.get('pending_order_id')):
                    order=staged.orders.get(order_id)
                    if order and order.get('journey')==journey and order.get('state') in ('executing','blocked'):
                        matches.append((gid,group,order))
            if len(matches)!=1:return False
            gid,group,order=matches[0]
            return self._routes.movement_safe(self._original_case,gid,order['destination_id'],journey,m,
                current_position=group['position'],burning=burning,wind=wind,excluded_cells=excluded) is True
        staged.tick(minute,wind=wind,daylight=daylight,hazard_check=hazard,movement_check=movement,
                    suppress=suppress,emit=emit,return_planner=host._return_plan)
        return staged,pending
