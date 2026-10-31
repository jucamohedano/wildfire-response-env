"""Private scoring over the exact synthetic population v2 composition.

Only the documented actor calls may be exposed. The Python objects, factory,
identity, mask, audit, checkpoints and verifier are trusted evaluator data.
"""
import copy
import hashlib
import inspect
import math
from pathlib import Path

import adapter_v1 as api
from envs.wildfire_response.incident_day.outcomes import OutcomeCounter
from envs.wildfire_response.regional.prepared.data import FUEL_MAPPING
from wildfire_containment_score_v1.scoring import REWARD_VERSION, mask_sha256, recompute
from wildfire_expanded_full_v1 import scored as base
from wildfire_expanded_full_v1.hold_fire import MixedHoldFireV1
from wildfire_expanded_incident_v1.mixed_v2 import MixedInputsV2, InteriorMixedFireV2, _MixedFire
from wildfire_expanded_population_v2 import ExpandedPopulationV2

VERSION = 'wildfire-expanded-scored-v2'
MIXED_FACTORS_VERSION = 'wildfire-expanded-effective-mixed-factors-v1'
_ROOT = Path(__file__).resolve().parents[2]


def implementation_sha256():
    """Bind the separately versioned scorer and population to the base sources."""
    population_path = _ROOT / 'envs/wildfire_expanded_population_v2/__init__.py'
    scorer_path = _ROOT / 'envs/wildfire_expanded_scored_v2/__init__.py'
    if (Path(inspect.getfile(ExpandedPopulationV2)).resolve() != population_path
            or Path(inspect.getfile(MixedInputsV2)).resolve() != _ROOT / 'envs/wildfire_expanded_incident_v1/mixed_v2.py'
            or Path(inspect.getfile(MixedHoldFireV1)).resolve() != _ROOT / 'envs/wildfire_expanded_full_v1/hold_fire.py'
            or Path(base.__file__).resolve() != _ROOT / 'envs/wildfire_expanded_full_v1/scored.py'
            or Path(__file__).resolve() != scorer_path):
        raise RuntimeError('expanded v2 modules loaded from an unexpected path')
    return api._hash(dict(version=VERSION, base=base.implementation_sha256(),
        population_v2=hashlib.sha256(population_path.read_bytes()).hexdigest(),
        scored_v2=hashlib.sha256(scorer_path.read_bytes()).hexdigest()))


def _canonical_mixed_factors(factors):
    """Normalize a duplicate-free, explicit mixed-cell factor inventory."""
    if type(factors) not in (list, tuple):
        raise ValueError('mixed factors must be a list or tuple')
    rows, seen = [], set()
    for row in factors:
        if type(row) not in (list, tuple) or len(row) != 4:
            raise ValueError('invalid mixed factor record')
        x, y, fuel, crossing = row
        if (type(x) is not int or type(y) is not int or x < 0 or y < 0
                or (x, y) in seen or type(fuel) not in (int, float)
                or type(crossing) not in (int, float) or not math.isfinite(fuel)
                or not math.isfinite(crossing) or not 0 < fuel <= 1 or not 0 < crossing < 1):
            raise ValueError('invalid or duplicate mixed factor')
        seen.add((x, y))
        rows.append([x, y, float(fuel), float(crossing)])
    return sorted(rows, key=lambda row: (row[1], row[0]))


def mixed_factors_sha256(factors):
    """Hash approved row-major (x, y, fuel, crossing) factors independently."""
    return api._hash(dict(version=MIXED_FACTORS_VERSION, factors=_canonical_mixed_factors(factors)))


