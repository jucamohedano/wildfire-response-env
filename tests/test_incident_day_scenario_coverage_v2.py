import copy
import gzip
import json

import pytest

from wildfire_core.incident_day import COVERAGE_VARIANT_IDS, EGRESS_CASE_IDS, IncidentDayEnv, fixture_inputs
from envs.wildfire_response.incident_day.baselines import DayDispatcher
from envs.wildfire_response.incident_day.scenario_coverage import event_coverage
from envs.wildfire_response.incident_day.plausibility import counterfactual_case
from envs.wildfire_response.regional.ops.generator import digest


def environment():
    inputs, network = fixture_inputs()
    return IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                          expected_network_sha256=network['sha256'])


def assess(current, *, audit=None, queries=None, orders=None):
    event = next(item for item in current._scenario['events']
                 if item['kind'] in ('access_restriction', 'destination_closed'))
    return event_coverage(event, current._scenario, current.inputs,
        current._audit if audit is None else audit, current.bus.reports,
        list(current.ops.orders.values()) if orders is None else orders,
        current._query_log if queries is None else queries)


@pytest.fixture(scope='module')
def reference_runs():
    result = {}
    for identifier, cutoff in zip(COVERAGE_VARIANT_IDS, (210, 550)):
        current = environment()
        observation, unused = current.reset(identifier)
        dispatcher = DayDispatcher()
        while current.minute < cutoff:
            observation, unused_reward, unused_terminated, unused_truncated, unused_info = current.step(
                dispatcher.action(current, observation))
        result[identifier] = current
    return result


def test_crew_only_access_removes_engine_missions_and_crews_do_the_work(reference_runs):
    current = reference_runs[COVERAGE_VARIANT_IDS[0]]
    result = assess(current)
    assert result['status'] == 'constraint_evidence_observed'
    evidence = result['causal_evidence']
    assert all(item['options_before'] > 0 and item['options_after'] == 0
               for item in evidence['resource_access'])
    assert evidence['public_refusals']
    assert evidence['crew_work_count'] > 0 and not evidence['forbidden_work']
    assert evidence['first_crew_work']['minute'] >= evidence['restriction_received_at'] > 90
    assert 'options_before' not in str(current.get_situation())
    assert 'event_effects' not in str(current.get_situation())


def test_access_coverage_rejects_queries_made_before_the_warning(reference_runs):
    current = reference_runs[COVERAGE_VARIANT_IDS[0]]
    queries = copy.deepcopy(current._query_log)
    for query in queries:
        if query.get('public_route_summary', {}).get('option_count') == 0:
            query['minute'] = 89
    assert assess(current, queries=queries)['status'] != 'constraint_evidence_observed'


def test_destination_closure_displaces_real_occupants_and_completes_relocation(reference_runs):
    current = reference_runs[COVERAGE_VARIANT_IDS[1]]
    result = assess(current)
    assert result['status'] == 'constraint_evidence_observed'
    evidence = result['causal_evidence']
    assert sum(item['count'] for item in evidence['affected_occupants']) == 150
    relocation = evidence['completed_relocations'][0]
    assert relocation['destination_id'] == 'destination-0'
    assert 480 < evidence['warning_received_at'] <= relocation['issued_at'] < relocation['completed_at']
    assert not evidence['unresolved_occupants']
    assert current.ops.groups['population-1']['destination_id'] == 'destination-0'


def test_an_unoccupied_closure_cannot_claim_coverage_from_later_movement(reference_runs):
    current = reference_runs[COVERAGE_VARIANT_IDS[1]]
    audit = copy.deepcopy(current._audit)
    audit[480]['event_effects'][0]['affected_occupants'] = []
    assert assess(current, audit=audit)['status'] != 'constraint_evidence_observed'


def test_orders_before_delivered_warning_do_not_prove_a_reaction(reference_runs):
    current = reference_runs[COVERAGE_VARIANT_IDS[1]]
    orders = copy.deepcopy(list(current.ops.orders.values()))
    for order in orders:
        order['issued_at'] = 479
    assert assess(current, orders=orders)['status'] != 'constraint_evidence_observed'


def test_ignoring_occupied_centre_closure_leaves_unresolved_population():
    current = environment()
    current.reset(COVERAGE_VARIANT_IDS[1])
    while current.minute < 550:
        current.advance_time(60)
    assert current.ops.groups['population-1']['status'] == 'blocked'
    assert current.ops.groups['population-1']['destination_id'] == 'destination-1'
    assert current.outcomes()['unresolved_evacuations'] == 1
    assert assess(current)['status'] != 'constraint_evidence_observed'


@pytest.mark.parametrize('variant,parent', tuple(zip(COVERAGE_VARIANT_IDS, (EGRESS_CASE_IDS[2], EGRESS_CASE_IDS[4]))))
def test_new_profiles_preserve_parent_identity_and_reset_isolation(variant, parent):
    current = environment()
    current.reset(parent)
    original = copy.deepcopy(current._scenario)
    current.reset(variant)
    assert current._scenario['parent_scenario_sha256'] == original['sha256']
    assert current._scenario['sha256'] != original['sha256']
    assert counterfactual_case(current)['status'] == 'demonstrated'
    assert current._audit[0]['event_effects'] == []
    assert 'closure_affects_occupants' not in original['profile']
    current.reset(parent)
    assert current._scenario == original


@pytest.mark.parametrize('variant,minute', tuple(zip(COVERAGE_VARIANT_IDS, (90, 480))))
def test_checkpoint_crosses_the_actual_restriction_or_closure_boundary(tmp_path, variant, minute):
    current = environment()
    observation, unused = current.reset(variant)
    while current.minute < minute - 1:
        observation, unused_reward, unused_terminated, unused_truncated, unused_info = current.advance_time(
            min(60, minute - 1 - current.minute))
    path = tmp_path / 'private' / (variant + '.json.gz')
    current.save_checkpoint(path)
    restored = environment()
    assert restored.restore(json.loads(gzip.decompress(path.read_bytes()))) == observation
    for unused in range(12):
        assert current.advance_time(1) == restored.advance_time(1)
    assert current._audit == restored._audit
    assert current._audit[minute]['event_effects']


@pytest.mark.parametrize('variant', COVERAGE_VARIANT_IDS)
def test_http_replays_actual_coverage_queries_and_orders(reference_runs, variant):
    pytest.importorskip('wildfire_openenv')
    from fastapi.testclient import TestClient
    from eval.incident_day_server import create_app
    from wildfire_openenv.incident_day import IncidentDayEnvironment

    current = reference_runs[variant]
    remote = IncidentDayEnvironment(current.inputs, current.network.document,
        expected_inputs_sha256=current.inputs['sha256'], expected_network_sha256=current.network.sha256)
    with TestClient(create_app(remote), base_url='http://127.0.0.1:8005', client=('127.0.0.1', 12345)) as client:
        response = client.post('/reset', json=dict(scenario_id=current._scenario['id']))
        assert response.status_code == 200
        assert response.json()['data'] == current.trajectory()['initial_observation']
        for transition in current.trajectory()['transitions']:
            for query in current._query_log:
                if query['before_decision'] != remote.core.decision:
                    continue
                endpoint = '/routes' if query['tool'] == 'route_options' else '/evacuation-options'
                response = client.post(endpoint, json=query['arguments'])
                assert response.status_code == 200
                assert digest(response.json()) == query['result_sha256']
            response = client.post('/step', json=dict(action=transition['action']))
            assert response.status_code == 200
            assert digest(response.json()['data']) == transition['observation_sha256']
    assert remote.core._audit == current._audit
