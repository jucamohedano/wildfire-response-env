"""Trusted scoring and public facade for restricted source exercises.

This is not a sandbox. Pins, host, mask, checkpoint and audit are private.
"""
import copy
import hashlib
import math
from pathlib import Path

import adapter_v1 as api
from envs.wildfire_response.incident_day.outcomes import OutcomeCounter
from wildfire_containment_score_v1.scoring import REWARD_VERSION as ACCOUNTING_VERSION, mask_sha256, recompute
from wildfire_expanded_full_v1 import scored as accounting
from wildfire_expanded_scored_v2 import _canonical_mixed_factors, mixed_factors_sha256
from wildfire_expanded_incident_v1.mixed_v2 import MixedInputsV2, InteriorMixedFireV2, _MixedFire
_MIXED_STEP = _MixedFire.step
from wildfire_source_joint_scored_v1 import _bound_terrain_profiles, terrain_profiles_sha256, implementation_sha256 as dependencies_sha256
from wildfire_source_restricted_v1.host import RestrictedSourceIncidentV1

VERSION = 'wildfire-source-restricted-scored-v1'
REWARD_VERSION = 'wildfire-restricted-no-ground-no-evac-outcomes-v1'
SCOPE = 'restricted_no_ground_no_evac'
_ROOT = Path(__file__).resolve().parents[2]
_COMMANDS = frozenset(('advance','clear_aircraft','dispatch_aircraft','protect','release','request_status'))


def implementation_sha256():
    paths = (Path(__file__), _ROOT / 'envs/wildfire_source_restricted_v1/host.py')
    return api._hash(dict(version=VERSION, dependencies=dependencies_sha256(),
                          sources=[hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]))


def _factors(host):
    mixed, backend = host._inputs, host._fire
    if (type(mixed) is not MixedInputsV2 or type(backend) is not InteriorMixedFireV2
            or host._weather._inputs is not mixed or host._weather._fire is not backend
            or backend._inputs is not mixed.contract or backend._censor._contract is not mixed.contract
            or type(backend._fire) is not _MixedFire or _MixedFire.step is not _MIXED_STEP
            or getattr(backend._fire.step,'__func__',None) is not _MIXED_STEP):
        raise ValueError('mixed fire bindings differ')
    rows = _canonical_mixed_factors(mixed.factors)
    fuel = {(x,y):f for x,y,f,_ in rows}
    crossing = {(x,y):v for x,y,_,v in rows}
    if (backend._mixed_factors != fuel or backend._mixed_openness != crossing
            or backend._fire._mixed_openness != crossing or backend._censor._allowed != frozenset(fuel)):
        raise ValueError('effective mixed factors differ')
    from envs.wildfire_response.regional.prepared.data import FUEL_MAPPING
    for x,y,f,_ in rows:
        c=mixed.contract
        if (not 0<=x<c.width or not 0<=y<c.height or not c.domain[y][x]
                or c.surface[y][x]!='mixed' or c.fuel[y][x] not in FUEL_MAPPING
                or c.elevation[y][x] is None
                or backend._fire.cells[y][x]['initial_fuel'] != FUEL_MAPPING[c.fuel[y][x]]['initial_fuel']*f):
            raise ValueError('mixed fuel differs')
    return rows


def _static(host):
    profile=copy.deepcopy(host._ops.profile)
    for facility in profile['facilities'].values():
        facility.pop('water_litres',None)
    fire=host._fire._fire
    rules=copy.deepcopy(fire.rules)
    rules.pop('spread_factor',None)
    return dict(operations=profile, fire=dict(rules=rules, config=copy.deepcopy(fire._containment.config),
        pressure=fire._pressure_scale, spread_multiplier=fire.spread_multiplier, slope_enabled=fire.slope_enabled))


def _public_rows(host, rows):
    """Validate an independently approved public handover, never infer it."""
    if type(rows) is not list:raise ValueError('approved public population required')
    source={g['id']:g for g in host._case['groups']}
    if len(source)!=len(host._case['groups']) or len(rows)!=len(source):
        raise ValueError('public roster differs')
    seen=set()
    for row in rows:
        if (type(row) is not dict or set(row)!={'id','count','position','status','exposed','observed_at'}
                or type(row['id']) is not str or row['id'] in seen or row['id'] not in source
                or type(row['count']) is not int or row['count']<=0
                or row['exposed'] is not None or type(row['observed_at']) is not int or row['observed_at']!=0):
            raise ValueError('invalid public handover declaration')
        original=source[row['id']]
        if any(api._encoded(row[k])!=api._encoded(original[k]) for k in ('count','position','status')):
            raise ValueError('public handover differs from pinned scenario')
        seen.add(row['id'])
    return copy.deepcopy(rows)