def _effective_mixed_factors(host):
    """Check declarations against all factor-bearing live fire/censor state."""
    mixed = host._weather._inputs
    wrapper = host._fire
    if (type(mixed) is not MixedInputsV2 or type(wrapper) is not MixedHoldFireV1
            or host._weather._fire is not wrapper or type(wrapper._inner) is not InteriorMixedFireV2
            or type(wrapper._inner._fire) is not _MixedFire):
        raise ValueError('exact mixed fire composition required')
    backend, fire = wrapper._inner, wrapper._inner._fire
    if backend._inputs is not mixed.contract or backend._censor._contract is not mixed.contract:
        raise ValueError('mixed contract differs from the live fire')
    rows = _canonical_mixed_factors(mixed.factors)
    fuel = {(x, y): factor for x, y, factor, _ in rows}
    crossing = {(x, y): factor for x, y, _, factor in rows}
    def valid_map(value):
        return (type(value) is dict and all(type(p) is tuple and len(p) == 2
                and all(type(c) is int for c in p) and type(v) in (int, float)
                and math.isfinite(v) for p, v in value.items()))
    if (not all(valid_map(v) for v in (backend._mixed_factors, backend._mixed_openness, fire._mixed_openness))
            or backend._mixed_factors != fuel or backend._mixed_openness != crossing
            or fire._mixed_openness != crossing or backend._censor._allowed != frozenset(fuel)):
        raise ValueError('declared and effective mixed factors differ')
    contract = mixed.contract
    for x, y, factor, _ in rows:
        if (x >= contract.width or y >= contract.height or not contract.domain[y][x]
                or contract.surface[y][x] != 'mixed' or contract.fuel[y][x] not in FUEL_MAPPING
                or contract.elevation[y][x] is None):
            raise ValueError('mixed factor references an unsupported cell')
        expected = FUEL_MAPPING[contract.fuel[y][x]]['initial_fuel'] * factor
        actual = fire.cells[y][x]['initial_fuel']
        if type(actual) not in (int, float) or not math.isfinite(actual) or actual != expected:
            raise ValueError('effective mixed-cell fuel differs')
    return rows


def configuration_identity(population, scoring_mask):
    """Produce a candidate for independent review and pinning before a run."""
    if type(population) is not ExpandedPopulationV2:
        raise ValueError('exact expanded population v2 host required')
    population._ensure()
    factors = _effective_mixed_factors(population._inner._host)
    return dict(version=VERSION, population=copy.deepcopy(population._identity),
                mixed_factors_sha256=mixed_factors_sha256(factors),
                scoring_mask_sha256=mask_sha256(scoring_mask),
                implementation_sha256=implementation_sha256(), reward_version=REWARD_VERSION)


def _handover(population):
    counts = population.private_handover_exposure()
    groups = population._inner._ops.groups
    actual = {key: group['count'] if group['exposed'] else 0 for key, group in groups.items()}
    if (type(counts) is not dict or set(counts) != set(actual)
            or any(type(counts[key]) is not int or counts[key] != actual[key] for key in actual)):
        raise ValueError('private handover exposure and operational state differ')
    return counts


