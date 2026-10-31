"""Private one-clock domain-ground and anchored-population owner, unscored."""
import copy
import hashlib
import math
from pathlib import Path

import adapter_v1 as api
from envs.wildfire_response.incident_day.events import EventBus
from envs.wildfire_response.incident_day.operations import Operations
from wildfire_source_domain_ground_v1.hold import DomainSourceWalkHoldV1
from wildfire_source_population_anchor_v1.adapter import PopulationAnchorAdapterV1
from wildfire_source_joint_v1.segment_movement import PopulationSegmentMovementV1
from wildfire_source_population_tick_v2 import PopulationOperationsStageV1,configuration_identity as stage_identity
from wildfire_source_population_tick_v2 import UnsupportedScheduledEvent
from wildfire_source_public_facade_v1 import redact_reports

VERSION='wildfire-domain-population-ground-incident-v1'
SCOPE='synthetic_domain_ground_population_unscored'
_ROOT=Path(__file__).resolve().parents[2]


def _source():
    path=_ROOT/'envs/wildfire_domain_population_host_v1/__init__.py'
    if Path(__file__).resolve()!=path:raise RuntimeError('population owner origin differs')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _unit(unit):
    keys=('id','kind','position','base_position','state','water_litres','capacity_litres',
          'fuel_minutes','fuel_capacity_minutes','work_remaining','total_work_remaining','available_at','clearance')
    return {k:copy.deepcopy(unit[k]) for k in keys if k in unit}