def configuration_identity(host, scoring_mask, *, public_population):
    """Candidate for independent review, never a release approval by itself."""
    if type(host) is not RestrictedSourceIncidentV1:
        raise ValueError('exact restricted source host required')
    host._ensure()
    if host._minute != 0 or host._calls or host._status not in ('active','censored'):
        raise ValueError('fresh handover required')
    return dict(version=VERSION, host=copy.deepcopy(host._identity), handover_sha256=api._hash(host.checkpoint()),
        mask_sha256=mask_sha256(scoring_mask), public_population_sha256=api._hash(_public_rows(host,public_population)), factors_sha256=mixed_factors_sha256(_factors(host)),
        terrain_sha256=terrain_profiles_sha256(_bound_terrain_profiles()), static_sha256=api._hash(_static(host)),
        implementation_sha256=implementation_sha256(), reward_version=REWARD_VERSION)


class RestrictedSourceScoredV1:
    """Private single-driver wrapper; expose only documented actor methods."""
    def __init__(self, host, *, scoring_mask, expected_identity, public_population, opt_in=False):
        if opt_in is not True or type(host) is not RestrictedSourceIncidentV1:
            raise ValueError('explicit opt-in and exact restricted host required')
        identity=configuration_identity(host,scoring_mask,public_population=public_population)
        if type(expected_identity) is not dict or api._encoded(identity)!=api._encoded(expected_identity):
            raise ValueError('independent configuration identity differs')
        self._host=host;self._identity=copy.deepcopy(identity);self._mask=set(scoring_mask)
        self._factors=_factors(host);self._terrain=_bound_terrain_profiles()
        fire=host._fire._fire
        burnable={y*fire.width+x for y,row in enumerate(fire.cells) for x,cell in enumerate(row)
                  if host._fire._is_land(x,y) and cell['fuel']>0}
        if not self._mask<=burnable:raise ValueError('mask must contain handover burnable cells')
        t=host._inputs.contract.transform;area=abs(t[0]*t[4]-t[1]*t[3])/10000
        if not math.isfinite(area) or area<=0:raise ValueError('invalid grid cell area')
        initial=accounting._footprint(host)
        self._seen=set(initial);self._counter=OutcomeCounter(initial,self._mask,host._ops.groups,area)
        self._audit=[dict(minute=0,footprint=sorted(initial),burnable=sorted(burnable),cell_area_hectares=area,
                          new_footprint=[],population=accounting._population(host._ops))]
        self._population=_public_rows(host,public_population)
        self._initial_audit=copy.deepcopy(self._audit[0])
        self._calls=[];self._poisoned=False;self._seal=api._hash(host.checkpoint())

    def _ensure(self):
        if self._poisoned:raise RuntimeError('scorer indeterminate')
        try:
            self._host._ensure()
            if (implementation_sha256()!=self._identity['implementation_sha256']
                    or mixed_factors_sha256(_factors(self._host))!=self._identity['factors_sha256']
                    or terrain_profiles_sha256(_bound_terrain_profiles())!=self._identity['terrain_sha256']
                    or api._hash(_static(self._host))!=self._identity['static_sha256']
                    or api._hash(self._population)!=self._identity['public_population_sha256']
                    or api._hash(self._host.checkpoint())!=self._seal):
                raise RuntimeError('scorer identity or host state changed')
        except BaseException:self._poisoned=True;raise

    def situation(self):
        self._ensure();value=self._host.situation()
        if value['status']=='unsupported':value['status']='end_unscored'
        if value['status']!='censored':
            value['version']=VERSION
            value['known_fire_scope']='handover_only'
            value['population']=copy.deepcopy(self._population)
            value['scope']='restricted synthetic source exercise; no ground or evacuation; evaluator-only score'
            value['limitations']=['ground dispatch unavailable','evacuation unavailable','population observation is handover only']
        return value

    def reports(self, *, after_cursor=0, limit=20):
        self._ensure();return self._host.reports(after_cursor=after_cursor,limit=limit)

    def _tick(self):
        host=self._host
        if (mixed_factors_sha256(_factors(host))!=self._identity['factors_sha256']
                or terrain_profiles_sha256(_bound_terrain_profiles())!=self._identity['terrain_sha256']
                or api._hash(_static(host))!=self._identity['static_sha256']):
            raise RuntimeError('effective configuration changed')
        if host._minute==len(self._audit)-1 and host._status in ('censored','unsupported'):return
        if host._minute!=len(self._audit):raise RuntimeError('audit clock differs')
        new=accounting._footprint(host)-self._seen;self._seen.update(new);self._counter.update(new)
        self._audit.append(dict(minute=host._minute,new_footprint=sorted(new),population=accounting._population(host._ops)))

    def _run(self,name,args):
        self._ensure()
        prior_seen=set(self._seen);prior_counter=self._counter.checkpoint();prior_audit=len(self._audit);prior_calls=len(self._calls)
        try:
            if name not in _COMMANDS:raise ValueError('unsupported command')
            if name=='advance':
                n=args[0]
                if type(n) is not int or not 1<=n<=60:raise ValueError('advance requires 1..60 minutes')
                for _ in range(n):
                    result=self._host.advance(1);self._tick()
                    if self._host._status!='active':break
            elif name=='protect':
                group,action,destination=args
                if group not in {row['id'] for row in self._population} or action!='shelter' or destination is not None:
                    raise ValueError('only shelter for an approved public group is supported')
                result=self._host._submit_shelter(*args);self._tick()
            elif name=='dispatch_aircraft':
                options=self._host.route_options(*args)['options']
                if not options:raise ValueError('aircraft target unavailable')
                result=self._host.dispatch(*args,options[0]['id']);self._tick()
            else:
                result=getattr(self._host,name)(*args);self._tick()
            self._calls.append(dict(name=name,args=copy.deepcopy(args),result=copy.deepcopy(result)))
            self._seal=api._hash(self._host.checkpoint())
            return self.situation() if name=='advance' else result
        except BaseException as error:
            # Validation errors are recoverable only when the full trusted host
            # and all scorer accounting are provably unchanged.
            try:
                unchanged=(isinstance(error,Exception) and api._hash(self._host.checkpoint())==self._seal
                           and self._seen==prior_seen and self._counter.checkpoint()==prior_counter
                           and len(self._audit)==prior_audit and len(self._calls)==prior_calls)
            except BaseException:unchanged=False
            if not unchanged:self._poisoned=True
            raise

    def advance(self,minutes=1):return self._run('advance',[minutes])
    def clear_aircraft(self,resource_id):return self._run('clear_aircraft',[resource_id])
    def dispatch_aircraft(self,resource_id,target):return self._run('dispatch_aircraft',[resource_id,target])
    def protect(self,group_id,action,destination_id=None):return self._run('protect',[group_id,action,destination_id])
    def release(self,resource_id):return self._run('release',[resource_id])
    def request_status(self,center_id):return self._run('request_status',[center_id])
    def dispatch_hold(self,*args):raise ValueError('ground dispatch unavailable: access unverified')

    def checkpoint(self):
        self._ensure()
        value=dict(version=VERSION,identity=copy.deepcopy(self._identity),mask=sorted(self._mask),
                   host=self._host.checkpoint(),audit=copy.deepcopy(self._audit),calls=copy.deepcopy(self._calls),
                   counter=self._counter.checkpoint(),population=copy.deepcopy(self._population))
        value['sha256']=api._hash(value);return value


    def restore(self, checkpoint, *, expected_checkpoint_sha256):
        """Restore only a complete independently trusted scorer checkpoint."""
        try:
            v=copy.deepcopy(checkpoint)
            if (type(expected_checkpoint_sha256) is not str or len(expected_checkpoint_sha256)!=64
                    or any(c not in '0123456789abcdef' for c in expected_checkpoint_sha256)
                    or api._hash(v)!=expected_checkpoint_sha256 or type(v) is not dict
                    or set(v)!={'version','identity','mask','host','audit','calls','counter','population','sha256'}
                    or v['version']!=VERSION or v['identity']!=self._identity or v['mask']!=sorted(self._mask)
                    or v['population']!=self._population or v['sha256']!=api._hash({k:x for k,x in v.items() if k!='sha256'})
                    or implementation_sha256()!=self._identity['implementation_sha256']):
                raise ValueError('trusted scorer checkpoint differs')
            self._host.restore(v['host'],expected_checkpoint_sha256=api._hash(v['host']))
            host=self._host
            if (mixed_factors_sha256(_factors(host))!=self._identity['factors_sha256']
                    or terrain_profiles_sha256(_bound_terrain_profiles())!=self._identity['terrain_sha256']
                    or api._hash(_static(host))!=self._identity['static_sha256']
                    or not v['audit'] or v['audit'][0]!=self._initial_audit
                    or len(v['audit'])!=host._minute+1 or type(v['calls']) is not list):
                raise ValueError('restored scorer identity or audit differs')
            independent=recompute(v['audit'],scoring_mask=self._mask,terminal=host._status=='end_unscored',
                                  abandoned=False,censored=host._status=='censored')
            counter=OutcomeCounter.restore(v['counter'])
            outcome=counter.result(host._ops.groups,host._minute,host._status=='end_unscored',False,
                                   host._status=='censored',host._ops.orders)
            outcome['reward_version']=ACCOUNTING_VERSION
            initial=set(v['audit'][0]['footprint'])
            seen=initial | {cell for row in v['audit'] for cell in row['new_footprint']}
            if (v['audit'][-1]['population']!=accounting._population(host._ops)
                    or not accounting._footprint(host)<=seen
                    or counter.inherited!=initial or counter.eligible!=self._mask-initial
                    or counter.additional!=(seen-initial)&self._mask
                    or not accounting._same_outcome(independent,outcome)):
                raise ValueError('restored scorer accounting differs')
            self._counter,self._audit,self._calls=counter,v['audit'],v['calls']
            self._seen=seen;self._seal=api._hash(host.checkpoint());self._poisoned=False
            if api._encoded(self.checkpoint())!=api._encoded(v):raise ValueError('scorer continuation differs')
        except BaseException:self._poisoned=True;raise


