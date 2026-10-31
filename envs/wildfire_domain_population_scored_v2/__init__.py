"""Private 80/20 audit for the synthetic domain-ground-population exercise.

Not a qualification API or a Python sandbox. The actor must receive only the
public facade; mask, checkpoints, truth, transcript and outcome are private.
"""
import copy
import hashlib
import inspect
import math
from pathlib import Path
import adapter_v1 as api
from envs.wildfire_response.incident_day.outcomes import OutcomeCounter
from wildfire_containment_score_v1.scoring import REWARD_VERSION as ACCOUNTING, mask_sha256, recompute
from wildfire_expanded_full_v1 import scored as accounting
from wildfire_source_restricted_scored_v1 import _static
from wildfire_source_joint_scored_v1 import _bound_terrain_profiles, terrain_profiles_sha256, implementation_sha256 as dependencies
from wildfire_domain_population_host_v2 import DomainPopulationGroundV2

VERSION='wildfire-domain-population-scored-v2'
SCOPE='synthetic_single_candidate_domain_ground_population'
REWARD_VERSION='wildfire-domain-population-synthetic-80-20-v2'
_COMMANDS=frozenset(('advance','protect','dispatch_crew','retreat','start_hold','clear_aircraft','dispatch_aircraft','release','request_status'))


def _implementation():
    root=Path(__file__).resolve().parents[2]
    static_path=root/'envs/wildfire_source_restricted_scored_v1/__init__.py'
    if Path(inspect.getsourcefile(_static)).resolve()!=static_path:
        raise RuntimeError('static scorer dependency origin differs')
    return api._hash(dict(version=VERSION,source=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                         static_source=hashlib.sha256(static_path.read_bytes()).hexdigest(),dependencies=dependencies()))


def fixed_mask(owner):
    """Candidate pre-episode source-supported inventory, never a release approval."""
    if type(owner) is not DomainPopulationGroundV2:raise ValueError('exact shared owner required')
    owner._ensure();h=owner._host
    if h._minute!=0 or h._status!='active' or owner._calls:raise ValueError('fresh active handover required')
    inp=h._domain_inputs;c=inp.base.contract;fire=h._fire._fire
    return sorted(y*c.width+x for y in range(c.height) for x in range(c.width)
        if c.domain[y][x] and c.surface[y][x]=='land' and c.fire_support[y][x]
        and c.elevation[y][x] is not None and inp.source_mask[y][x] is True
        and inp.estimated_mask[y][x] is False and h._fire._is_land(x,y)
        and fire.cells[y][x]['initial_fuel']>0)


def _population(owner):
    raw=accounting._population(owner._host._ops)
    return {owner._group_alias[k]:v for k,v in raw.items()}


def configuration_identity(owner,scoring_mask):
    if type(owner) is not DomainPopulationGroundV2:raise ValueError('exact shared owner required')
    owner._ensure();h=owner._host
    if h._minute!=0 or owner._calls or h._status!='active':raise ValueError('fresh active handover required')
    if sorted(scoring_mask)!=fixed_mask(owner) or not scoring_mask:raise ValueError('fixed source-supported mask differs')
    return dict(version=VERSION,scope=SCOPE,reward_version=REWARD_VERSION,owner=copy.deepcopy(owner._identity),
        handover=api._hash(owner.checkpoint()),mask=mask_sha256(scoring_mask),public_roster=api._hash(owner._population),
        static=api._hash(_static(h)),terrain=terrain_profiles_sha256(_bound_terrain_profiles()),implementation=_implementation())


