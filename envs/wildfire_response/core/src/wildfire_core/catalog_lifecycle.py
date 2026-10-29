"""Versioned active exercises and explicit historical-catalog retirement.

Lifecycle metadata never changes a scenario's seed, simulator rules or score.
An active exercise is development-only; it is not a validated training corpus.
"""
import copy
from functools import lru_cache
import hashlib
import json

from envs.wildfire_response.incident_day.scenarios import (
    ALL_CASE_IDS, COVERAGE_CASE_IDS, REAL_MAP_CASE_IDS, REAL_MAP_V2_CASE_IDS, fixture_inputs,
)
from envs.wildfire_response.incident_day.real_map_scenarios import PACK_PIN, NETWORK_PIN

VERSION = 'wildfire-catalog-lifecycle-v1'
ACTIVE_FIXTURE_CASE_IDS = COVERAGE_CASE_IDS
ACTIVE_REAL_MAP_CASE_IDS = REAL_MAP_V2_CASE_IDS
ARCHIVED_DAY_CASE_IDS = tuple(case for case in ALL_CASE_IDS
                              if case not in ACTIVE_FIXTURE_CASE_IDS + ACTIVE_REAL_MAP_CASE_IDS)
ARCHIVED_CATALOGS = ('benchmark_v1', 'synthetic_expansion_v1', 'topographic_stress_v1', 'ops_v1')


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


@lru_cache(maxsize=1)
def _fixture_pins():
    inputs, network = fixture_inputs()
    return inputs['sha256'], network['sha256']


def active_day_case_ids(inputs_sha256, network_sha256):
    pins = (inputs_sha256, network_sha256)
    if pins == (PACK_PIN, NETWORK_PIN):
        return ACTIVE_REAL_MAP_CASE_IDS
    if pins == _fixture_pins():
        return ACTIVE_FIXTURE_CASE_IDS
    raise ValueError('No active exercise suite for these input/network pins; use validated inputs or an explicit archived scenario')


def default_day_case_id(inputs_sha256, network_sha256):
    return active_day_case_ids(inputs_sha256, network_sha256)[0]


def select_day_case_id(case_id, inputs_sha256, network_sha256, *, allow_archived=False):
    if type(allow_archived) is not bool:
        raise TypeError('allow_archived must be a boolean')
    if case_id is None:
        return default_day_case_id(inputs_sha256, network_sha256)
    if case_id not in ALL_CASE_IDS:
        raise ValueError('Unknown incident-day scenario')
    if case_id in REAL_MAP_CASE_IDS + REAL_MAP_V2_CASE_IDS and (inputs_sha256, network_sha256) != (PACK_PIN, NETWORK_PIN):
        raise ValueError('Real-map scenario requires its matching input and network pins')
    if allow_archived:
        return case_id
    if case_id not in active_day_case_ids(inputs_sha256, network_sha256):
        raise ValueError('Archived or unsupported scenario: enable allow_archived_scenarios only for explicit legacy replay')
    return case_id


def require_archived_opt_in(catalog_id, *, allow_archived=False):
    if type(allow_archived) is not bool:
        raise TypeError('allow_archived must be a boolean')
    if catalog_id not in ARCHIVED_CATALOGS:
        raise ValueError('Unknown archived catalog')
    if not allow_archived:
        raise ValueError(f'{catalog_id} is archived; --allow-archived-catalog is required for historical exports or learning recipes. This does not authorize GPU spending or publication.')


def registry():
    records = [
        dict(id='incident_day_fixture', status='active_development', case_ids=list(ACTIVE_FIXTURE_CASE_IDS),
             horizon_minutes=1440, inputs_sha256=_fixture_pins()[0], network_sha256=_fixture_pins()[1]),
        dict(id='incident_day_realmap_v2', status='active_development', case_ids=list(ACTIVE_REAL_MAP_CASE_IDS),
             horizon_minutes=1440, inputs_sha256=PACK_PIN, network_sha256=NETWORK_PIN),
        dict(id='incident_day_legacy', status='archived', case_ids=list(ARCHIVED_DAY_CASE_IDS),
             reason='Superseded development probes, including ineffective original handover assignments; replay only'),
    ]
    for name in ARCHIVED_CATALOGS:
        records.append(dict(id=name, status='archived',
            reason=('Night-only two-hour reference; no active aircraft-response coverage'
                    if name != 'ops_v1' else 'Superseded preliminary ops interface; retained for explicit historical research'),
            retained_in_place=True, rewrite_existing_ids=False))
    result = dict(version=VERSION, catalogs=records, training_corpus_available=False,
        policy='Only corrected input-pinned 24-hour development suites are active. Archives retain their original scope, source rights, seeds, scores and replay pins. No active train/validation/test corpus is claimed.')
    result['sha256'] = hashlib.sha256(_canonical(result)).hexdigest()
    return copy.deepcopy(result)


if __name__ == '__main__':
    print(json.dumps(registry(), indent=2, sort_keys=True))
