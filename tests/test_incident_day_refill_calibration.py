import copy
import json
from pathlib import Path

import pytest

from wildfire_core.incident_day import IncidentDayEnv, fixture_inputs
from envs.wildfire_response.incident_day.plausibility import audit_case


@pytest.mark.parametrize('runtime_minutes,legacy_minutes,actual_minutes,too_short',
                         [(2, 9, 2, False), (9, 2, 2, True)])
def test_refill_screen_uses_the_same_parameter_as_aircraft_runtime(
        runtime_minutes, legacy_minutes, actual_minutes, too_short):
    inputs, network = fixture_inputs()
    env = IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                         expected_network_sha256=network['sha256'])
    env.reset()
    scenario = copy.deepcopy(env._scenario)
    scenario['profile'].update(air_refill_minutes=runtime_minutes, aircraft_refill_minutes=legacy_minutes)
    first = copy.deepcopy(env._audit[0])
    first['resources']['plane-0'].update(state='refilling', due_at=actual_minutes)
    audit = []
    for minute in range(actual_minutes + 1):
        record = copy.deepcopy(first)
        record.update(minute=minute, new_footprint=[])
        if minute == actual_minutes:
            record['resources']['plane-0'].update(state='available', due_at=None)
        audit.append(record)
    bounds = json.loads((Path(__file__).resolve().parents[1]
                         / 'data/incident_day_v2/plausibility/bounds-v1.json').read_text())
    result = audit_case(scenario, env.inputs, env.network, audit, bounds)
    findings = [finding for finding in result['findings']
                if finding['code'] == 'duration_too_short' and finding['resource'] == 'plane-0']
    assert bool(findings) is too_short