class DomainPopulationScoredV2:
    def __init__(self,owner,*,scoring_mask,expected_identity,opt_in=False):
        if opt_in is not True:raise ValueError('explicit opt-in required')
        identity=configuration_identity(owner,scoring_mask)
        if identity!=expected_identity:raise ValueError('configuration identity differs')
        self._owner=owner;self._identity=copy.deepcopy(identity);self._mask=set(scoring_mask)
        h=owner._host;t=h._inputs.contract.transform;area=abs(t[0]*t[4]-t[1]*t[3])/10000
        if not math.isfinite(area) or area<=0:raise ValueError('invalid cell area')
        fire=h._fire._fire
        burnable={y*fire.width+x for y,row in enumerate(fire.cells) for x,cell in enumerate(row)
                  if h._fire._is_land(x,y) and cell['initial_fuel']>0}
        if not self._mask<=burnable:raise ValueError('mask contains unsupported fuel')
        initial=accounting._footprint(h);self._seen=set(initial)
        self._counter=OutcomeCounter(initial,self._mask,h._ops.groups,area)
        self._audit=[dict(minute=0,footprint=sorted(initial),burnable=sorted(burnable),cell_area_hectares=area,
                          new_footprint=[],population=_population(owner))]
        self._initial_audit=copy.deepcopy(self._audit[0])
        self._calls=[];self._seal=api._hash(owner.checkpoint());self._poisoned=False

    def _ensure(self):
        if self._poisoned:raise RuntimeError('scorer indeterminate')
        try:
            self._owner._ensure();h=self._owner._host
            if (_implementation()!=self._identity['implementation'] or api._hash(self._owner.checkpoint())!=self._seal
                or api._hash(_static(h))!=self._identity['static']
                or terrain_profiles_sha256(_bound_terrain_profiles())!=self._identity['terrain']
                or api._hash(self._owner._population)!=self._identity['public_roster']
                or mask_sha256(self._mask)!=self._identity['mask']):raise RuntimeError('scoring identity changed')
        except BaseException:self._poisoned=True;raise

    @staticmethod
    def _view(value):
        value=copy.deepcopy(value);value['version']=VERSION
        if value.get('status')!='censored':
            value['scope']=SCOPE
            value['limitations']=[x for x in value.get('limitations',[]) if x!='unscored']+['private evaluator score; no source qualification']
        return value

    def situation(self):self._ensure();return self._view(self._owner.situation())
    def reports(self,*,after_cursor=0,limit=20):self._ensure();return self._owner.reports(after_cursor=after_cursor,limit=limit)
    def evacuation_options(self,*args):self._ensure();return self._owner.evacuation_options(*args)
    def route_options(self,*args):self._ensure();return self._owner.route_options(*args)

    def _tick(self):
        h=self._owner._host
        if h._minute==len(self._audit)-1 and h._status in ('unsupported','censored'):return
        if h._minute!=len(self._audit):raise RuntimeError('scoring clock differs')
        new=accounting._footprint(h)-self._seen;self._seen.update(new);self._counter.update(new)
        self._audit.append(dict(minute=h._minute,new_footprint=sorted(new),population=_population(self._owner)))

    def _run(self,name,args):
        self._ensure();old=self._seal;prior=api._hash(dict(audit=self._audit,calls=self._calls,counter=self._counter.checkpoint(),seen=sorted(self._seen)))
        try:
            if name not in _COMMANDS:raise ValueError('unsupported command')
            if name=='advance':
                n=args[0]
                if type(n) is not int or not 1<=n<=60:raise ValueError('advance requires 1..60 minutes')
                for _ in range(n):
                    result=self._owner.advance(1);self._tick()
                    if self._owner._host._status!='active':break
            else:result=getattr(self._owner,name)(*args);self._tick()
            result=self._view(result)
            self._calls.append(dict(name=name,args=copy.deepcopy(args),result=copy.deepcopy(result)))
            self._seal=api._hash(self._owner.checkpoint());return result
        except BaseException:
            try:unchanged=(api._hash(self._owner.checkpoint())==old and prior==api._hash(dict(audit=self._audit,calls=self._calls,counter=self._counter.checkpoint(),seen=sorted(self._seen))))
            except BaseException:unchanged=False
            if not unchanged:self._poisoned=True
            raise

    def advance(self,minutes=1):return self._run('advance',[minutes])
    def protect(self,group_id,action,destination_id=None,option_id=None):return self._run('protect',[group_id,action,destination_id,option_id])
    def dispatch_crew(self,crew_id):return self._run('dispatch_crew',[crew_id])
    def retreat(self,crew_id):return self._run('retreat',[crew_id])
    def start_hold(self,crew_id):return self._run('start_hold',[crew_id])
    def clear_aircraft(self,resource_id):return self._run('clear_aircraft',[resource_id])
    def dispatch_aircraft(self,resource_id,target,option_id):return self._run('dispatch_aircraft',[resource_id,target,option_id])
    def release(self,resource_id):return self._run('release',[resource_id])
    def request_status(self,center_id):return self._run('request_status',[center_id])

    def checkpoint(self):
        self._ensure();v=dict(version=VERSION,identity=copy.deepcopy(self._identity),owner=self._owner.checkpoint(),
            mask=sorted(self._mask),audit=copy.deepcopy(self._audit),counter=self._counter.checkpoint(),calls=copy.deepcopy(self._calls))
        v['sha256']=api._hash(v);return v

    def restore(self,checkpoint,*,expected_checkpoint_sha256):
        try:
            v=copy.deepcopy(checkpoint)
            if (type(expected_checkpoint_sha256) is not str or len(expected_checkpoint_sha256)!=64
                or any(c not in '0123456789abcdef' for c in expected_checkpoint_sha256)
                or api._hash(v)!=expected_checkpoint_sha256 or type(v) is not dict
                or set(v)!={'version','identity','owner','mask','audit','counter','calls','sha256'}
                or v['version']!=VERSION or v['identity']!=self._identity or v['mask']!=sorted(self._mask)
                or v['sha256']!=api._hash({k:x for k,x in v.items() if k!='sha256'})
                or _implementation()!=self._identity['implementation'] or type(v['calls']) is not list):
                raise ValueError('trusted scoring checkpoint differs')
            self._owner.restore(v['owner'],expected_checkpoint_sha256=api._hash(v['owner']))
            h=self._owner._host
            if (not v['audit'] or v['audit'][0]!=self._initial_audit or len(v['audit'])!=h._minute+1
                or api._hash(_static(h))!=self._identity['static']
                or terrain_profiles_sha256(_bound_terrain_profiles())!=self._identity['terrain']):
                raise ValueError('restored scoring identity differs')
            independent=recompute(v['audit'],scoring_mask=self._mask,terminal=h._status=='end_unscored',
                                  abandoned=False,censored=h._status=='censored')
            counter=OutcomeCounter.restore(v['counter'])
            outcome=counter.result(h._ops.groups,h._minute,h._status=='end_unscored',False,h._status=='censored',h._ops.orders)
            outcome['reward_version']=ACCOUNTING
            initial=set(v['audit'][0]['footprint']);seen=initial|{c for row in v['audit'] for c in row['new_footprint']}
            if (v['audit'][-1]['population']!=_population(self._owner) or not accounting._footprint(h)<=seen
                or counter.inherited!=initial or counter.eligible!=self._mask-initial
                or counter.additional!=(seen-initial)&self._mask or not accounting._same_outcome(independent,outcome)):
                raise ValueError('restored scoring audit differs')
            self._counter=counter;self._audit=v['audit'];self._calls=v['calls'];self._seen=seen
            self._seal=api._hash(self._owner.checkpoint());self._poisoned=False
            if api._encoded(self.checkpoint())!=api._encoded(v):raise ValueError('scoring continuation differs')
        except BaseException:self._poisoned=True;raise