class ExpandedScoredV2(base.ExpandedScoredV1):
    """Trusted driver; the superclass supplies only the actor-call methods."""
    def __init__(self, population, *, scoring_mask, expected_identity, opt_in=False):
        if opt_in is not True or type(population) is not ExpandedPopulationV2:
            raise ValueError('explicit opt-in and exact population v2 host required')
        population._ensure()
        host = population._inner._host
        if host._minute != 0 or host._complete or host._censored:
            raise ValueError('uncensored fresh handover required')
        actual = configuration_identity(population, scoring_mask)
        if type(expected_identity) is not dict or api._encoded(actual) != api._encoded(expected_identity):
            raise ValueError('independent configuration identity differs')
        mask = set(scoring_mask)
        fire = host._fire._fire
        burnable = {y * fire.width + x for y, row in enumerate(fire.cells) for x, cell in enumerate(row)
                    if host._fire._is_land(x, y) and cell['fuel'] > 0}
        if not mask <= burnable:
            raise ValueError('mask must contain only handover burnable cells')
        t = host._inputs.transform
        area = abs(t[0] * t[4] - t[1] * t[3]) / 10000
        if not math.isfinite(area) or area <= 0:
            raise ValueError('invalid projected grid cell area')
        handover = _handover(population)
        self._env = population
        self._identity = copy.deepcopy(actual)
        self._mixed = _effective_mixed_factors(host)
        if mixed_factors_sha256(self._mixed) != actual['mixed_factors_sha256']:
            raise ValueError('trusted mixed factor pin differs')
        self._mask = mask
        self._initial = base._footprint(host)
        self._seen = set(self._initial)
        self._counter = OutcomeCounter(self._initial, mask, population._inner._ops.groups, area)
        initial_population = base._population(population._inner._ops)
        if ({key: group['exposed'] for key, group in initial_population.items()} != handover
                or self._counter.initial_exposure != sum(handover.values())):
            raise ValueError('initial counter and private handover exposure differ')
        self._audit = [dict(minute=0, footprint=sorted(self._initial), burnable=sorted(burnable),
                            cell_area_hectares=area, new_footprint=[], population=initial_population)]
        self._calls = []
        self._poisoned = False
        self._seal = api._hash(population.checkpoint())

    def _ensure(self):
        if self._poisoned:
            raise RuntimeError('scored episode indeterminate; restore a trusted checkpoint')
        try:
            self._env._ensure()
            if (mixed_factors_sha256(self._mixed) != self._identity['mixed_factors_sha256']
                    or api._encoded(configuration_identity(self._env, sorted(self._mask))) != api._encoded(self._identity)
                    or api._encoded(_effective_mixed_factors(self._env._inner._host)) != api._encoded(self._mixed)
                    or api._hash(self._env.checkpoint()) != self._seal):
                raise RuntimeError('scored identity or host state changed outside wrapper')
        except BaseException:
            self._poisoned = True
            raise

    def _record_tick(self):
        host = self._env._inner._host
        if api._encoded(_effective_mixed_factors(host)) != api._encoded(self._mixed):
            raise RuntimeError('effective mixed factors changed during a tick')
        minute = host._minute
        if minute == len(self._audit) - 1 and host._censored:
            return
        if minute != len(self._audit):
            raise RuntimeError('audit and host clocks differ')
        new = base._footprint(host) - self._seen
        self._seen.update(new)
        self._counter.update(new)
        self._audit.append(dict(minute=minute, new_footprint=sorted(new),
                                population=base._population(self._env._inner._ops)))

    def _run(self, name, args, kwargs):
        self._ensure()
        try:
            if name == 'advance':
                minutes = args[0]
                if type(minutes) is not int or not 1 <= minutes <= 60:
                    raise ValueError('advance requires 1..60 minutes')
                for _ in range(minutes):
                    result = self._env.advance(1)
                    self._record_tick()
                    host = self._env._inner._host
                    if host._censored or host._complete:
                        break
            else:
                result = getattr(self._env, name)(*args, **kwargs)
                self._record_tick()
            self._calls.append(dict(name=name, args=copy.deepcopy(args), kwargs=copy.deepcopy(kwargs),
                                    result=copy.deepcopy(result)))
            self._seal = api._hash(self._env.checkpoint())
            return result
        except BaseException:
            self._poisoned = True
            raise

    def _outcome(self):
        inner = self._env._inner
        host = inner._host
        result = self._counter.result(inner._ops.groups, host._minute, host._complete,
                                      False, host._censored, inner._ops.orders)
        result['reward_version'] = REWARD_VERSION
        return result

    def checkpoint(self):
        self._ensure()
        value = dict(version=VERSION, identity=copy.deepcopy(self._identity), mask=sorted(self._mask),
                     mixed_factors=copy.deepcopy(self._mixed), population=self._env.checkpoint(), counter=self._counter.checkpoint(),
                     audit=copy.deepcopy(self._audit), calls=copy.deepcopy(self._calls))
        value['sha256'] = api._hash(value)
        return value

    def restore(self, checkpoint):
        try:
            v = copy.deepcopy(checkpoint)
            if (type(v) is not dict or set(v) != {'version','identity','mask','mixed_factors','population','counter','audit','calls','sha256'}
                    or v['version'] != VERSION or api._encoded(v['identity']) != api._encoded(self._identity)
                    or v['mask'] != sorted(self._mask)
                    or api._encoded(v['mixed_factors']) != api._encoded(self._mixed)
                    or mixed_factors_sha256(self._mixed) != self._identity['mixed_factors_sha256']
                    or _canonical_mixed_factors(self._env._inner._host._weather._inputs.factors) != self._mixed
                    or v['sha256'] != api._hash({k: val for k, val in v.items() if k != 'sha256'})
                    or implementation_sha256() != self._identity['implementation_sha256']
                    or api._encoded(self._env._identity) != api._encoded(self._identity['population'])):
                raise ValueError('scored checkpoint identity or integrity differs')
            self._env.restore(v['population'])
            inner = self._env._inner
            host = inner._host
            if api._encoded(_effective_mixed_factors(host)) != api._encoded(self._mixed):
                raise ValueError('restored effective mixed factors differ')
            independent = recompute(v['audit'], scoring_mask=self._mask, terminal=host._complete,
                                    abandoned=False, censored=host._censored)
            handover = self._env.private_handover_exposure()
            counter = OutcomeCounter.restore(v['counter'])
            self._counter, self._audit, self._calls = counter, v['audit'], v['calls']
            self._initial = set(self._audit[0]['footprint'])
            self._seen = self._initial | {c for row in self._audit for c in row['new_footprint']}
            if (type(self._calls) is not list or len(self._audit) != host._minute + 1
                    or {k:g['exposed'] for k,g in self._audit[0]['population'].items()} != handover
                    or self._counter.initial_exposure != sum(handover.values())
                    or self._seen != base._footprint(host)
                    or self._audit[-1]['population'] != base._population(inner._ops)
                    or not base._same_outcome(self._outcome(), independent)
                    or self._counter.inherited != self._initial
                    or self._counter.eligible != self._mask - self._initial
                    or self._counter.additional != (self._seen - self._initial) & self._mask):
                raise ValueError('scored checkpoint accounting differs')
            self._seal = api._hash(self._env.checkpoint())
            self._poisoned = False
            if api._encoded(self.checkpoint()) != api._encoded(v):
                raise ValueError('scored checkpoint restoration differs')
        except BaseException:
            self._poisoned = True
            raise


