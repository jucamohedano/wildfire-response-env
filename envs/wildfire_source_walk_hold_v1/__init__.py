"""Opt-in synthetic walk-and-hold exercise; not operational or scored."""
import copy
import hashlib
import math
from pathlib import Path

import adapter_v1 as api
from wildfire_containment_v1.kernel import _edge
from wildfire_expanded_full_v1 import hold_fire as hold_fire_module
from wildfire_expanded_full_v1.hold_fire import MixedHoldFireV1
from wildfire_source_walk_exercise_v1 import SourceWalkExerciseV1

VERSION='wildfire-source-walk-hold-exercise-v1'
SCOPE='synthetic_predeployed_walk_hold_no_evacuation_unscored'
_HOLD_STEP=MixedHoldFireV1.step
_CANDIDATE=object()
_ROOT=Path(__file__).resolve().parents[2]


class _GuardedHoldFire(MixedHoldFireV1):
    """Revalidate staged work immediately before the physical fire step."""
    def __init__(self,fire,validator):
        super().__init__(fire);self._validator=validator;self.work_blocked=False

    def stage(self,assignments):
        super().stage(assignments);self.work_blocked=False

    def step(self,command=None):
        if self._pending and not self._validator():
            self._pending=[];self.work_blocked=True
        return super().step(command)


_GUARDED_STEP=_GuardedHoldFire.step


def _source():
    expected=_ROOT/'envs/wildfire_source_walk_hold_v1/__init__.py'
    if (Path(__file__).resolve()!=expected or MixedHoldFireV1 is not hold_fire_module.MixedHoldFireV1
            or MixedHoldFireV1.step is not _HOLD_STEP or _GuardedHoldFire.step is not _GUARDED_STEP):
        raise RuntimeError('hold implementation binding differs')
    return api._hash(dict(source=hashlib.sha256(expected.read_bytes()).hexdigest(),
                          hold_fire=hold_fire_module.source_sha256()))


def _setup_minutes(walk):
    n=walk._host._ops.profile.get('ground_setup_minutes')
    if type(n) is not int or not 1<=n<=60:raise ValueError('bounded pinned crew setup required')
    return n


def _install(walk,validator):
    host=walk._host
    wrapped=_GuardedHoldFire(host._fire,validator)
    host._weather._fire=host._fire=wrapped
    return wrapped