def verify_outcome(env,*,fresh_owner,expected_identity,scoring_mask):
    """Replay and independently recompute; returns only an evaluator diagnostic."""
    def result(status,score=None):return dict(status=status,verified=score is not None,score_eligible=score is not None,
        score=score,version=VERSION,scope=SCOPE,reward_version=REWARD_VERSION)
    try:
        if type(env) is not DomainPopulationScoredV2:return result('verification_failed')
        env._ensure();h=env._owner._host
        if expected_identity!=env._identity or mask_sha256(scoring_mask)!=env._identity['mask']:return result('verification_failed')
        if h._status=='censored':return result('censored')
        if h._status!='end_unscored' or h._minute!=1440:return result('unfinished')
        before=env.checkpoint();clone=DomainPopulationScoredV2(fresh_owner(),scoring_mask=scoring_mask,expected_identity=expected_identity,opt_in=True)
        for call in env._calls:
            if call['name'] not in _COMMANDS or getattr(clone,call['name'])(*call['args'])!=call['result']:return result('verification_failed')
        if api._encoded(clone.checkpoint())!=api._encoded(before):return result('verification_failed')
        independent=recompute(clone._audit,scoring_mask=scoring_mask,terminal=True,abandoned=False,censored=False)
        counter=clone._counter.result(clone._owner._host._ops.groups,1440,True,False,False,clone._owner._host._ops.orders)
        counter['reward_version']=ACCOUNTING
        if not independent['score_eligible'] or not accounting._same_outcome(counter,independent):return result('verification_failed')
        if api._encoded(env.checkpoint())!=api._encoded(before):return result('verification_failed')
        return result('synthetic_domain_population_v2_full_day',independent['graded_score'])
    except Exception:return result('verification_failed')


class PublicDomainPopulationScoredV2:
    """Actor allowlist, not an in-process sandbox or an outcome API."""
    __slots__=('__owner',)
    _allowed=_COMMANDS|frozenset(('situation','reports','evacuation_options','route_options'))
    def __init__(self,owner):
        if type(owner) is not DomainPopulationScoredV2:raise ValueError('exact scored owner required')
        self.__owner=owner
    def __getattr__(self,name):
        if name not in self._allowed:raise AttributeError(name)
        return getattr(self.__owner,name)
