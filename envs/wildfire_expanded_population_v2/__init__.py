"""Pinned synthetic cohort handover exposure, composed with population v1.

The Python instance, catalog, scorer state and checkpoints are trusted-driver
objects. Only the explicit actor methods are suitable for an allowlisted facade.
"""
import copy
import hashlib
from pathlib import Path

import adapter_v1 as api
from wildfire_expanded_full_v1.population import ExpandedPopulationV1, CATALOG_VERSION as V1_CATALOG

VERSION = 'wildfire-expanded-population-protection-v2'
CATALOG_VERSION = 'wildfire-expanded-synthetic-population-v2'
CHECKPOINT_VERSION = 'wildfire-expanded-population-checkpoint-v2'


def _source():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class ExpandedPopulationV2:
    """An explicitly declared, whole-cohort synthetic handover exposure state."""
    def __init__(self, host, catalog, *, expected_catalog_sha256, opt_in=False):
        if opt_in is not True:
            raise ValueError('explicit population v2 opt-in required')
        catalog = copy.deepcopy(catalog)
        if (type(catalog) is not dict or set(catalog) != {'version','role','case_id','inputs_sha256',
                'network_sha256','groups','destinations'} or catalog['version'] != CATALOG_VERSION
                or catalog['role'] != 'synthetic_fixture_only'
                or type(expected_catalog_sha256) is not str or api._hash(catalog) != expected_catalog_sha256
                or type(catalog['groups']) is not list or not catalog['groups']):
            raise ValueError('population v2 catalog or pin differs')
        declarations = {}
        handover = {}
        for group in catalog['groups']:
            if (type(group) is not dict or set(group) != {'id','count','position','initial_exposed','handover_exposure'}
                    or type(group['id']) is not str or not group['id'] or group['id'] in declarations
                    or type(group['initial_exposed']) is not bool
                    or (group['handover_exposure'] is not None and type(group['handover_exposure']) is not bool)
                    or (group['handover_exposure'] is not None
                        and group['handover_exposure'] != group['initial_exposed'])):
                raise ValueError('invalid synthetic initial exposure or handover disclosure')
            declarations[group['id']] = group['initial_exposed']
            handover[group['id']] = group['handover_exposure']
        # Use the unmodified v1 implementation for all validation and lifecycle.
        # This derived catalog is internal: only the independently pinned v2
        # catalog supplies the exposure declarations.
        original = copy.deepcopy(catalog)
        derived = copy.deepcopy(catalog)
        derived['version'] = V1_CATALOG
        derived['groups'] = [{k:v for k,v in g.items() if k not in ('initial_exposed', 'handover_exposure')} for g in derived['groups']]
        inner = ExpandedPopulationV1(host, derived, expected_catalog_sha256=api._hash(derived), opt_in=True)
        if not host._censored:
            burning = {tuple(p) for p in host._fire.burning()}
            for group in catalog['groups']:
                if tuple(host._router._cell(group['position'])) in burning and not group['initial_exposed']:
                    raise ValueError('a cohort on a burning handover cell must be declared exposed')
        for identifier, exposed in declarations.items():
            inner._ops.groups[identifier]['exposed'] = exposed
            inner._known[identifier]['exposed'] = handover[identifier] is True
            inner._exposed_at[identifier] = 0 if handover[identifier] is True else None
        self._inner = inner
        self._catalog = original
        self._initial_exposed = declarations
        self._handover_exposure = handover
        self._source = _source()
        self._identity = dict(version=VERSION, source=self._source, catalog=expected_catalog_sha256,
                              base=copy.deepcopy(inner._identity))
        self._poisoned = False
        self._ensure()

    def _ensure(self):
        if self._poisoned:
            raise RuntimeError('population v2 indeterminate; restore a trusted checkpoint')
        self._inner._ensure()
        if (self._source != _source() or self._identity['source'] != self._source
                or self._identity['catalog'] != api._hash(self._catalog)
                or self._initial_exposed != {g['id']:g['initial_exposed'] for g in self._catalog['groups']}
                or self._handover_exposure != {g['id']:g['handover_exposure'] for g in self._catalog['groups']}
                or api._encoded(self._identity['base']) != api._encoded(self._inner._identity)):
            self._poisoned = True
            raise RuntimeError('population v2 source or identity changed')
        for identifier, exposed in self._initial_exposed.items():
            known_time = self._inner._exposed_at[identifier]
            if ((exposed and self._inner._ops.groups[identifier]['exposed'] is not True)
                    or (self._handover_exposure[identifier] is True and
                        (self._inner._known[identifier]['exposed'] is not True or known_time != 0))
                    or (self._handover_exposure[identifier] is not True and known_time == 0)):
                self._poisoned = True
                raise RuntimeError('declared handover exposure changed')

    def _result(self, value):
        value = copy.deepcopy(value)
        if isinstance(value, dict) and 'version' in value:
            value['version'] = VERSION
        if isinstance(value, dict) and 'population' in value:
            for group in value['population']:
                identifier = group['id']
                group['observed_at'] = self._inner._known_at[identifier]
                exposure_at = self._inner._exposed_at[identifier]
                if exposure_at is not None:
                    group['exposed'], group['exposure_observed_at'] = True, exposure_at
                else:
                    # False is knowledge only when independently disclosed at
                    # handover or contained in an actually delivered report.
                    times = [r['observed_at'] for r in self._inner._host._bus.reports
                             if isinstance(r.get('payload', {}).get('group'), dict)
                             and r['payload']['group'].get('id') == identifier
                             and r['payload']['group'].get('exposed') is False]
                    if self._handover_exposure[identifier] is False:
                        times.append(0)
                    group['exposed'] = False if times else None
                    group['exposure_observed_at'] = min(times) if times else None
        return value

    def situation(self):
        self._ensure()
        return self._result(self._inner.situation())

    def reports(self, *, after_cursor=0, limit=20):
        self._ensure()
        return self._inner.reports(after_cursor=after_cursor, limit=limit)

    def route_options(self, resource_id, target_grid):
        self._ensure()
        return self._inner.route_options(resource_id, target_grid)

    def evacuation_options(self, group_id, destination_id):
        self._ensure()
        return self._inner.evacuation_options(group_id, destination_id)

    def protect(self, group_id, action, *, destination_id=None, option_id=None):
        self._ensure()
        return self._result(self._inner.protect(group_id, action, destination_id=destination_id, option_id=option_id))

    def dispatch_hold(self, resource_id, target_grid, option_id, edge, protected_cell):
        self._ensure()
        return self._result(self._inner.dispatch_hold(resource_id, target_grid, option_id, edge, protected_cell))

    def advance(self, minutes=1):
        self._ensure()
        return self._result(self._inner.advance(minutes))

    def private_handover_exposure(self):
        """Return declared inherited exposed counts for a trusted scorer only."""
        self._ensure()
        return {g['id']: g['count'] if self._initial_exposed[g['id']] else 0 for g in self._catalog['groups']}

    def checkpoint(self):
        self._ensure()
        value = dict(version=CHECKPOINT_VERSION, identity=copy.deepcopy(self._identity),
                     population=self._inner.checkpoint())
        value['sha256'] = api._hash(value)
        return value

    def restore(self, checkpoint):
        try:
            value = copy.deepcopy(checkpoint)
            if (type(value) is not dict or set(value) != {'version','identity','population','sha256'}
                    or value['version'] != CHECKPOINT_VERSION
                    or api._encoded(value['identity']) != api._encoded(self._identity)
                    or _source() != self._source
                    or value['sha256'] != api._hash({k:v for k,v in value.items() if k != 'sha256'})):
                raise ValueError('population v2 checkpoint identity or integrity differs')
            self._inner.restore(value['population'])
            self._poisoned = False
            self._ensure()
            if api._encoded(self.checkpoint()) != api._encoded(value):
                raise ValueError('population v2 checkpoint restore differs')
        except BaseException:
            self._poisoned = True
            raise