def verify_outcome(env, *, fresh_host, expected_identity, scoring_mask, infrastructure_timeout=False):
    """Replay a complete day and independently recompute the private minute audit."""
    def result(status,score=None):return dict(status=status,verified=score is not None,
                                             score_eligible=score is not None,score=score,version=VERSION,scope=SCOPE,reward_version=REWARD_VERSION)
    if type(infrastructure_timeout) is not bool:raise ValueError('timeout must be boolean')
    if infrastructure_timeout:return result('timeout')
    try:
        if type(env) is not RestrictedSourceScoredV1:return result('verification_failed')
        env._ensure()
        if api._encoded(expected_identity)!=api._encoded(env._identity) or mask_sha256(scoring_mask)!=env._identity['mask_sha256']:
            return result('verification_failed')
        if env._host._status=='censored':return result('censored')
        if env._host._status!='end_unscored' or env._host._minute!=1440:return result('unfinished')
        before=env.checkpoint()
        replay=RestrictedSourceScoredV1(fresh_host(),scoring_mask=scoring_mask,expected_identity=expected_identity,
                                         public_population=env._population,opt_in=True)
        for call in env._calls:
            if call['name'] not in _COMMANDS:return result('verification_failed')
            observed=getattr(replay,call['name'])(*call['args'])
            # advance stores the host response; compare full scorer checkpoints below.
            if call['name']!='advance' and api._encoded(observed)!=api._encoded(call['result']):return result('verification_failed')
        if api._encoded(replay.checkpoint())!=api._encoded(before):return result('verification_failed')
        independent=recompute(replay._audit,scoring_mask=scoring_mask,terminal=True,abandoned=False,censored=False)
        counter=replay._counter.result(replay._host._ops.groups,1440,True,False,False,replay._host._ops.orders)
        counter['reward_version']=ACCOUNTING_VERSION
        if not independent['score_eligible'] or not accounting._same_outcome(counter,independent):return result('verification_failed')
        if api._encoded(env.checkpoint())!=api._encoded(before):return result('verification_failed')
        return result('restricted_full_day',independent['graded_score'])
    except Exception:return result('verification_failed')
