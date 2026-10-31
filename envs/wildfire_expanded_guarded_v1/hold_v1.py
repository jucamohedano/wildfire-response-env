"""Guarded synthetic ground holds; never derives work access from road geometry."""
import copy
import hashlib
import math
from pathlib import Path

import adapter_v1 as api
from envs.wildfire_response.incident_day.operations import Operations
from wildfire_containment_v1.kernel import _edge, _point
from wildfire_expanded_full_v1 import hold_fire as hold_fire_module
from wildfire_expanded_full_v1.hold_fire import MixedHoldFireV1, source_sha256 as fire_source_sha256
from . import host as host_module
from .host import GuardedIncidentV1, _group, _view

VERSION='wildfire-expanded-guarded-hold-v1'
EVIDENCE_VERSION='wildfire-guarded-synthetic-hold-sites-v1'


def _source():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class GuardedHoldV1:
    """Single-driver composition with atomic resource/fire reconciliation.

    The caller must explicitly pin independent synthetic work-site declarations.
    Neither a source road nor an accessible cell alone authorizes line work.
    """
    def __init__(self, host, work_sites, *, expected_work_sites_sha256, opt_in=False):
        if opt_in is not True or type(host) is not GuardedIncidentV1:
            raise ValueError('explicit opt-in and exact guarded host required')
        host._ensure()
        if host._minute!=0 or host._calls or host._complete:
            raise ValueError('a fresh guarded host is required')
        value=copy.deepcopy(work_sites)
        if (type(value) is not dict or set(value)!={'version','role','inputs_sha256','graph_sha256','sites'}
            or value['version']!=EVIDENCE_VERSION or value['role']!='synthetic_fixture_only'
            or value['inputs_sha256']!=host._inputs.contract.document_sha256
            or value['graph_sha256']!=host._routes.identity['graph_sha256']
            or type(value['sites']) is not list or api._hash(value)!=expected_work_sites_sha256):
            raise ValueError('work-site evidence or independently trusted pin differs')
        if value['sites'] and host._routes.identity['cell_evidence_role']!='synthetic_fixture_only':
            raise ValueError('source-unknown access cannot authorize synthetic work')
        self._host=host
        self._sites=value
        self._site_keys=set()
        for site in value['sites']:
            if (type(site) is not dict or set(site)!={'node','edge','protected_cell','kinds'}
                or type(site['node']) is not list or type(site['kinds']) is not list
                or not site['kinds'] or len(set(site['kinds']))!=len(site['kinds'])
                or any(k not in ('crew','engine') for k in site['kinds'])):
                raise ValueError('invalid synthetic work site')
            host._node(site['node'])
            if host._routes._guard.assess_origin(site['node'])['status']=='ambiguous':
                raise ValueError('ambiguous work-site node')
            edge=_edge(site['edge']);protected=_point(site['protected_cell'])
            node_cell=host._routes._guard._router._cell(site['node'])
            if protected not in edge or node_cell not in edge:
                raise ValueError('work node must occupy an edge endpoint')
            for x,y in edge:
                c=host._inputs.contract
                if not (0<=x<c.width and 0<=y<c.height and c.domain[y][x]
                    and c.surface[y][x]=='land' and c.access[y][x]=='accessible'
                    and c.fire_support[y][x] and (x,y) not in host._routes._excluded):
                    raise ValueError('work site lacks independently declared ground support')
            for kind in site['kinds']:
                key=(tuple(site['node']),edge,protected,kind)
                if key in self._site_keys: raise ValueError('duplicate work site')
                self._site_keys.add(key)
        self._source=_source()
        self._fire_source=fire_source_sha256()
        self._identity=dict(version=VERSION,source=self._source,hold_fire_source=self._fire_source,
                            host=copy.deepcopy(host._identity),work_sites=expected_work_sites_sha256)
        self._missions={}
        self._calls=[]
        self._recording=True
        self._poisoned=False
        self._install_fire()

    def _install_fire(self):
        wrapper=MixedHoldFireV1(self._host._weather._fire)
        self._host._weather._fire=self._host._fire=wrapper

    def _ensure(self):
        if self._poisoned: raise RuntimeError('guarded hold indeterminate; restore trusted checkpoint')
        self._host._ensure()
        root=Path(__file__).resolve().parents[2]
        if (Path(hold_fire_module.__file__).resolve() != (root/'envs/wildfire_expanded_full_v1/hold_fire.py').resolve()
            or MixedHoldFireV1 is not hold_fire_module.MixedHoldFireV1
            or fire_source_sha256 is not hold_fire_module.source_sha256
            or Operations is not host_module.Operations or GuardedIncidentV1 is not host_module.GuardedIncidentV1
            or _source()!=self._source or fire_source_sha256()!=self._fire_source
            or self._identity['host']!=self._host._identity or self._identity['work_sites']!=api._hash(self._sites)):
            self._poisoned=True;raise RuntimeError('guarded hold identity changed')

    def _active(self):
        self._ensure();self._host._active()

    def situation(self):
        self._ensure();value=self._host.situation();value['version']=VERSION;return value

    def reports(self, *, after_cursor=0, limit=20):
        self._ensure();return self._host.reports(after_cursor=after_cursor,limit=limit)

    def route_options(self, resource_id, destination):
        self._active();return self._host.route_options(resource_id,destination)

    def return_options(self, resource_id):
        self._active();return self._host.return_options(resource_id)

    def evacuation_options(self, group_id, destination_id):
        self._active();return self._host.evacuation_options(group_id,destination_id)

    def private_handover_exposure(self):
        self._ensure();return self._host.private_handover_exposure()

    def _record(self, name, args, kwargs, value):
        if self._recording:
            self._calls.append(dict(operation=name,args=copy.deepcopy(args),kwargs=copy.deepcopy(kwargs),result=copy.deepcopy(value)))
        return value

    def _dispatch_option(self, resource_id, destination, option_id):
        h=self._host
        if resource_id not in h._known: raise ValueError('unknown reported resource')
        unit=h._known[resource_id]
        options=h._routes.dispatch_options(unit['position'],destination,mode=unit['kind'],
            burning=h._case['initial_fire'],wind=h._case['wind'])['options']
        chosen=next((item for item in options if item['id']==option_id),None)
        if chosen is None: raise ValueError('unknown or stale guarded route')
        return copy.deepcopy(chosen['journey'])

    def dispatch_hold(self, resource_id, destination, option_id, edge, protected_cell):
        self._active()
        journey=self._dispatch_option(resource_id,destination,option_id)
        edge=_edge(edge);protected=_point(protected_cell)
        if protected not in edge: raise ValueError('protected cell must be an edge endpoint')
        mission=dict(node=list(destination),edge=[list(p) for p in edge],protected_cell=list(protected))
        self._step(dict(kind='dispatch',resource=resource_id,journey=journey,mission=mission))
        return self._record('dispatch_hold',[resource_id,copy.deepcopy(destination),option_id,
            [list(p) for p in edge],list(protected)],{},self.situation())

    def dispatch(self, resource_id, destination, option_id):
        self._active();journey=self._dispatch_option(resource_id,destination,option_id)
        self._step(dict(kind='dispatch',resource=resource_id,journey=journey))
        return self._record('dispatch',[resource_id,copy.deepcopy(destination),option_id],{},self.situation())

    def release(self, resource_id):
        self._active()
        if resource_id not in self._host._known: raise ValueError('unknown reported resource')
        self._step(dict(kind='release',resource=resource_id))
        return self._record('release',[resource_id],{},self.situation())

    def protect(self, group_id, action, *, destination_id=None, option_id=None):
        self._active();h=self._host
        if group_id not in h._groups or action not in ('shelter','evacuate'):
            raise ValueError('invalid cohort or protection action')
        journey=None
        if action=='evacuate':
            if destination_id not in h._ops.destinations: raise ValueError('unknown destination')
            options=h._routes.evacuation_options(h._groups[group_id]['position'],h._ops.destinations[destination_id]['position'],
                burning=h._case['initial_fire'],wind=h._case['wind'])['options']
            chosen=next((x for x in options if x['id']==option_id),None)
            if chosen is None: raise ValueError('unknown or stale guarded evacuation route')
            journey=copy.deepcopy(chosen['journey'])
        elif destination_id is not None or option_id is not None:
            raise ValueError('shelter takes no destination or route')
        self._step(dict(kind='protect',group=group_id,action=action,destination=destination_id,journey=journey))
        value={'status':'submitted'} if not h._censored else self.situation()
        return self._record('protect',[group_id,action],dict(destination_id=destination_id,option_id=option_id),value)

    def advance(self, minutes=1):
        self._active()
        if type(minutes) is not int or not 1<=minutes<=60: raise ValueError('advance requires 1..60 minutes')
        for _ in range(minutes):
            self._step()
            if self._host._censored or self._host._complete: break
        return self._record('advance',[minutes],{},self.situation())

    def _step(self, command=None):
        try:
            h=self._host
            if not h._fire._check():
                h._weather._status='censored';h._censored=True;return
            h._minute+=1
            ops=Operations.restore(h._ops.checkpoint())
            missions=copy.deepcopy(self._missions)
            dose=copy.deepcopy(h._dose)
            pending=[]
            def emit(source,kind,payload,**kwargs):
                # Only explicit resident updates carry population observations.
                pending.append((source,kind,copy.deepcopy(payload),copy.deepcopy(kwargs)))
            if command:
                kind=command['kind']
                if kind=='dispatch':
                    unit=ops.resources[command['resource']];journey=command['journey']
                    actual=h._routes.dispatch_options(unit['position'],journey['destination'],mode=unit['kind'],
                        burning=h._fire.burning(),wind=h._wind())['options']
                    path=[leg['edge_id'] for leg in journey['legs']]
                    safe=(h._valid_journey(journey,unit['kind']) and h._safe(unit['position'],unit['kind'])
                        and any([leg['edge_id'] for leg in item['journey']['legs']]==path for item in actual))
                    mission=command.get('mission')
                    site_supported=(mission is None or (tuple(mission['node']),_edge(mission['edge']),
                        _point(mission['protected_cell']),unit['kind']) in self._site_keys)
                    accepted=bool(safe and site_supported and ops.dispatch(unit['id'],'incident',
                        list(h._routes._guard._router._cell(journey['destination'])),journey,h._minute))
                    if accepted:
                        missions.pop(unit['id'],None)
                        if 'mission' in command: missions[unit['id']]=copy.deepcopy(command['mission'])
                    emit('operations','dispatch_result',dict(resource_id=unit['id'],accepted=accepted))
                elif kind=='release':
                    unit=ops.resources[command['resource']]
                    accepted=ops.release(unit['id'],h._return_plan(unit,h._minute),h._minute)
                    emit('operations','release_result',dict(resource_id=unit['id'],accepted=bool(accepted)))
                else:
                    accepted=True
                    try:ops.protect(command['group'],command['action'],command['destination'],command['journey'],h._minute)
                    except ValueError:accepted=False
                    emit('operations','protection_request_result',dict(group_id=command['group'],accepted=accepted))
            previous={k:u['state'] for k,u in ops.resources.items()}
            group_before={k:_group(g) for k,g in ops.groups.items()}
            assignments=[]
            total_before={}
            def work(unit):
                mission=missions.get(unit['id'])
                if mission is None:return False
                edge=_edge(mission['edge']);protected=_point(mission['protected_cell'])
                key=(tuple(mission['node']),edge,protected,unit['kind'])
                line=h._fire._fire._containment._lines.get(edge)
                decay=h._fire._fire._containment.config['decay']
                unavailable=line and (line['breached'] or 0<line['strength']<=decay
                    or (line.get('protected_cell') is not None and tuple(line['protected_cell'])!=protected))
                conflict=any(_edge(a['edge'])==edge and tuple(a['protected_cell'])!=protected for a in assignments)
                if (key not in self._site_keys or unit['position']!=mission['node'] or unavailable or conflict
                    or unit['work_remaining']<1 or (unit.get('total_work_remaining') is not None
                                                   and unit['total_work_remaining']<1)):
                    missions.pop(unit['id'],None);return False
                cost=0 if unit['kind']=='crew' else min(unit['water_litres'],ops.profile['engine_litres_per_minute'])
                if unit['kind']=='engine' and cost<=0:return False
                total_before[unit['id']]=unit.get('total_work_remaining')
                assignments.append(dict(resource_id=unit['id'],edge=copy.deepcopy(mission['edge']),
                    protected_cell=list(protected),kind=unit['kind'],ready=True,
                    work_remaining=unit['work_remaining'],water_litres=unit['water_litres'],water_cost=cost))
                return True
            ops.tick(h._minute,wind=h._wind(),daylight=True,hazard_check=h._safe,movement_check=h._movement,
                     suppress=work,emit=emit,return_planner=h._return_plan)
            h._guard_bases(ops,emit)
            h._fire.stage(assignments)
            state=h._weather.step()
            h._guard_bases(ops,emit)
            expected=[dict(resource_id=a['resource_id'],work_minutes=1,water_litres=float(a['water_cost']))
                      for a in sorted(assignments,key=lambda a:a['resource_id'])]
            if h._fire.costs!=expected: raise RuntimeError('fire and Operations costs differ')
            for item in assignments:
                unit=ops.resources[item['resource_id']]
                if (unit['work_remaining']!=item['work_remaining']-1
                    or unit['water_litres']!=item['water_litres']-item['water_cost']
                    or (total_before[item['resource_id']] is not None
                        and unit.get('total_work_remaining')!=total_before[item['resource_id']]-1)):
                    raise RuntimeError('Operations duty or water differs')
            for identifier in list(missions):
                if ops.resources[identifier]['state'] not in ('mobilizing','travel','on_scene','working','blocked'):
                    del missions[identifier]
            h._ops,self._missions=ops,missions
            if state['status']=='censored':h._censored=True;return
            for item in assignments:
                line=h._fire._fire._containment._lines[_edge(item['edge'])]
                emit(item['resource_id'],'hold_status',dict(resource_id=item['resource_id'],
                     status='breached' if line['breached'] else 'holding'),actionable=True)
            burning=h._fire.burning()
            for identifier,group in ops.groups.items():
                cell=h._routes._guard._router._cell(group['position'])
                if any(math.dist(cell,p)<=1.5 for p in burning):
                    dose[identifier]+=2 if group['status']=='sheltering' else 10
                if list(cell) in burning or dose[identifier]>=180:group['exposed']=True
                if _group(group)!=group_before[identifier]:
                    emit(identifier,'resident_update',{'group':_group(group)},role='resident',channel='radio')
            h._dose=dose
            for identifier,unit in ops.resources.items():
                if previous[identifier]!=unit['state'] or h._minute%30==0:
                    emit(identifier,'resource_status',{'resource':_view(unit)},actionable=True)
            for source,kind,payload,kwargs in pending:
                h._bus.emit(source,kind,payload,h._minute,**kwargs)
            h._emit_weather(h._minute);h._bus.tick(h._minute,{});h._sync()
            h._complete=state['status']=='diagnostic_end'
        except BaseException:
            self._poisoned=True;self._host._poisoned=True
            raise

    def checkpoint(self):
        self._ensure()
        if self._host._fire._pending is not None:raise RuntimeError('hold tick is in progress')
        value=dict(version=VERSION,identity=copy.deepcopy(self._identity),host=self._host.checkpoint(),
                   missions=copy.deepcopy(self._missions),calls=copy.deepcopy(self._calls))
        value['sha256']=api._hash(value)
        return value

    def restore(self, checkpoint):
        try:
            value=copy.deepcopy(checkpoint)
            if (type(value) is not dict or set(value)!={'version','identity','host','missions','calls','sha256'}
                or value['version']!=VERSION or api._encoded(value['identity'])!=api._encoded(self._identity)
                or _source()!=self._source or fire_source_sha256()!=self._fire_source
                or value['sha256']!=api._hash({k:v for k,v in value.items() if k!='sha256'})
                or type(value['missions']) is not dict or type(value['calls']) is not list):
                raise ValueError('guarded hold checkpoint identity or integrity differs')
            self._host.restore(value['host'])
            self._install_fire()
            for identifier,mission in value['missions'].items():
                if (identifier not in self._host._ops.resources or type(mission) is not dict
                    or set(mission)!={'node','edge','protected_cell'}):raise ValueError('invalid hold mission')
                unit=self._host._ops.resources[identifier]
                key=(tuple(mission['node']),_edge(mission['edge']),_point(mission['protected_cell']),unit['kind'])
                if (key not in self._site_keys or unit['state'] not in ('mobilizing','travel','on_scene','working','blocked')):
                    raise ValueError('hold mission is unsupported')
            self._missions,self._calls=value['missions'],value['calls']
            self._poisoned=False
            if api._encoded(self.checkpoint())!=api._encoded(value):raise ValueError('guarded hold restore differs')
        except BaseException:
            self._poisoned=True;raise

    def transcript(self):
        self._ensure()
        return dict(version=VERSION,identity_sha256=api._hash(self._identity),calls=copy.deepcopy(self._calls))

    def replay(self, transcript, *, expected_sha256, expected_identity_sha256):
        self._active()
        if self._host._minute!=0 or self._calls:raise ValueError('replay requires a fresh host')
        if (type(transcript) is not dict or set(transcript)!={'version','identity_sha256','calls'}
            or transcript['version']!=VERSION or api._hash(transcript)!=expected_sha256
            or transcript['identity_sha256']!=expected_identity_sha256
            or expected_identity_sha256!=api._hash(self._identity) or type(transcript['calls']) is not list):
            raise ValueError('transcript or trusted identity differs')
        self._recording=False
        try:
            for entry in transcript['calls']:
                if (type(entry) is not dict or set(entry)!={'operation','args','kwargs','result'}
                    or entry['operation'] not in ('dispatch','dispatch_hold','release','protect','advance')
                    or type(entry['args']) is not list or type(entry['kwargs']) is not dict):
                    raise ValueError('unsupported transcript call')
                result=getattr(self,entry['operation'])(*entry['args'],**entry['kwargs'])
                if api._encoded(result)!=api._encoded(entry['result']):raise ValueError('public replay differs')
            self._calls=copy.deepcopy(transcript['calls'])
            return self.situation()
        except BaseException:
            self._poisoned=True;raise
        finally:self._recording=True
