"""Private 80/20 accounting for the exact shared synthetic joint host.

Expose only the documented actor methods. The host, factory, pins, audit,
checkpoints, transcript and verifier belong to a trusted evaluator.
"""
import copy
import hashlib
import inspect
import math
from pathlib import Path

import adapter_v1 as api
from envs.wildfire_response.incident_day.outcomes import OutcomeCounter
from wildfire_containment_score_v1.scoring import REWARD_VERSION, mask_sha256, recompute
from simulator import terrain as terrain_module
from wildfire_expanded_incident_v1 import mixed_v2 as mixed_module
from wildfire_expanded_full_v1 import hold_fire as hold_fire_module
from wildfire_expanded_full_v1 import scored as accounting
from wildfire_expanded_scored_v2 import (
    implementation_sha256 as expanded_source_sha256,
    mixed_factors_sha256, _effective_mixed_factors, _canonical_mixed_factors,
)
from wildfire_source_joint_v1.fixture_host import SharedSyntheticIncidentV1

VERSION = 'wildfire-source-joint-synthetic-scored-v1'
HANDOVER_VERSION = 'wildfire-source-joint-synthetic-handover-exposure-v1'
TERRAIN_PROFILES_VERSION = 'wildfire-source-joint-terrain-profiles-v1'
_MIXED_STEP = mixed_module._MixedFire.step
_ROOT = Path(__file__).resolve().parents[2]
_ACTOR_COMMANDS = frozenset(('dispatch_hold','clear_aircraft','dispatch_aircraft',
                             'protect','release','request_status','advance'))


