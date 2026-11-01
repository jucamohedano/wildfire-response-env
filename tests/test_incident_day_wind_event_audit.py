import copy
import json
import os
from pathlib import Path

import pytest

from envs.wildfire_response.incident_day.plausibility import audit_case, counterfactual_case
from envs.wildfire_response.incident_day.real_map_acceptance import wind_access_event
from envs.wildfire_response.incident_day.real_map_scenarios import NETWORK_PIN, PACK_PIN
from wildfire_core.incident_day import IncidentDayEnv, fixture_inputs

ROOT = Path(__file__).resolve().parents[1]
BOUNDS = json.loads((ROOT / 'data/incident_day_v2/plausibility/bounds-v1.json').read_text())


def test_versioned_selection_preserves_old_minutes_and_rejects_ambiguity():
    inputs, network = fixture_inputs()
    env = IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                         expected_network_sha256=network['sha256'])
    env.reset('incident-day-shift-v1-001')
    old = env._scenario
    assert wind_access_event(old)['minute'] == 180
    assert counterfactual_case(env)['status'] == 'demonstrated'
    v2 = copy.deepcopy(old)
    v2['profile']['realmap_v2_wind_coverage'] = True
    v2['events'].append({'kind': 'wind', 'minute': 15, 'wind': [0., 2.]})
    assert wind_access_event(v2)['minute'] == 15
    development = copy.deepcopy(v2)
    development['version'] = 'wildfire-incident-day-realmap-development-scenarios-v1'
    next(e for e in development['events'] if e['minute'] == 15)['minute'] = 45
    assert wind_access_event(development)['minute'] == 45
    development['events'].append({'kind': 'wind', 'minute': 90, 'wind': [0., 2.]})
    with pytest.raises(ValueError, match='identifiable'):
        wind_access_event(development)
    development['events'] = [e for e in development['events'] if e.get('wind') != [0., 2.]]
    with pytest.raises(ValueError, match='identifiable'):
        wind_access_event(development)


@pytest.fixture(scope='module')
def source_inputs():
    path = Path(os.environ.get('WILDFIRE_LOCAL_RUNTIME', '.runtime/wildfire-response-spain'))
    inputs = path / 'runtime/es-ma-2021/inputs.json'
    network = path / 'runtime/es-ma-2021/network.json'
    if not inputs.is_file() or not network.is_file():
        pytest.skip('Local pinned source-map runtime unavailable')
    return json.loads(inputs.read_text()), json.loads(network.read_text())


@pytest.mark.parametrize('case_id,minute', [
    ('incident-day-realmap-v2-001', 15),
    ('incident-day-realmap-development-v1-002', 20),
    ('incident-day-realmap-development-v1-003', 45),
])
def test_scheduled_transition_is_screened_captured_and_audited(source_inputs, case_id, minute):
    inputs, network = source_inputs
    env = IncidentDayEnv(inputs, network, expected_inputs_sha256=PACK_PIN,
                         expected_network_sha256=NETWORK_PIN, allow_unapproved_data=True)
    env.reset(case_id)
    event = wind_access_event(env._scenario)
    assert event['minute'] == minute
    counterfactual = counterfactual_case(env)
    assert counterfactual['status'] == 'demonstrated'
    assert counterfactual['details']['new_wind'] == event['wind']
    while env.minute < minute:
        env.advance_time(min(10, minute - env.minute))
    effects = env._audit[minute]['event_effects']
    assert any(effect['kind'] == 'wind' and effect['minute'] == minute for effect in effects)
    audited = audit_case(env._scenario, env.inputs, env.network, env._audit, BOUNDS,
                         reports=env.bus.reports)
    evidence = next(row for row in audited['event_evidence'] if row['event'] == event)
    assert 'causal_evidence' in evidence
    assert evidence['status'] != 'not_observed_censored'
    later = next(row for row in audited['event_evidence'] if row['event']['kind'] == 'wind'
                 and row['event']['minute'] > minute)
    assert later['status'] == 'not_observed_censored'


@pytest.mark.parametrize('index', [0, 1])
def test_development_quiet_cases_use_full_work_and_return_probe(source_inputs, index):
    inputs, network = source_inputs
    env = IncidentDayEnv(inputs, network, expected_inputs_sha256=PACK_PIN,
                         expected_network_sha256=NETWORK_PIN, allow_unapproved_data=True)
    env.reset(f'incident-day-realmap-development-v1-{index:03}')
    result = counterfactual_case(env)
    assert result['status'] == 'demonstrated'
    assert result['details']['work_minute'] < result['details']['return_minute']
