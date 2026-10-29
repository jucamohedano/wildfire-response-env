import copy
import inspect
from pathlib import Path
from unittest.mock import patch

import pytest

from envs.wildfire_response.incident_day import environment
from envs.wildfire_response.incident_day.outcomes import OutcomeCounter, recompute
from envs.wildfire_response.regional.fire import RegionalFire
from wildfire_core.incident_day import IncidentDayEnv, fixture_inputs
from wildfire_core.mobility_routing import journey_position


@pytest.fixture
def incident():
    inputs, network = fixture_inputs(24)
    env = IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                         expected_network_sha256=network['sha256'])
    env.reset()
    return env


def test_colocated_crew_queries_have_distinct_usable_approaches(incident):
    first = incident.route_options('crew-1')['options']
    second = incident.route_options('relief-crew-0')['options']
    assert first and second
    assert {option['id'] for option in first}.isdisjoint(option['id'] for option in second)
    for resource, option in (('crew-1', first[0]), ('relief-crew-0', second[0])):
        action = {'requests': [{'resource_id': resource, 'sector_id': 'incident', 'approach_id': option['id']}]}
        assert incident.check_action(action)['valid']
    assert incident.minute == 0


@pytest.mark.parametrize('observed_count,expect_reinforcement', [(0, False), (12, True)])
def test_reinforcement_depends_on_observed_not_hidden_fire(incident, observed_count, expect_reinforcement):
    incident._memory = {(column, 0): {'x': column, 'y': 0, 'burning': True,
                                      'observed_at': 0, 'received_at': 0}
                        for column in range(observed_count)}
    hidden_fire = {(column, row) for column in range(2, 20) for row in range(2, 20)}
    with patch.object(incident, '_burning', return_value=hidden_fire), \
            patch.object(incident.fire, 'step'), patch.object(incident, '_emit', wraps=incident._emit) as emit:
        incident._tick()
    calls = [call for call in emit.call_args_list if call.args[1] == 'reinforcement_needed']
    assert bool(calls) is expect_reinforcement


def _delay_order_reports(bus):
    original = bus.emit

    def emit(source, kind, payload, minute, **kwargs):
        if 'order_id' in payload:
            kwargs['delay'] = 5
        return original(source, kind, payload, minute, **kwargs)

    return emit


def test_protection_order_appears_only_after_a_delivered_report(incident):
    shelter = {'protection_orders': [{'group_id': 'population-0', 'action': 'shelter',
                                       'destination_id': None, 'approach_id': None}]}
    with patch.object(incident.bus, 'emit', side_effect=_delay_order_reports(incident.bus)):
        observation, _, _, _, info = incident.step(shelter)
        assert info['error'] is None
        assert incident.ops.orders
        assert observation['protection_orders'] == []
        while incident.minute < 5:
            incident.advance_time(1)
            assert incident.get_situation()['protection_orders'] == []
        incident.advance_time(1)
    delivered = [report for report in incident.get_reports(limit=20)
                 if report['kind'] == 'protection_accepted']
    assert delivered and delivered[0]['received_at'] == 6
    assert incident.get_situation()['protection_orders'][0]['id'] == delivered[0]['payload']['order_id']


def test_release_runs_next_minute_and_emits_delayed_acknowledgement(incident):
    calls = []
    original_release = incident.ops.release
    original_emit = incident.bus.emit

    def release(resource_id, journey, minute):
        calls.append((incident.minute, minute, resource_id))
        return original_release(resource_id, journey, minute)

    def emit(source, kind, payload, minute, **kwargs):
        if kind in ('release_accepted', 'release_declined'):
            kwargs['delay'] = 5
        return original_emit(source, kind, payload, minute, **kwargs)

    with patch.object(incident.ops, 'release', side_effect=release), patch.object(incident.bus, 'emit', side_effect=emit):
        observation, _, _, _, info = incident.step({'releases': ['engine-0']})
        assert info['error'] is None
        assert calls == [(1, 1, 'engine-0')]
        assert not any(report['kind'] in ('release_accepted', 'release_declined') for report in observation['reports'])
        while incident.minute < 6:
            incident.advance_time(1)
    acknowledgements = [report for report in incident.get_reports(limit=20)
                        if report['kind'] in ('release_accepted', 'release_declined')]
    assert len(acknowledgements) == 1
    assert acknowledgements[0]['observed_at'] == 1
    assert acknowledgements[0]['received_at'] == 6
    assert acknowledgements[0]['payload']['resource_id'] == 'engine-0'


@pytest.mark.parametrize('kind,new_payload,old_payload,field', [
    ('wind_update', {'wind': [0, 2]}, {'wind': [2, 0]}, 'wind'),
    ('destination_threatened', {'destination': {'id': 'destination-0', 'status': 'closed'}},
     {'destination': {'id': 'destination-0', 'status': 'open'}}, 'destinations'),
    ('order_update', {'order': {'id': 'test-order', 'state': 'completed'}},
     {'order': {'id': 'test-order', 'state': 'issued'}}, 'protection_orders'),
])
def test_older_reports_cannot_overwrite_newer_public_knowledge(incident, kind, new_payload, old_payload, field):
    def record(payload, observed, received):
        return {'kind': kind, 'payload': payload, 'observed_at': observed, 'received_at': received}

    incident._receive([record(new_payload, 10, 11)])
    newer = copy.deepcopy(incident.get_situation()[field])
    incident._receive([record(old_payload, 5, 12)])
    assert incident.get_situation()[field] == newer


@pytest.mark.parametrize('source', [RegionalFire, journey_position])
def test_implementation_digest_tracks_simulator_dependencies_without_editing_files(source):
    target = Path(inspect.getfile(source)).resolve()
    baseline = environment.implementation_digest()
    read_bytes = Path.read_bytes
    changed = []

    def altered(path):
        data = read_bytes(path)
        if path.resolve() == target:
            changed.append(path)
            return data + b'\nsynthetic regression change\n'
        return data

    with patch.object(Path, 'read_bytes', altered):
        modified = environment.implementation_digest()
    assert changed, f'{target} was not read when computing the implementation digest'
    assert modified != baseline


def test_unable_to_comply_cannot_be_safe_success_in_both_scorers():
    groups = {'group': {'count': 10, 'exposed': False, 'status': 'unable_to_comply'}}
    counter = OutcomeCounter([], [0], groups, 1)
    incremental = counter.result(groups, 1440, True, False, False)
    population = {'group': {'count': 10, 'exposed': 0, 'status': 'unable_to_comply'}}
    records = [{'minute': 0, 'footprint': [], 'new_footprint': [], 'burnable': [0],
                'cell_area_hectares': 1, 'population': population}]
    records.extend({'minute': minute, 'new_footprint': [], 'population': population}
                   for minute in range(1, 1441))
    independent = recompute(records)
    for result in (incremental, independent):
        assert result['unresolved_evacuations'] >= 1
        assert result['safe_success'] is False


def test_pending_protection_at_day_end_is_not_safe_success(incident):
    while incident.minute < 1439 and not (incident.terminated or incident.truncated):
        incident.advance_time(min(60, 1439 - incident.minute))
    assert incident.minute == 1439 and not incident.truncated
    action = {'protection_orders': [{'group_id': 'population-0', 'action': 'shelter',
                                      'destination_id': None, 'approach_id': None}]}
    _, _, terminated, truncated, info = incident.step(action)
    assert info['error'] is None and terminated and not truncated
    for result in (incident.outcomes(), recompute(incident._audit)):
        assert result['unresolved_evacuations'] >= 1
        assert result['safe_success'] is False