class SourceWalkHoldV1:
    """Private single-driver hold prototype, for one separately approved walk."""
    def __init__(self,walk,*,expected_identity,opt_in=False):
        if opt_in is not True or type(walk) is not SourceWalkExerciseV1:
            raise ValueError('explicit opt-in and exact source-walk host required')
        walk._ensure()
        if walk._host._minute!=0 or walk._calls or walk._host._status!='active':
            raise ValueError('fresh active walk handover required')
        edge=_edge((walk._proposal['front_cell'],walk._proposal['site_cell']))
        protected=tuple(walk._proposal['site_cell'])
        if protected not in edge or any(not walk._host._fire._is_land(x,y) for x,y in edge):
            raise ValueError('candidate front requires supported containment cells')
        self._walk=walk;self._edge=[list(p) for p in edge];self._protected=list(protected)
        self._setup=_setup_minutes(walk);self._source=_source()
        self._identity=dict(version=VERSION,scope=SCOPE,implementation=self._source,
            walk=copy.deepcopy(walk._identity),handover=api._hash(walk.checkpoint()),
            edge=copy.deepcopy(self._edge),protected_cell=copy.deepcopy(self._protected),
            setup_minutes=self._setup,authorization='explicit_synthetic_candidate_front_only',
            accounting='one_existing_walk_duty_minute_per_tick_no_extra_water')
        if expected_identity is not _CANDIDATE and (type(expected_identity) is not dict
                or api._encoded(expected_identity)!=api._encoded(self._identity)):
            raise ValueError('independently approved hold identity differs')
        self._identity_seal=api._hash(self._identity)
        self._fire=_install(walk,self._live_work_safe)
        self._hold=dict(phase='idle',due_at=None,work_minutes=0)
        self._hold_seal=api._hash(self._hold);self._calls=[];self._poisoned=False
        if api._hash(walk._host.checkpoint())!=walk._seal:raise RuntimeError('hold installation changed handover')

    def _ensure(self):
        if self._poisoned:raise RuntimeError('hold exercise indeterminate')
        try:
            self._walk._ensure()
            if (_source()!=self._source or self._identity['implementation']!=self._source
                    or api._hash(self._identity)!=self._identity_seal
                    or api._encoded(self._walk._identity)!=api._encoded(self._identity['walk'])
                    or self._edge!=self._identity['edge'] or self._protected!=self._identity['protected_cell']
                    or self._setup!=self._identity['setup_minutes']
                    or type(self._fire) is not _GuardedHoldFire or self._fire._pending is not None
                    or self._walk._host._fire is not self._fire or self._walk._host._weather._fire is not self._fire
                    or api._hash(self._hold)!=self._hold_seal or _setup_minutes(self._walk)!=self._setup):
                raise RuntimeError('hold implementation or state changed')
        except BaseException:self._poisoned=True;raise

    def situation(self):
        self._ensure();view=self._walk.situation()
        if view['status']=='censored':return view
        phase='idle';latest=None
        for report in self._walk._host._bus.reports:
            if report['kind']=='synthetic_hold_status' and report['payload'].get('crew_id')==self._walk._public_id:
                key=(report['observed_at'],report['id'])
                if latest is None or key>latest:latest=key;phase=report['payload']['phase']
        view.update(version=VERSION,scope=SCOPE,hold={'crew_id':self._walk._public_id,'phase':phase},
                    limitations=['synthetic predeployment','synthetic line-holding','no verified road return','no evacuation','unscored'])
        return view

    def reports(self,*,after_cursor=0,limit=20):
        self._ensure();return self._walk.reports(after_cursor=after_cursor,limit=limit)

    def _live_work_safe(self):
        # The host may apply an aircraft drop after _prepare. Recheck the
        # actual fire immediately before assignments enter the fire kernel.
        walk=self._walk;ground=walk._ground;minute=walk._host._minute+1
        hazards,front=walk._conditions(minute)
        return bool(ground['phase']=='front_no_work'
            and ground['work_remaining']>0 and ground['total_work_remaining']>0
            and math.dist(ground['position'],walk._proposal['site_position'])<=1e-6
            and walk._front_valid(front) and walk._safe(ground['position'],ground['position'],hazards))

    def _prepare(self,minute,walk_command,hold_command):
        walk=self._walk;g=walk._ground;h=copy.deepcopy(self._hold)
        hazards,front=walk._conditions(minute)
        projected,_=walk._ground_tick(g,minute,hazards,front,walk_command)
        safe=(g['phase']=='front_no_work' and projected['phase']=='front_no_work'
              and math.dist(g['position'],walk._proposal['site_position'])<=1e-6 and walk._front_valid(front)
              and walk._safe(g['position'],g['position'],hazards))
        accepted=None;changed=False
        if hold_command:
            accepted=bool(h['phase']=='idle' and safe)
            if accepted:h.update(phase='setting_up',due_at=minute+self._setup);changed=True
        if h['phase'] in ('setting_up','holding') and not safe:
            h.update(phase='stopped',due_at=None);changed=True
        assignments=[]
        if h['phase']=='setting_up' and minute>=h['due_at']:
            h.update(phase='holding',due_at=None);changed=True
        if h['phase']=='holding':
            line=self._fire._fire._containment._lines.get(_edge(self._edge))
            decay=self._fire._fire._containment.config['decay']
            if (line and (line['breached'] or 0<line['strength']<=decay
                    or line.get('protected_cell') not in (None,tuple(self._protected)))):
                h.update(phase='stopped',due_at=None);changed=True
            elif g['work_remaining']<=0 or g['total_work_remaining']<=0:
                h.update(phase='stopped',due_at=None);changed=True
            else:
                assignments=[dict(resource_id=walk._public_id,edge=copy.deepcopy(self._edge),
                    protected_cell=copy.deepcopy(self._protected),kind='crew',ready=True,
                    work_remaining=g['work_remaining'],water_litres=0,water_cost=0)]
        return h,assignments,accepted,changed

    def _step(self,name,args,*,walk_command=None,hold_command=False):
        self._ensure();walk=self._walk;host=walk._host;minute=host._minute+1
        before=copy.deepcopy(walk._ground);prior_hold=copy.deepcopy(self._hold)
        prior_host=walk._seal;prior_walk_calls=len(walk._calls);prior_costs=copy.deepcopy(self._fire.costs)
        try:
            hold,assignments,accepted,changed=self._prepare(minute,walk_command,hold_command)
            self._fire.stage(assignments)
            result=walk._run(name,args,command=walk_command)
            if host._minute!=minute:
                # Pre-step censor/unsupported events do not execute the staged batch.
                self._fire._pending=None
                return result
            if assignments and self._fire.work_blocked:
                assignments=[];hold.update(phase='stopped',due_at=None);changed=True
            expected=[dict(resource_id=walk._public_id,work_minutes=1,water_litres=0.0)] if assignments else []
            if self._fire.costs!=expected:raise RuntimeError('containment and crew work costs differ')
            if assignments:
                after=walk._ground
                if (after['work_remaining']!=before['work_remaining']-1
                        or after['total_work_remaining']!=before['total_work_remaining']-1):
                    raise RuntimeError('crew duty and containment costs differ')
                hold['work_minutes']+=1
            if host._status!='censored':
                hazards,front=walk._conditions(minute)
                safe=(walk._ground['phase']=='front_no_work' and walk._front_valid(front)
                      and walk._safe(walk._ground['position'],walk._ground['position'],hazards))
                line=self._fire._fire._containment._lines.get(_edge(self._edge))
                if assignments and safe:
                    state='breached' if line and line['breached'] else 'holding'
                    host._bus.emit('operations','synthetic_hold_observation',
                        {'crew_id':walk._public_id,'status':state},minute,role='responder')
                    if state=='breached':hold.update(phase='breached',due_at=None);changed=True
                if hold['phase'] in ('setting_up','holding') and (not safe
                        or walk._ground['work_remaining']<=0 or walk._ground['total_work_remaining']<=0):
                    hold.update(phase='stopped',due_at=None);changed=True
                if changed:host._bus.emit('operations','synthetic_hold_status',
                    {'crew_id':walk._public_id,'phase':hold['phase']},minute,role='responder')
                if hold_command:host._bus.emit('operations','synthetic_hold_command_result',
                    {'crew_id':walk._public_id,'accepted':accepted},minute,role='responder')
            self._hold=hold;self._hold_seal=api._hash(hold)
            walk._seal=api._hash(host.checkpoint())
            return result
        except BaseException as error:
            try:
                unchanged=(isinstance(error,Exception) and api._hash(host.checkpoint())==prior_host
                    and walk._ground==before and self._hold==prior_hold and len(walk._calls)==prior_walk_calls
                    and not walk._poisoned and not self._fire._inner._poisoned)
            except BaseException:unchanged=False
            if unchanged:
                self._fire._pending=None;self._fire.costs=prior_costs
            else:self._poisoned=True
            raise

    def _record(self,name,args,result):
        self._calls.append(dict(name=name,args=copy.deepcopy(args),result=copy.deepcopy(result)))
        return result

    def advance(self,minutes=1):
        self._ensure()
        if type(minutes) is not int or not 1<=minutes<=60:raise ValueError('advance requires 1..60 minutes')
        for _ in range(minutes):
            self._step('advance',[])
            if self._walk._host._status!='active':break
        return self._record('advance',[minutes],self.situation())

    def dispatch_crew(self,crew_id):
        self._ensure();self._walk._require_commandable(crew_id,('staged_assumed',))
        self._step('advance',[],walk_command='dispatch')
        return self._record('dispatch_crew',[crew_id],self.situation())

    def retreat(self,crew_id):
        self._ensure();self._walk._require_commandable(crew_id,('walking','front_no_work'))
        self._step('advance',[],walk_command='retreat')
        return self._record('retreat',[crew_id],self.situation())

    def start_hold(self,crew_id):
        self._ensure();view=self.situation()
        if (view.get('status')!='active' or crew_id!=self._walk._public_id
                or view['ground'][0]['phase']!='front_no_work' or view['hold']['phase']!='idle'):
            raise ValueError('hold request unavailable from delivered information')
        self._step('advance',[],hold_command=True)
        return self._record('start_hold',[crew_id],self.situation())

    def clear_aircraft(self,resource_id):
        self._ensure();result=self._step('clear_aircraft',[resource_id]);return self._record('clear_aircraft',[resource_id],result)
    def dispatch_aircraft(self,resource_id,target):
        self._ensure();result=self._step('dispatch_aircraft',[resource_id,target]);return self._record('dispatch_aircraft',[resource_id,target],result)
    def release(self,resource_id):
        self._ensure();result=self._step('release',[resource_id]);return self._record('release',[resource_id],result)
    def request_status(self,center_id):
        self._ensure();result=self._step('request_status',[center_id]);return self._record('request_status',[center_id],result)

    def checkpoint(self):
        self._ensure();value=dict(version=VERSION,identity=copy.deepcopy(self._identity),walk=self._walk.checkpoint(),
                                  hold=copy.deepcopy(self._hold),calls=copy.deepcopy(self._calls))
        value['sha256']=api._hash(value);return value

    def restore(self,checkpoint,*,expected_checkpoint_sha256):
        try:
            value=copy.deepcopy(checkpoint)
            if (type(expected_checkpoint_sha256) is not str or len(expected_checkpoint_sha256)!=64
                    or any(c not in '0123456789abcdef' for c in expected_checkpoint_sha256)
                    or api._hash(value)!=expected_checkpoint_sha256 or type(value) is not dict
                    or set(value)!={'version','identity','walk','hold','calls','sha256'}
                    or value['version']!=VERSION or value['identity']!=self._identity
                    or value['sha256']!=api._hash({k:v for k,v in value.items() if k!='sha256'})
                    or _source()!=self._source):raise ValueError('trusted hold checkpoint differs')
            h=value['hold']
            if (type(h) is not dict or set(h)!={'phase','due_at','work_minutes'}
                    or h['phase'] not in ('idle','setting_up','holding','stopped','breached')
                    or (h['phase']=='setting_up' and (type(h['due_at']) is not int or h['due_at']<1))
                    or (h['phase']!='setting_up' and h['due_at'] is not None)
                    or type(h['work_minutes']) is not int or h['work_minutes']<0 or type(value['calls']) is not list):
                raise ValueError('invalid hold checkpoint state')
            self._walk.restore(value['walk'],expected_checkpoint_sha256=api._hash(value['walk']))
            self._fire=_install(self._walk,self._live_work_safe)
            self._hold,self._calls=h,value['calls'];self._hold_seal=api._hash(h);self._poisoned=False
            if api._encoded(self.checkpoint())!=api._encoded(value):raise ValueError('hold continuation differs')
        except BaseException:self._poisoned=True;raise

    def transcript(self):
        self._ensure();return dict(version=VERSION,identity=copy.deepcopy(self._identity),calls=copy.deepcopy(self._calls))

    def replay(self,transcript,*,expected_sha256,expected_final_checkpoint_sha256):
        self._ensure()
        if self._walk._host._minute!=0 or self._calls:raise ValueError('fresh hold handover required')
        if (type(transcript) is not dict or set(transcript)!={'version','identity','calls'}
                or transcript['version']!=VERSION or transcript['identity']!=self._identity
                or api._hash(transcript)!=expected_sha256 or type(transcript['calls']) is not list):
            raise ValueError('trusted hold transcript differs')
        try:
            for call in transcript['calls']:
                if (type(call) is not dict or set(call)!={'name','args','result'} or type(call['args']) is not list
                        or call['name'] not in ('advance','dispatch_crew','retreat','start_hold','clear_aircraft',
                                                'dispatch_aircraft','release','request_status')):
                    raise ValueError('invalid hold replay call')
                if api._encoded(getattr(self,call['name'])(*call['args']))!=api._encoded(call['result']):
                    raise ValueError('hold replay response differs')
            if api._hash(self.checkpoint())!=expected_final_checkpoint_sha256:raise ValueError('hold replay checkpoint differs')
            return self.situation()
        except BaseException:self._poisoned=True;raise


def configuration_identity(walk,*,opt_in=False):
    """Produce a candidate for separate independent review, not an approval."""
    return copy.deepcopy(SourceWalkHoldV1(walk,expected_identity=_CANDIDATE,opt_in=opt_in)._identity)