def implementation_sha256():
    """Pin the joint and scorer sources together with their existing dependencies."""
    host_path = _ROOT / 'envs/wildfire_source_joint_v1/fixture_host.py'
    scorer_path = _ROOT / 'envs/wildfire_source_joint_scored_v1/__init__.py'
    if (Path(inspect.getfile(SharedSyntheticIncidentV1)).resolve() != host_path
            or Path(__file__).resolve() != scorer_path):
        raise RuntimeError('joint host or scorer loaded from an unexpected path')
    joint = _ROOT / 'envs/wildfire_source_joint_v1'
    guarded = _ROOT / 'envs/wildfire_expanded_guarded_v1'
    files = list(joint.rglob('*.py')) + list(guarded.rglob('*.py')) + [scorer_path]
    return api._hash(dict(version=VERSION, expanded=expanded_source_sha256(),
        sources={str(p.relative_to(_ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}))


def _handover_rows(groups):
    if type(groups) is not list:
        raise ValueError('explicit handover groups required')
    result, seen = [], set()
    for group in groups:
        if type(group) is not dict:
            raise ValueError('invalid handover group')
        identifier, count = group.get('id'), group.get('count')
        exposed, disclosed = group.get('initial_exposed'), group.get('handover_exposure')
        if (type(identifier) is not str or not identifier or identifier in seen
                or type(count) is not int or count <= 0 or type(exposed) is not bool
                or 'handover_exposure' not in group
                or (disclosed is not None and (type(disclosed) is not bool or disclosed != exposed))):
            raise ValueError('invalid private or disclosed handover exposure')
        seen.add(identifier)
        result.append(dict(id=identifier,count=count,initial_exposed=exposed,handover_exposure=disclosed))
    return sorted(result, key=lambda item:item['id'])


def handover_exposure_sha256(groups):
    """Hash independently approved cohort counts and handover exposure inputs."""
    return api._hash(dict(version=HANDOVER_VERSION,groups=_handover_rows(groups)))


def _initial_counts(host):
    rows = _handover_rows(host._fixture['groups'])
    actual = host._ops.groups
    if set(actual) != {row['id'] for row in rows}:
        raise ValueError('handover population roster differs')
    for row in rows:
        k = row['id']; group = actual[k]
        if (group['count'] != row['count'] or type(group['exposed']) is not bool
                or group['exposed'] != row['initial_exposed']
                or host._initial_exposure.get(k) is not row['initial_exposed']
                or host._handover_exposure.get(k) is not row['handover_exposure']):
            raise ValueError('operational and fixture handover exposure differ')
    return {row['id']:row['count'] if row['initial_exposed'] else 0 for row in rows}


def _operations_configuration(host):
    profile = copy.deepcopy(host._ops.profile)
    if type(profile) is not dict or type(profile.get('facilities')) is not dict:
        raise ValueError('invalid joint operations configuration')
    for facility in profile['facilities'].values():
        if (type(facility) is not dict or 'water_litres' not in facility
                or type(facility['water_litres']) not in (int,float)
                or not math.isfinite(facility['water_litres']) or facility['water_litres'] < 0):
            raise ValueError('invalid joint facility stock')
        # Stock changes during refill; the initial amounts are in the pinned
        # fixture and handover checkpoint, not in this static configuration.
        del facility['water_litres']
    return profile


def _routing_configuration(host):
    router, network = host._router, host._network
    if (router.network is not network or host._inputs is not host._weather._inputs
            or router.allowed_edge_ids != host._allowed):
        raise ValueError('joint routing or input bindings differ')
    return dict(network_document=copy.deepcopy(network.document),nodes=copy.deepcopy(network.nodes),
        edges=copy.deepcopy(network.edges),adjacency=copy.deepcopy(network.adjacency),
        main_component=sorted(network.main_component),allowed=sorted(host._allowed),
        excluded=[list(point) for point in sorted(host._excluded)],
        sites=copy.deepcopy(host._sites),
        nodes_by_position=[[list(position),sorted(ids)] for position,ids in sorted(host._nodes.items())],
        router_inputs=copy.deepcopy(router.inputs),heights=copy.deepcopy(router.heights),
        walk_mask=copy.deepcopy(router.walk_mask),width=router.width,height=router.height,
        transform=copy.deepcopy(router.transform),estimated_land=[list(p) for p in sorted(router._estimated_land)],
        origin_tiles=[[list(key),copy.deepcopy(value)] for key,value in sorted(router._origin_tiles.items())],
        origin_wide=copy.deepcopy(router._origin_wide))


def _canonical_terrain_profiles(profiles):
    if type(profiles) is not dict or not profiles:
        raise ValueError('terrain profiles must be a nonempty mapping')
    canonical = {}
    for name, profile in profiles.items():
        if (type(name) is not str or not name or type(profile) is not dict
                or set(profile) != {'fuel','spread','duration'}):
            raise ValueError('invalid terrain profile')
        values = {}
        for key in ('fuel','spread','duration'):
            value = profile[key]
            if type(value) not in (int,float):
                raise ValueError('terrain profile values must be finite numbers')
            try:
                value = float(value)
            except (ValueError,OverflowError) as error:
                raise ValueError('terrain profile values must be finite numbers') from error
            if not math.isfinite(value) or (value <= 0 if key == 'duration' else value < 0):
                raise ValueError('invalid terrain profile value')
            values[key] = value
        canonical[name] = values
    return {key:canonical[key] for key in sorted(canonical)}


def terrain_profiles_sha256(profiles):
    """Hash independently approved canonical terrain profiles."""
    return api._hash(dict(version=TERRAIN_PROFILES_VERSION,profiles=_canonical_terrain_profiles(profiles)))


def _bound_terrain_profiles():
    if (Path(mixed_module.__file__).resolve() != _ROOT / 'envs/wildfire_expanded_incident_v1/mixed_v2.py'
            or Path(terrain_module.__file__).resolve() != _ROOT / 'envs/wildfire_response/core/src/simulator/terrain.py'
            or mixed_module._MixedFire.step is not _MIXED_STEP
            or hold_fire_module._MIXED_STEP is not _MIXED_STEP
            or _MIXED_STEP.__globals__ is not mixed_module.__dict__
            or _MIXED_STEP.__globals__.get('PROFILES') is not terrain_module.PROFILES):
        raise RuntimeError('mixed-fire terrain profile binding differs')
    return _canonical_terrain_profiles(terrain_module.PROFILES)


def _effective_terrain_profiles(host):
    fire = host._fire._fire
    if (type(fire) is not mixed_module._MixedFire
            or getattr(fire.step,'__func__',None) is not _MIXED_STEP):
        raise RuntimeError('live mixed-fire step binding differs')
    return _bound_terrain_profiles()


def _fire_configuration(host):
    fire = host._fire._fire
    rules = copy.deepcopy(fire.rules)
    if type(rules) is not dict or 'spread_factor' not in rules:
        raise ValueError('invalid joint fire configuration')
    # Weather changes spread_factor over time; the pinned weather identity,
    # handover checkpoint and replay cover that state.
    del rules['spread_factor']
    config = copy.deepcopy(fire._containment.config)
    if (api._encoded(config) != api._encoded(host._weather._identity['fire']['config'])
            or fire._pressure_scale != host._weather._identity['fire']['pressure_scale']):
        raise ValueError('joint fire configuration differs from the host identity')
    return dict(containment_config=config,pressure_scale=fire._pressure_scale,rules=rules,
                spread_multiplier=fire.spread_multiplier,slope_enabled=fire.slope_enabled)


def _identity(host, scoring_mask, handover_state_sha256):
    factors = _effective_mixed_factors(host)
    profiles = _effective_terrain_profiles(host)
    return dict(version=VERSION, host=copy.deepcopy(host._identity),
        handover_state_sha256=handover_state_sha256,
        handover_exposure_sha256=handover_exposure_sha256(host._fixture['groups']),
        mixed_factors_sha256=mixed_factors_sha256(factors),
        terrain_profiles_sha256=terrain_profiles_sha256(profiles),
        operations_configuration_sha256=api._hash(_operations_configuration(host)),
        routing_configuration_sha256=api._hash(_routing_configuration(host)),
        fire_configuration_sha256=api._hash(_fire_configuration(host)),
        scoring_mask_sha256=mask_sha256(scoring_mask),
        implementation_sha256=implementation_sha256(), reward_version=REWARD_VERSION)


def configuration_identity(host, scoring_mask):
    """Produce a fresh-handover candidate for independent review before a run."""
    if type(host) is not SharedSyntheticIncidentV1:
        raise ValueError('exact shared synthetic joint host required')
    host._ensure()
    if host._minute != 0 or host._calls or host._status not in ('active','censored'):
        raise ValueError('fresh joint handover required')
    return _identity(host,scoring_mask,api._hash(host.checkpoint()))


class SourceJointScoredV1:
    """A single trusted driver; all scoring methods are private to the runner."""
    def __init__(self, host, *, scoring_mask, expected_identity, opt_in=False):
        if opt_in is not True or type(host) is not SharedSyntheticIncidentV1:
            raise ValueError('explicit opt-in and exact shared synthetic host required')
        host._ensure()
        if host._minute != 0 or host._calls or host._status not in ('active','censored'):
            raise ValueError('fresh joint handover required')
        actual = configuration_identity(host,scoring_mask)
        if type(expected_identity) is not dict or api._encoded(actual) != api._encoded(expected_identity):
            raise ValueError('independent joint configuration identity differs')
        mask = set(scoring_mask)
        fire = host._fire._fire
        burnable = {y*fire.width+x for y,row in enumerate(fire.cells) for x,cell in enumerate(row)
                    if host._fire._is_land(x,y) and cell['fuel'] > 0}
        if not mask <= burnable:
            raise ValueError('mask must contain only handover burnable cells')
        t = host._inputs.contract.transform
        area = abs(t[0]*t[4]-t[1]*t[3])/10000
        if not math.isfinite(area) or area <= 0:
            raise ValueError('invalid projected grid cell area')
        handover = _initial_counts(host)
        initial = accounting._population(host._ops)
        if {k:g['exposed'] for k,g in initial.items()} != handover:
            raise ValueError('handover audit exposure differs')
        self._host = host
        self._identity = copy.deepcopy(actual)
        self._mask = mask
        self._mixed = _effective_mixed_factors(host)
        self._terrain = _effective_terrain_profiles(host)
        if terrain_profiles_sha256(self._terrain) != actual['terrain_profiles_sha256']:
            raise ValueError('trusted terrain profile pin differs')
        self._handover = handover
        self._initial = accounting._footprint(host)
        self._seen = set(self._initial)
        self._counter = OutcomeCounter(self._initial,mask,host._ops.groups,area)
        if self._counter.initial_exposure != sum(handover.values()):
            raise ValueError('handover counter exposure differs')
        self._audit = [dict(minute=0,footprint=sorted(self._initial),burnable=sorted(burnable),
                            cell_area_hectares=area,new_footprint=[],population=initial)]
        self._calls = []
        self._poisoned = False
        self._seal = api._hash(host.checkpoint())

    def _ensure(self):
        if self._poisoned:
            raise RuntimeError('joint scorer indeterminate; restore a trusted checkpoint')
        try:
            self._host._ensure()
            if (mixed_factors_sha256(self._mixed) != self._identity['mixed_factors_sha256']
                    or terrain_profiles_sha256(self._terrain) != self._identity['terrain_profiles_sha256']
                    or api._encoded(_effective_terrain_profiles(self._host)) != api._encoded(self._terrain)
                    or api._encoded(_identity(self._host,sorted(self._mask),self._identity['handover_state_sha256'])) != api._encoded(self._identity)
                    or api._encoded(_effective_mixed_factors(self._host)) != api._encoded(self._mixed)
                    or api._hash(self._host.checkpoint()) != self._seal):
                raise RuntimeError('joint scorer identity or host state changed')
        except BaseException:
            self._poisoned = True
            raise

    def situation(self):
        self._ensure()
        return self._host.situation()

    def reports(self, *, after_cursor=0, limit=20):
        self._ensure()
        return self._host.reports(after_cursor=after_cursor,limit=limit)

    def _record_tick(self):
        host = self._host
        if (api._encoded(_effective_mixed_factors(host)) != api._encoded(self._mixed)
                or api._encoded(_effective_terrain_profiles(host)) != api._encoded(self._terrain)
                or api._hash(_operations_configuration(host)) != self._identity['operations_configuration_sha256']
                or api._hash(_routing_configuration(host)) != self._identity['routing_configuration_sha256']
                or api._hash(_fire_configuration(host)) != self._identity['fire_configuration_sha256']):
            raise RuntimeError('effective factors or configuration changed during a tick')
        minute = host._minute
        if minute == len(self._audit)-1 and host._status == 'censored':
            return
        if minute != len(self._audit):
            raise RuntimeError('joint scorer and host clocks differ')
        new = accounting._footprint(host) - self._seen
        self._seen.update(new)
        self._counter.update(new)
        self._audit.append(dict(minute=minute,new_footprint=sorted(new),
                                population=accounting._population(host._ops)))

    def _run(self, name, args):
        self._ensure()
        try:
            if name not in _ACTOR_COMMANDS:
                raise ValueError('unknown actor operation')
            if name == 'advance':
                minutes = args[0]
                if type(minutes) is not int or not 1 <= minutes <= 60:
                    raise ValueError('advance requires 1..60 minutes')
                for _ in range(minutes):
                    result = self._host.advance(1)
                    self._record_tick()
                    if self._host._status != 'active':
                        break
            else:
                result = getattr(self._host,name)(*args)
                self._record_tick()
            self._calls.append(dict(name=name,args=copy.deepcopy(args),result=copy.deepcopy(result)))
            self._seal = api._hash(self._host.checkpoint())
            return result
        except BaseException:
            self._poisoned = True
            raise

    def dispatch_hold(self, resource_id, site_id):
        return self._run('dispatch_hold',[resource_id,site_id])

    def clear_aircraft(self, resource_id):
        return self._run('clear_aircraft',[resource_id])

    def dispatch_aircraft(self, resource_id, target):
        return self._run('dispatch_aircraft',[resource_id,target])

    def protect(self, group_id, action, destination_id=None):
        return self._run('protect',[group_id,action,destination_id])

    def release(self, resource_id):
        return self._run('release',[resource_id])

    def request_status(self, center_id):
        return self._run('request_status',[center_id])

    def advance(self, minutes=1):
        return self._run('advance',[minutes])

    def _outcome(self):
        host = self._host
        value = self._counter.result(host._ops.groups,host._minute,host._status == 'end_unscored',
                                     False,host._status == 'censored',host._ops.orders)
        value['reward_version'] = REWARD_VERSION
        return value

    def checkpoint(self):
        self._ensure()
        value = dict(version=VERSION,identity=copy.deepcopy(self._identity),mask=sorted(self._mask),
            mixed_factors=copy.deepcopy(self._mixed),terrain_profiles=copy.deepcopy(self._terrain),host=self._host.checkpoint(),
            counter=self._counter.checkpoint(),audit=copy.deepcopy(self._audit),calls=copy.deepcopy(self._calls))
        value['sha256'] = api._hash(value)
        return value

    def restore(self, checkpoint, *, expected_checkpoint_sha256):
        try:
            v = copy.deepcopy(checkpoint)
            if (type(expected_checkpoint_sha256) is not str or len(expected_checkpoint_sha256) != 64
                    or any(c not in '0123456789abcdef' for c in expected_checkpoint_sha256)
                    or api._hash(v) != expected_checkpoint_sha256
                    or type(v) is not dict or set(v) != {'version','identity','mask','mixed_factors','terrain_profiles','host','counter','audit','calls','sha256'}
                    or v['version'] != VERSION or api._encoded(v['identity']) != api._encoded(self._identity)
                    or v['mask'] != sorted(self._mask) or api._encoded(v['mixed_factors']) != api._encoded(self._mixed)
                    or api._encoded(v['terrain_profiles']) != api._encoded(self._terrain)
                    or terrain_profiles_sha256(self._terrain) != self._identity['terrain_profiles_sha256']
                    or api._encoded(_bound_terrain_profiles()) != api._encoded(self._terrain)
                    or mixed_factors_sha256(self._mixed) != self._identity['mixed_factors_sha256']
                    or _canonical_mixed_factors(self._host._weather._inputs.factors) != self._mixed
                    or v['sha256'] != api._hash({k:val for k,val in v.items() if k != 'sha256'})
                    or implementation_sha256() != self._identity['implementation_sha256']
                    or api._encoded(self._host._identity) != api._encoded(self._identity['host'])):
                raise ValueError('joint scorer checkpoint identity or integrity differs')
            self._host.restore(v['host'],expected_checkpoint_sha256=api._hash(v['host']))
            host = self._host
            if (api._encoded(_effective_mixed_factors(host)) != api._encoded(self._mixed)
                or api._encoded(_effective_terrain_profiles(host)) != api._encoded(self._terrain)
                    or api._encoded(_identity(host,sorted(self._mask),self._identity['handover_state_sha256'])) != api._encoded(self._identity)):
                raise ValueError('restored factors or configuration differ')
            independent = recompute(v['audit'],scoring_mask=self._mask,
                terminal=host._status == 'end_unscored',abandoned=False,censored=host._status == 'censored')
            counter = OutcomeCounter.restore(v['counter'])
            self._counter,self._audit,self._calls = counter,v['audit'],v['calls']
            self._initial = set(self._audit[0]['footprint'])
            self._seen = self._initial | {c for row in self._audit for c in row['new_footprint']}
            initial = {k:g['exposed'] for k,g in self._audit[0]['population'].items()}
            if (type(self._calls) is not list or len(self._audit) != host._minute+1
                    or initial != self._handover or self._counter.initial_exposure != sum(self._handover.values())
                    or handover_exposure_sha256(host._fixture['groups']) != self._identity['handover_exposure_sha256']
                    or not accounting._footprint(host) <= self._seen
                    or self._audit[-1]['population'] != accounting._population(host._ops)
                    or not accounting._same_outcome(self._outcome(),independent)
                    or self._counter.inherited != self._initial or self._counter.eligible != self._mask-self._initial
                    or self._counter.additional != (self._seen-self._initial) & self._mask):
                raise ValueError('joint scorer checkpoint accounting differs')
            self._seal = api._hash(host.checkpoint())
            self._poisoned = False
            if api._encoded(self.checkpoint()) != api._encoded(v):
                raise ValueError('joint scorer checkpoint continuation differs')
        except BaseException:
            self._poisoned = True
            raise


def verify_outcome(env, *, fresh_host, expected_identity, scoring_mask, infrastructure_timeout=False):
    """Verify only completed uncensored days through a full fresh joint replay."""
    def result(status, verified=False, score=None):
        return dict(version=VERSION,status=status,verified=verified,score_eligible=score is not None,score=score)
    if type(infrastructure_timeout) is not bool:
        raise ValueError('timeout flag must be boolean')
    if infrastructure_timeout:
        return result('timeout')
    try:
        if type(env) is not SourceJointScoredV1 or type(expected_identity) is not dict:
            return result('verification_failed')
        env._ensure()
        if (api._encoded(expected_identity) != api._encoded(env._identity)
                or mask_sha256(scoring_mask) != env._identity['scoring_mask_sha256']):
            return result('verification_failed')
        if env._host._status == 'censored':
            return result('censored')
        if env._host._status != 'end_unscored' or env._host._minute != 1440:
            return result('unfinished')
        before = env.checkpoint()
        replay = SourceJointScoredV1(fresh_host(),scoring_mask=scoring_mask,
                                     expected_identity=expected_identity,opt_in=True)
        for call in env._calls:
            if call['name'] not in _ACTOR_COMMANDS:
                return result('verification_failed')
            observed = getattr(replay,call['name'])(*call['args'])
            if api._encoded(observed) != api._encoded(call['result']):
                return result('verification_failed')
        if api._encoded(replay.checkpoint()) != api._encoded(before):
            return result('verification_failed')
        independent = recompute(replay._audit,scoring_mask=scoring_mask,terminal=True,abandoned=False,censored=False)
        if (not independent['score_eligible'] or not accounting._same_outcome(replay._outcome(),independent)
                or not accounting._same_outcome(env._outcome(),independent)
                or api._encoded(env.checkpoint()) != api._encoded(before)):
            return result('verification_failed')
        return result('full_day',True,independent['graded_score'])
    except Exception:
        return result('verification_failed')