class DomainPopulationGroundV1:
    """One owner commits anchored population, aircraft, ground and fire once.

    Public cohort/destination identifiers are synthetic aliases. Inputs,
    mappings, paths, checkpoints and transcripts remain private. No score.
    """
    def __init__(self,hold,anchors,routes,*,opt_in=False):
        if (opt_in is not True or type(hold) is not DomainSourceWalkHoldV1
                or type(anchors) is not PopulationAnchorAdapterV1 or type(routes) is not PopulationSegmentMovementV1):
            raise ValueError('explicit opt-in and exact source components required')
        hold._ensure();walk=hold._walk;host=walk._host
        if host._minute!=0 or hold._calls or walk._calls or host._calls or host._status!='active':
            raise ValueError('fresh active handover required')
        anchors._ensure();routes._ensure()
        case_id=host._identity['case'];case=anchors.private_case(case_id)
        if (anchors._selector is not host._selector or case['original_case_id']!=host._case['id']
                or case['original_case_sha256']!=host._case['sha256']):
            raise ValueError('population and host lineage differ')
        groups=copy.deepcopy(case['adapted_groups']);destinations=copy.deepcopy(case['adapted_destinations'])
        if ({g['id'] for g in groups}!=set(host._ops.groups) or {d['id'] for d in destinations}!=set(host._ops.destinations)):
            raise ValueError('anchored population roster differs')
        projected=Operations(list(host._ops.resources.values()),destinations,groups,host._ops.profile,return_planner=host._return_plan)
        t=host._inputs.contract.transform;burning={tuple(p) for p in host._fire.burning()}
        for group in projected.groups.values():
            x=math.floor((group['position'][0]-t[2])/t[0]);y=math.floor((group['position'][1]-t[5])/t[4])
            if not (0<=x<host._inputs.contract.width and 0<=y<host._inputs.contract.height):
                raise ValueError('population position outside domain')
            if (x,y) in burning and not group['exposed']:
                raise ValueError('inherited exposure declaration differs')
        self._hold=hold;self._host=host;self._walk=walk;self._anchors=anchors;self._routes=routes;self._case=case_id
        private_ids=set(projected.groups)|set(projected.destinations)|set(host._case['centers'])|set(projected.resources)
        prefix=''
        # Keep actor aliases disjoint even when source IDs use common labels
        # such as "destination-1". This choice is deterministic for replay.
        while True:
            group_ids={f'{prefix}cohort-{i+1}':key for i,key in enumerate(sorted(projected.groups))}
            destination_ids={f'{prefix}destination-{i+1}':key for i,key in enumerate(sorted(projected.destinations))}
            center_ids={f'{prefix}center-{i+1}':key for i,key in enumerate(sorted(host._case['centers']))}
            if not private_ids & (set(group_ids)|set(destination_ids)|set(center_ids)):break
            prefix+='public-'
        self._group_ids=group_ids;self._destination_ids=destination_ids
        self._group_alias={v:k for k,v in self._group_ids.items()};self._destination_alias={v:k for k,v in self._destination_ids.items()}
        self._center_ids=center_ids
        self._population=[dict(id=alias,count=projected.groups[key]['count'],status=projected.groups[key]['status'],
                               exposed=None,observed_at=0) for alias,key in self._group_ids.items()]
        self._destinations=[dict(id=alias,observed_at=0) for alias in self._destination_ids]
        self._source=_source();old_ops=host._ops;old_seal=walk._seal
        try:
            host._ops=projected;walk._seal=api._hash(host.checkpoint())
            candidate=stage_identity(host,anchors,routes,case_id)
            self._stage=PopulationOperationsStageV1(host,anchors,routes,case_id=case_id,expected_identity=candidate,opt_in=True)
            self._identity=dict(version=VERSION,scope=SCOPE,implementation=self._source,hold=copy.deepcopy(hold._identity),
                population_stage=copy.deepcopy(candidate),anchored_handover=api._hash(hold.checkpoint()),
                public_population_sha256=api._hash(self._population),public_destinations_sha256=api._hash(self._destinations),
                mappings_sha256=api._hash(dict(groups=self._group_ids,destinations=self._destination_ids,centers=self._center_ids)),
                disclosure='synthetic aliases and handover counts; review candidate, not source route geometry')
            self._identity_seal=api._hash(self._identity);self._seal=api._hash(hold.checkpoint());self._calls=[];self._poisoned=False
        except BaseException:
            host._ops=old_ops;walk._seal=old_seal
            raise

    def _ensure(self):
        if self._poisoned:raise RuntimeError('population owner indeterminate; restore trusted checkpoint')
        try:
            self._hold._ensure();self._stage._ensure()
            if (_source()!=self._source or api._hash(self._identity)!=self._identity_seal
                    or api._hash(self._population)!=self._identity['public_population_sha256']
                    or api._hash(self._destinations)!=self._identity['public_destinations_sha256']
                    or api._hash(dict(groups=self._group_ids,destinations=self._destination_ids,centers=self._center_ids))!=self._identity['mappings_sha256']
                    or api._hash(self._hold.checkpoint())!=self._seal):
                raise RuntimeError('population owner identity or state changed')
        except BaseException:self._poisoned=True;raise

    def situation(self):
        self._ensure();view=self._hold.situation()
        view['version']=VERSION
        if view['status']=='censored':return view
        groups={row['id']:copy.deepcopy(row) for row in self._population};latest={k:(0,'') for k in groups}
        for report in self._host._bus.reports:
            p=report['payload'];key=p.get('group_id')
            if key not in groups:continue
            if report['kind']=='synthetic_population_status':
                order=(report['observed_at'],report['id'])
                if order>latest[key]:
                    groups[key]['status']=p['status'];groups[key]['observed_at']=report['observed_at'];latest[key]=order
            elif report['kind']=='synthetic_population_exposure' and p.get('exposed') is True:
                groups[key]['exposed']=True
        view.update(scope=SCOPE,population=list(groups.values()),destinations=copy.deepcopy(self._destinations),centers=list(self._center_ids),
                    limitations=['synthetic population aliases','exact-edge exercise routes only',
                                 'unknown access elsewhere','no verified road return','unscored'])
        return view

    def reports(self,*,after_cursor=0,limit=20):
        self._ensure();records=redact_reports(self._hold.reports(after_cursor=after_cursor,limit=limit))
        aliases={private:public for public,private in self._center_ids.items()}
        for record in records:
            if record['kind']=='status_response' and record['role']=='center' and record['source'] in aliases:
                record['source']=aliases[record['source']]
        return records

    def _evacuation_options(self,group_id,destination_id):
        self._ensure();view=self.situation()
        if (view['status']!='active' or group_id not in self._group_ids or destination_id not in self._destination_ids):
            raise ValueError('unknown public population or destination')
        known=next(row for row in view['population'] if row['id']==group_id)
        if known['status'] not in ('unwarned','at_home','sheltering') or self._host._known_wind is None:
            return dict(status='unavailable',options=[]),{}
        private_group=self._group_ids[group_id];private_dest=self._destination_ids[destination_id]
        # Planning uses the fixed handover position and delivered wind/fire,
        # not the live private group position or true fire.
        origin=self._stage._groups[private_group]['position']
        result=self._routes.options(self._stage._original_case,private_group,private_dest,origin,
            burning=self._host._handover_fire,wind=self._host._known_wind,excluded_cells=self._stage._excluded())
        options=result.get('options',[])
        if len(options)>1:raise RuntimeError('unexpected population route options')
        aliases={f'route-{i+1}':o['id'] for i,o in enumerate(options)}
        return dict(status=result.get('status','unavailable'),options=[dict(id=f'route-{i+1}',travel_minutes=o['travel_minutes'])
                    for i,o in enumerate(options)]),aliases

    def evacuation_options(self,group_id,destination_id):
        return self._evacuation_options(group_id,destination_id)[0]

    def _emit_pending(self,bus,pending,minute,public_group=None):
        for source,kind,payload,kwargs in pending:
            p=copy.deepcopy(payload)
            if 'group_id' in p:
                if p['group_id'] in self._group_alias:p['group_id']=self._group_alias[p['group_id']]
                elif p['group_id'] not in self._group_ids:raise RuntimeError('unknown group event')
            if 'destination_id' in p:
                if p['destination_id'] in self._destination_alias:p['destination_id']=self._destination_alias[p['destination_id']]
                elif p['destination_id'] not in self._destination_ids:raise RuntimeError('unknown destination event')
            p.pop('position',None);p.pop('journey',None)
            if kind=='request_result' and public_group is not None:p['group_id']=public_group
            bus.emit(source,kind,p,minute,**kwargs)

    def _step(self,*,population=None,public_group=None,walk_command=None,hold_command=False,air_command=None):
        self._ensure();host=self._host;walk=self._walk;hold=self._hold;minute=host._minute+1
        if host._status!='active':raise ValueError('incident is not active')
        try:
            if not host._fire._check():
                host._weather._status=host._status='censored';walk._seal=api._hash(host.checkpoint())
                self._seal=api._hash(hold.checkpoint());return
            due=[e for e in host._case['events'] if e['minute']==minute]
            from wildfire_source_domain_v3.host import SUPPORTED
            if any(e['kind'] not in SUPPORTED for e in due):
                host._status='unsupported';host._reason='unsupported_scheduled_event'
                host._unsupported_kind=next(e['kind'] for e in due if e['kind'] not in SUPPORTED)
                walk._seal=api._hash(host.checkpoint());self._seal=api._hash(hold.checkpoint());return
            conditions=walk._conditions(minute);ground,ground_changed=walk._ground_tick(walk._ground,minute,*conditions,walk_command)
            hold_state,assignments,hold_accepted,hold_changed=hold._prepare(minute,walk_command,hold_command)
            before_ground=copy.deepcopy(walk._ground);before_groups=copy.deepcopy(host._ops.groups)
            before_units={k:_unit(u) for k,u in host._ops.resources.items()}
            bus=EventBus.restore(host._bus.checkpoint());closed=copy.deepcopy(host._closed);air_until=host._air_until
            pending=[];drops=[];pre_tick={}
            for event in due:
                if event['kind']=='center_unavailable':closed[event['center_id']]=max(closed.get(event['center_id'],0),event['until'])
                elif event['kind']=='aircraft_unavailable':
                    air_until=max(air_until,event['until']);pending.append(('regional','aircraft_window',{'until':air_until},{}))
                elif event['kind']=='destination_closed':pending.append(('destination','destination_threatened',{'text':'A destination has closed.'},{'urgent':True}))
                elif event['kind']=='access_restriction':pending.append(('road-patrol','access_restriction',{'resource_kind':event['resource_kind']},{'urgent':True}))
            shadow={tuple(p):copy.deepcopy(host._fire._fire.cells[p[1]][p[0]]) for p in host._fire.burning()}
            def drop(unit):
                if unit['kind']!='aircraft':return False
                target=unit.get('target_grid')
                for point in sorted(shadow):
                    cell=shadow[point]
                    if target is not None and math.dist(point,target)<=3 and cell['heat']>0 and cell['fuel']>0:
                        before=cell['heat'];cell['heat']=max(0.,before-.15);cell['wet']=max(cell.get('wet',0),5)
                        drops.append(dict(resource_id=unit['id'],cell=point,before=before,after=cell['heat'],
                            work_before=unit['work_remaining'],water_before=unit['water_litres'],total_before=unit.get('total_work_remaining')))
                        return True
                return False
            def action(ops,emit,m):
                if air_command:
                    kind=air_command['kind'];key=air_command.get('resource')
                    if kind=='clear':accepted=ops.clear_aircraft(key,m);emit('operations','clearance_result',dict(resource_id=key,accepted=bool(accepted)))
                    elif kind=='dispatch':
                        accepted=ops.dispatch(key,'incident',air_command['target'],air_command['journey'],m)
                        emit('operations','dispatch_result',dict(resource_id=key,accepted=bool(accepted)))
                    elif kind=='release':
                        unit=ops.resources[key];accepted=ops.release(key,host._return_plan(unit,m),m)
                        if accepted and unit['state']=='returning':
                            unit['journey']['departed_at']=m-1;unit['journey']['arrives_at']=m-1+unit['journey']['duration_minutes']
                        emit('operations','release_result',dict(resource_id=key,accepted=bool(accepted)))
                    elif kind=='status':bus.request_status(air_command['center'],'situation',m-1)
                    else:raise ValueError('unsupported aircraft command')
                for key,unit in ops.resources.items():
                    if unit['kind']=='aircraft':pre_tick[key]=dict(state=unit['state'],blocked_state=unit.get('blocked_state'),
                        fuel=unit.get('fuel_minutes'),position=copy.deepcopy(unit['position']))
            hold._fire.stage(assignments)
            staged,events=self._stage.stage_tick(minute=minute,command=population,suppress=drop,
                other_hazard=lambda p,k:k=='aircraft',other_movement=lambda j,m,k:k=='aircraft',other_action=action)
            for identifier in sorted({p['resource_id'] for _,kind,p,_ in events if kind=='aircraft_aborted'}):
                prior=pre_tick[identifier];unit=staged.resources[identifier]
                movement=prior['blocked_state'] if prior['state']=='blocked' else prior['state']
                if movement=='travel' and prior['fuel'] is not None:
                    if unit['position']!=prior['position'] or unit['fuel_minutes']!=prior['fuel']-1:raise RuntimeError('abort accounting differs')
                    unit['fuel_minutes']=prior['fuel']
                    if (unit['position']==unit['base_position'] and unit['fuel_minutes']==unit['fuel_capacity_minutes']
                            and unit['water_litres']==unit['capacity_litres']):
                        staged._cancel_refill(unit);staged.air_refill_queue=[k for k in staged.air_refill_queue if k!=identifier]
                        if staged.air_refill_active==identifier:staged.air_refill_active=None
                        staged._at_base(unit,minute)
                        if staged.air_refill_active is None and staged.air_refill_queue:
                            key=staged.air_refill_queue.pop(0);next_unit=staged.resources[key]
                            if not staged._reserve_refill(next_unit):next_unit['state']='needs_assistance'
                            else:
                                staged.air_refill_active=key;next_unit.update(state='refilling',due_at=minute+staged.profile.get('air_refill_minutes',15))
            for item in drops:
                unit=staged.resources[item['resource_id']]
                if (unit['work_remaining']!=item['work_before']-1 or unit['water_litres']!=0
                        or (item['total_before'] is not None and unit.get('total_work_remaining')!=item['total_before']-1)):
                    raise RuntimeError('aircraft work costs differ')
                x,y=item['cell'];cell=host._fire._fire.cells[y][x]
                if not host._fire._is_land(x,y) or cell['heat']!=item['before'] or cell['fuel']<=0:raise RuntimeError('unsupported drop')
                cell['heat']=item['after'];cell['wet']=max(cell.get('wet',0),5)
            if not host._fire._check():raise RuntimeError('censored after aircraft drop')
            result=host._weather.step()
            host._ops=staged;host._minute=minute;host._closed=closed;host._air_until=air_until
            walk._ground=ground;walk._ground_seal=api._hash(ground)
            host._drop_audit.extend(dict(minute=minute,**item) for item in drops)
            if assignments and hold._fire.work_blocked:
                assignments=[];hold_state.update(phase='stopped',due_at=None);hold_changed=True
            expected=[dict(resource_id=walk._public_id,work_minutes=1,water_litres=0.0)] if assignments else []
            if hold._fire.costs!=expected:raise RuntimeError('hold costs differ')
            if assignments:
                if (ground['work_remaining']!=before_ground['work_remaining']-1
                        or ground['total_work_remaining']!=before_ground['total_work_remaining']-1):raise RuntimeError('ground costs differ')
                hold_state['work_minutes']+=1
            hold._hold=hold_state;hold._hold_seal=api._hash(hold_state)
            if result['status']=='censored':
                host._status='censored';walk._seal=api._hash(host.checkpoint());self._seal=api._hash(hold.checkpoint());return
            burning=host._fire.burning();t=host._inputs.contract.transform
            for key,group in staged.groups.items():
                x=math.floor((group['position'][0]-t[2])/t[0]);y=math.floor((group['position'][1]-t[5])/t[4]);cell=(x,y)
                if any(math.dist(cell,p)<=1.5 for p in burning):host._dose[key]+=2 if group['status']=='sheltering' else 10
                if list(cell) in burning or host._dose[key]>=180:group['exposed']=True
                alias=self._group_alias[key]
                if group['status']!=before_groups[key]['status']:
                    pending.append(('operations','synthetic_population_status',{'group_id':alias,'status':group['status']},{}))
                if group['exposed'] and not before_groups[key]['exposed']:
                    pending.append(('operations','synthetic_population_exposure',{'group_id':alias,'exposed':True},{}))
            hazards,front=walk._conditions(minute)
            if not walk._safe(ground['position'],ground['position'],hazards):ground['phase']='needs_assistance';ground_changed=True
            elif ground['phase'] in ('walking','front_no_work') and not walk._front_valid(front):ground['phase']='retreating';ground_changed=True
            walk._ground_seal=api._hash(ground)
            if ground_changed:pending.append(('operations','synthetic_ground_status',{'crew_id':walk._public_id,'phase':ground['phase']},{}))
            if walk_command is not None:
                accepted=(before_ground['phase']=='staged_assumed' if walk_command=='dispatch' else before_ground['phase'] in ('walking','front_no_work'))
                pending.append(('operations','synthetic_ground_command_result',{'crew_id':walk._public_id,'command':walk_command,'accepted':accepted},{}))
            safe=(ground['phase']=='front_no_work' and walk._front_valid(front) and walk._safe(ground['position'],ground['position'],hazards))
            from wildfire_containment_v1.kernel import _edge
            line=host._fire._fire._containment._lines.get(_edge(hold._edge))
            if assignments and safe:
                state='breached' if line and line['breached'] else 'holding'
                pending.append(('operations','synthetic_hold_observation',{'crew_id':walk._public_id,'status':state},{}))
                if state=='breached':hold_state.update(phase='breached',due_at=None);hold_changed=True
            if hold_state['phase'] in ('setting_up','holding') and (not safe or ground['work_remaining']<=0 or ground['total_work_remaining']<=0):
                hold_state.update(phase='stopped',due_at=None);hold_changed=True
            hold._hold=hold_state;hold._hold_seal=api._hash(hold_state)
            if hold_changed:pending.append(('operations','synthetic_hold_status',{'crew_id':walk._public_id,'phase':hold_state['phase']},{}))
            if hold_command:pending.append(('operations','synthetic_hold_command_result',{'crew_id':walk._public_id,'accepted':hold_accepted},{}))
            for item in drops:pending.append((item['resource_id'],'drop_completed',{'resource_id':item['resource_id'],'target_grid':copy.deepcopy(staged.resources[item['resource_id']].get('target_grid'))},{}))
            for key,unit in staged.resources.items():
                if before_units[key]!=_unit(unit) or minute%30==0:pending.append((key,'resource_status',{'resource':_unit(unit)},{'actionable':True}))
            self._emit_pending(bus,events,minute,public_group);self._emit_pending(bus,pending,minute)
            host._bus=bus;host._weather_reports(minute);host._incoming(minute)
            views={c:({'available':False} if closed.get(c,0)>minute else {'situation':{'observed_at':minute,'text':'Synthetic shared exercise'}}) for c in host._case['centers']}
            host._bus.tick(minute,views);host._sync()
            if result['status']=='diagnostic_end':host._status='end_unscored'
            walk._seal=api._hash(host.checkpoint());self._seal=api._hash(hold.checkpoint())
        except BaseException:
            self._poisoned=True;host._poisoned=True
            raise

    def _record(self,name,args):
        result=self.situation();self._calls.append(dict(name=name,args=copy.deepcopy(args),result=copy.deepcopy(result)))
        return result

    def advance(self,minutes=1):
        self._ensure()
        if type(minutes) is not int or not 1<=minutes<=60:raise ValueError('advance requires 1..60 minutes')
        for _ in range(minutes):
            self._step()
            if self._host._status!='active':break
        return self._record('advance',[minutes])

    def protect(self,group_id,action,destination_id=None,option_id=None):
        self._ensure()
        if group_id not in self._group_ids or action not in ('shelter','evacuate'):
            raise ValueError('invalid public population request')
        command=dict(kind=action,group_id=self._group_ids[group_id])
        if action=='shelter':
            if destination_id is not None or option_id is not None:raise ValueError('shelter takes no route')
        else:
            if destination_id not in self._destination_ids or type(option_id) is not str:
                raise ValueError('invalid public evacuation request')
            _,options=self._evacuation_options(group_id,destination_id)
            if option_id not in options:
                raise ValueError('unknown public evacuation option')
            command.update(destination_id=self._destination_ids[destination_id],option_id=options[option_id])
        self._step(population=command,public_group=group_id)
        return self._record('protect',[group_id,action,destination_id,option_id])

    def dispatch_crew(self,crew_id):
        self._ensure();self._walk._require_commandable(crew_id,('staged_assumed',))
        self._step(walk_command='dispatch');return self._record('dispatch_crew',[crew_id])

    def retreat(self,crew_id):
        self._ensure();self._walk._require_commandable(crew_id,('walking','front_no_work'))
        self._step(walk_command='retreat');return self._record('retreat',[crew_id])

    def start_hold(self,crew_id):
        self._ensure();view=self.situation()
        if (view['status']!='active' or crew_id!=self._walk._public_id
                or view['ground'][0]['phase']!='front_no_work' or view['hold']['phase']!='idle'):
            raise ValueError('hold request unavailable from delivered information')
        self._step(hold_command=True);return self._record('start_hold',[crew_id])

    def route_options(self,resource_id,target):
        self._ensure();return self._host.route_options(resource_id,target)

    def clear_aircraft(self,resource_id):
        self._ensure()
        if resource_id not in self._host._known:raise ValueError('unknown reported aircraft')
        self._step(air_command=dict(kind='clear',resource=resource_id));return self._record('clear_aircraft',[resource_id])

    def dispatch_aircraft(self,resource_id,target,option_id):
        self._ensure();options=self._host.route_options(resource_id,target)['options']
        if not any(o['id']==option_id for o in options):raise ValueError('unknown or stale flight option')
        journey=self._host._flight(self._host._known[resource_id]['position'],self._host._target_native(target))
        self._step(air_command=dict(kind='dispatch',resource=resource_id,target=copy.deepcopy(target),journey=journey))
        return self._record('dispatch_aircraft',[resource_id,copy.deepcopy(target),option_id])

    def release(self,resource_id):
        self._ensure()
        if resource_id not in self._host._known:raise ValueError('unknown reported aircraft')
        self._step(air_command=dict(kind='release',resource=resource_id));return self._record('release',[resource_id])

    def request_status(self,center_id):
        self._ensure()
        if center_id not in self._center_ids:raise ValueError('unknown public center')
        self._step(air_command=dict(kind='status',center=self._center_ids[center_id]))
        return self._record('request_status',[center_id])

    def checkpoint(self):
        self._ensure();value=dict(version=VERSION,identity=copy.deepcopy(self._identity),hold=self._hold.checkpoint(),calls=copy.deepcopy(self._calls))
        value['sha256']=api._hash(value);return value

    def restore(self,checkpoint,*,expected_checkpoint_sha256):
        try:
            value=copy.deepcopy(checkpoint)
            if (type(expected_checkpoint_sha256) is not str or len(expected_checkpoint_sha256)!=64
                    or any(c not in '0123456789abcdef' for c in expected_checkpoint_sha256)
                    or api._hash(value)!=expected_checkpoint_sha256 or type(value) is not dict
                    or set(value)!={'version','identity','hold','calls','sha256'}
                    or value['version']!=VERSION or value['identity']!=self._identity
                    or value['sha256']!=api._hash({k:v for k,v in value.items() if k!='sha256'})
                    or _source()!=self._source or type(value['calls']) is not list):
                raise ValueError('trusted population checkpoint differs')
            self._hold.restore(value['hold'],expected_checkpoint_sha256=api._hash(value['hold']))
            self._calls=value['calls'];self._seal=api._hash(self._hold.checkpoint());self._poisoned=False
            if api._encoded(self.checkpoint())!=api._encoded(value):raise ValueError('population continuation differs')
        except BaseException:self._poisoned=True;raise

    def transcript(self):
        self._ensure();return dict(version=VERSION,identity=copy.deepcopy(self._identity),calls=copy.deepcopy(self._calls))

    def replay(self,transcript,*,expected_sha256,expected_final_checkpoint_sha256):
        self._ensure()
        if self._host._minute!=0 or self._calls:raise ValueError('fresh population handover required')
        if (type(transcript) is not dict or set(transcript)!={'version','identity','calls'} or transcript['version']!=VERSION
                or transcript['identity']!=self._identity or api._hash(transcript)!=expected_sha256 or type(transcript['calls']) is not list):
            raise ValueError('trusted population transcript differs')
        allowed=('advance','protect','dispatch_crew','retreat','start_hold','clear_aircraft','dispatch_aircraft','release','request_status')
        try:
            for call in transcript['calls']:
                if (type(call) is not dict or set(call)!={'name','args','result'} or call['name'] not in allowed or type(call['args']) is not list):
                    raise ValueError('invalid population replay call')
                if api._encoded(getattr(self,call['name'])(*call['args']))!=api._encoded(call['result']):raise ValueError('population replay differs')
            if api._hash(self.checkpoint())!=expected_final_checkpoint_sha256:raise ValueError('population replay checkpoint differs')
            return self.situation()
        except BaseException:self._poisoned=True;raise


class PublicDomainPopulationV1:
    """Actor allowlist; an accidental-access guard, not a Python sandbox."""
    __slots__=('__host',)
    _allowed=frozenset(('situation','reports','evacuation_options','protect','advance',
        'dispatch_crew','retreat','start_hold','route_options','clear_aircraft',
        'dispatch_aircraft','release','request_status'))

    def __init__(self,host):
        if type(host) is not DomainPopulationGroundV1:raise ValueError('exact population host required')
        self.__host=host

    def __getattr__(self,name):
        if name not in self._allowed:raise AttributeError(name)
        return getattr(self.__host,name)