def verify_outcome(env, *, fresh_population, expected_identity, scoring_mask, infrastructure_timeout=False):
    """Return a score only after a complete v2 day, fresh replay and recomputation."""
    def result(status, verified=False, score=None):
        return dict(version=VERSION, status=status, verified=verified,
                    score_eligible=score is not None, score=score)
    if type(infrastructure_timeout) is not bool:
        raise ValueError('timeout flag must be boolean')
    if infrastructure_timeout:
        return result('timeout')
    try:
        if type(env) is not ExpandedScoredV2 or type(expected_identity) is not dict:
            return result('verification_failed')
        env._ensure()
        if (api._encoded(expected_identity) != api._encoded(env._identity)
                or mask_sha256(scoring_mask) != env._identity['scoring_mask_sha256']):
            return result('verification_failed')
        host = env._env._inner._host
        if host._censored:
            return result('censored')
        if not host._complete or host._minute != 1440:
            return result('unfinished')
        before = env.checkpoint()
        replay = ExpandedScoredV2(fresh_population(), scoring_mask=scoring_mask,
                                  expected_identity=expected_identity, opt_in=True)
        for call in env._calls:
            if call['name'] not in ('advance', 'protect', 'dispatch_hold'):
                return result('verification_failed')
            observed = getattr(replay, call['name'])(*call['args'], **call['kwargs'])
            if api._encoded(observed) != api._encoded(call['result']):
                return result('verification_failed')
        if api._encoded(replay.checkpoint()) != api._encoded(before):
            return result('verification_failed')
        independent = recompute(replay._audit, scoring_mask=scoring_mask, terminal=True,
                                abandoned=False, censored=False)
        if (not independent['score_eligible'] or not base._same_outcome(replay._outcome(), independent)
                or not base._same_outcome(env._outcome(), independent)
                or api._encoded(env.checkpoint()) != api._encoded(before)):
            return result('verification_failed')
        return result('full_day', True, independent['graded_score'])
    except Exception:
        return result('verification_failed')
