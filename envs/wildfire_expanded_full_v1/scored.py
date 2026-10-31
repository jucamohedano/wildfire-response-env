"""Private fixed-mask scoring for the synthetic expanded population slice.

Only the explicitly listed actor methods may be exported. The instance, factory,
configuration identity, audit, transcript, checkpoints and verifier are trusted
runner data, not a sandbox or an actor interface.
"""
import copy
import hashlib
import math
from pathlib import Path
import inspect

import adapter_v1 as api
from envs.wildfire_response.incident_day.outcomes import OutcomeCounter
from wildfire_containment_score_v1.scoring import REWARD_VERSION, mask_sha256, recompute
from .population import ExpandedPopulationV1

VERSION = 'wildfire-expanded-scored-v1'
_ROOT = Path(__file__).resolve().parents[2]


def implementation_sha256():
    """Digest repository Python sources used by this composition and verifier."""
    dirs = ('envs/wildfire_expanded_full_v1', 'envs/wildfire_expanded_incident_v1',
            'envs/wildfire_extended_inputs_v1', 'envs/wildfire_containment_v1/src',
            'envs/wildfire_containment_score_v1/src',
            'envs/wildfire_response/core/src/envs/wildfire_response/incident_day',
            'envs/wildfire_response/core/src/envs/wildfire_response/regional',
            'envs/wildfire_response/core/src/simulator', 'envs/wildfire_response/core/src/wildfire_core')
    expected_modules = {
        api: _ROOT / 'envs/wildfire_extended_inputs_v1/adapter_v1.py',
        inspect.getmodule(OutcomeCounter): _ROOT / 'envs/wildfire_response/core/src/envs/wildfire_response/incident_day/outcomes.py',
        inspect.getmodule(recompute): _ROOT / 'envs/wildfire_containment_score_v1/src/wildfire_containment_score_v1/scoring.py',
        inspect.getmodule(ExpandedPopulationV1): _ROOT / 'envs/wildfire_expanded_full_v1/population.py',
    }
    if any(module is None or Path(module.__file__).resolve() != path for module, path in expected_modules.items()):
        raise RuntimeError('scoring dependencies were loaded from an unexpected path')
    paths = [p for directory in dirs for p in (_ROOT / directory).rglob('*.py')]
    paths.append(_ROOT / 'eval/incident_weather_v1.py')
    return api._hash({str(p.relative_to(_ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in sorted(paths)})


def configuration_identity(population, scoring_mask):
    """Produce a candidate identity for review and independent pinning before a run.

    A value obtained from the episode under evaluation is not an independent pin.
    """
    if type(population) is not ExpandedPopulationV1:
        raise ValueError('exact expanded population host required')
    population._ensure()
    return dict(version=VERSION, population=copy.deepcopy(population._identity),
                scoring_mask_sha256=mask_sha256(scoring_mask),
                implementation_sha256=implementation_sha256(), reward_version=REWARD_VERSION)


def _footprint(host):
    fire = host._fire._fire
    return {y * fire.width + x for y, row in enumerate(fire.cells) for x, cell in enumerate(row)
            if cell['heat'] > 0 or cell['burned'] > 0}


def _same_outcome(left, right):
    if left.keys() != right.keys():
        return False
    return all((type(a) is float and type(b) is float and math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12))
               or (type(a) is type(b) and a == b) for a, b in ((left[k], right[k]) for k in left))


def _population(ops):
    pending = {o['group_id'] for o in ops.orders.values()
               if o['state'] not in ('completed', 'cancelled', 'unable_to_comply')}
    return {k: dict(count=g['count'], exposed=g['count'] if g['exposed'] else 0,
                    status=g['status'], protection_pending=k in pending)
            for k, g in ops.groups.items()}


class ExpandedScoredV1:
    """Trusted runner wrapper. Construct with a fresh, exact population host."""
    def __init__(self, population, *, scoring_mask, expected_identity, opt_in=False):
        if opt_in is not True or type(population) is not ExpandedPopulationV1:
            raise ValueError('explicit opt-in and exact population host required')
        population._ensure()
        host = population._host
        if host._minute != 0 or host._complete or host._censored:
            raise ValueError('uncensored fresh handover required')
        actual = configuration_identity(population, scoring_mask)
        if type(expected_identity) is not dict or api._encoded(actual) != api._encoded(expected_identity):
            raise ValueError('independent configuration identity differs')
        mask = set(scoring_mask)  # mask_sha256 already validates duplicates and types
        fire = host._fire._fire
        burnable = {y * fire.width + x for y, row in enumerate(fire.cells) for x, cell in enumerate(row)
                    if host._fire._is_land(x, y) and cell['fuel'] > 0}
        if not mask <= burnable:
            raise ValueError('mask must contain only handover burnable cells')
        t = host._inputs.transform
        area = abs(t[0] * t[4] - t[1] * t[3]) / 10000
        if not math.isfinite(area) or area <= 0:
            raise ValueError('invalid projected grid cell area')
        self._env = population
        self._identity = copy.deepcopy(actual)
        self._mask = mask
        self._initial = _footprint(host)
        self._seen = set(self._initial)
        self._counter = OutcomeCounter(self._initial, mask, population._ops.groups, area)
        self._audit = [dict(minute=0, footprint=sorted(self._initial), burnable=sorted(burnable),
                            cell_area_hectares=area, new_footprint=[], population=_population(population._ops))]
        self._calls = []
        self._poisoned = False
        self._seal = api._hash(population.checkpoint())

    def _ensure(self):
        if self._poisoned:
            raise RuntimeError('scored episode indeterminate; restore a trusted checkpoint')
        self._env._ensure()
        if (configuration_identity(self._env, sorted(self._mask)) != self._identity
                or api._hash(self._env.checkpoint()) != self._seal):
            self._poisoned = True
            raise RuntimeError('scored identity or host state changed outside wrapper')

    def situation(self):
        self._ensure()
        return self._env.situation()

    def reports(self, *, after_cursor=0, limit=20):
        self._ensure()
        return self._env.reports(after_cursor=after_cursor, limit=limit)

    def route_options(self, resource_id, target_grid):
        self._ensure()
        return self._env.route_options(resource_id, target_grid)

    def evacuation_options(self, group_id, destination_id):
        self._ensure()
        return self._env.evacuation_options(group_id, destination_id)

    def _record_tick(self):
        host = self._env._host
        minute = host._minute
        if minute == len(self._audit) - 1 and host._censored:
            return
        if minute != len(self._audit):
            raise RuntimeError('audit and host clocks differ')
        footprint = _footprint(host)
        new = footprint - self._seen
        self._seen.update(new)
        self._counter.update(new)
        self._audit.append(dict(minute=minute, new_footprint=sorted(new), population=_population(self._env._ops)))

    def _run(self, name, args, kwargs):
        self._ensure()
        # Ordinary validation errors before a tick may be safely retried, but an
        # exception during any call can leave the underlying host indeterminate.
        try:
            if name == 'advance':
                minutes = args[0]
                if type(minutes) is not int or not 1 <= minutes <= 60:
                    raise ValueError('advance requires 1..60 minutes')
                for _ in range(minutes):
                    result = self._env.advance(1)
                    self._record_tick()
                    if self._env._host._censored or self._env._host._complete:
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

    def advance(self, minutes=1):
        return self._run('advance', [minutes], {})

    def protect(self, group_id, action, *, destination_id=None, option_id=None):
        return self._run('protect', [group_id, action], dict(destination_id=destination_id, option_id=option_id))

    def dispatch_hold(self, resource_id, target_grid, option_id, edge, protected_cell):
        return self._run('dispatch_hold', [resource_id, target_grid, option_id, edge, protected_cell], {})

    def _outcome(self):
        host = self._env._host
        result = self._counter.result(self._env._ops.groups, host._minute, host._complete,
                                      False, host._censored, self._env._ops.orders)
        result['reward_version'] = REWARD_VERSION
        return result

    def checkpoint(self):
        self._ensure()
        value = dict(version=VERSION, identity=copy.deepcopy(self._identity), mask=sorted(self._mask),
                     population=self._env.checkpoint(), counter=self._counter.checkpoint(),
                     audit=copy.deepcopy(self._audit), calls=copy.deepcopy(self._calls))
        value['sha256'] = api._hash(value)
        return value

    def restore(self, checkpoint):
        try:
            v = copy.deepcopy(checkpoint)
            if (type(v) is not dict or set(v) != {'version','identity','mask','population','counter','audit','calls','sha256'}
                    or v['version'] != VERSION or v['identity'] != self._identity or v['mask'] != sorted(self._mask)
                    or v['sha256'] != api._hash({k: val for k, val in v.items() if k != 'sha256'})
                    or implementation_sha256() != self._identity['implementation_sha256']
                    or self._env._identity != self._identity['population']):
                raise ValueError('scored checkpoint identity or integrity differs')
            self._env.restore(v['population'])
            host = self._env._host
            independent = recompute(v['audit'], scoring_mask=self._mask, terminal=host._complete,
                                    abandoned=False, censored=host._censored)
            counter = OutcomeCounter.restore(v['counter'])
            self._counter, self._audit, self._calls = counter, v['audit'], v['calls']
            self._initial = set(self._audit[0]['footprint'])
            self._seen = self._initial | {c for row in self._audit for c in row['new_footprint']}
            if (type(self._calls) is not list or len(self._audit) != host._minute + 1
                    or self._seen != _footprint(host) or self._audit[-1]['population'] != _population(self._env._ops)
                    or not _same_outcome(self._outcome(), independent) or self._counter.inherited != self._initial
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
    """Fail closed: v1 cannot establish inherited population exposure."""
    def result(status, verified=False, score=None):
        return dict(version=VERSION, status=status, verified=verified,
                    score_eligible=score is not None, score=score)
    if type(infrastructure_timeout) is not bool:
        raise ValueError('timeout flag must be boolean')
    if infrastructure_timeout:
        return result('timeout')
    try:
        if type(env) is not ExpandedScoredV1 or type(expected_identity) is not dict:
            return result('verification_failed')
        env._ensure()
        if (api._encoded(expected_identity) != api._encoded(env._identity)
                or mask_sha256(scoring_mask) != env._identity['scoring_mask_sha256']):
            return result('verification_failed')
        host = env._env._host
        if host._censored:
            return result('censored')
        if not host._complete or host._minute != 1440:
            return result('unfinished')
        # Population v1 initializes every cohort unexposed, so a fresh replay
        # cannot recover exposure already present at handover. It is never a
        # trustworthy basis for an eligible score.
        return result('handover_exposure_unavailable')
    except Exception:
        return result('verification_failed')
