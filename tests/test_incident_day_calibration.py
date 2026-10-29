import copy
import json
import sys

import pytest

from eval.check_incident_day_calibration import main, run
from envs.wildfire_response.incident_day.baselines import DayDispatcher
from envs.wildfire_response.incident_day.calibration import run_probes
from wildfire_core.incident_day import IncidentDayEnv, fixture_inputs


class PublicRoutes:
    def evacuation_options(self, group_id, destination_id):
        minutes = {'west': 28, 'east': 14}[destination_id]
        return {'options': [{'id': destination_id + '-route', 'travel_minutes': minutes}]}


def observation(condition):
    return dict(minute=100, offers=[], sensor_memory=[], districts=[dict(id='g', exposed=False,
        status='unwarned', destination_id=None)], destinations=[dict(id='west', status='open'),
        dict(id='east', status='open')], reports=[dict(source='g', kind='resident_update', actionable=True,
        observed_at=99, received_at=100, payload={'condition': condition})], pending_resource_requests=[],
        resources=[], centers=[])


def test_dispatcher_uses_latest_public_condition_and_shortest_offered_route_once():
    dispatcher = DayDispatcher()
    clear = observation('clear')
    assert 'protection_orders' not in dispatcher.action(PublicRoutes(), clear)
    smoke = observation('nearby_smoke')
    action = dispatcher.action(PublicRoutes(), smoke)
    assert action['protection_orders'][0]['destination_id'] == 'east'
    assert 'protection_orders' not in dispatcher.action(PublicRoutes(), smoke)
    smoke['minute'] = 120
    assert 'protection_orders' not in dispatcher.action(PublicRoutes(), smoke)
    stale = copy.deepcopy(smoke)
    stale['reports'].append(dict(source='g', kind='resident_update', actionable=True,
        observed_at=100, received_at=101, payload={'condition': 'clear'}))
    stale['minute'] = 200
    assert 'protection_orders' not in dispatcher.action(PublicRoutes(), stale)


def test_dispatcher_respects_public_destination_capacity():
    obs = observation('nearby_smoke')
    obs['districts'][0]['count'] = 4
    obs['districts'].append(dict(id='other', count=4, destination_id='east', status='arrived'))
    obs['destinations'][1]['capacity'] = 5
    action = DayDispatcher().action(PublicRoutes(), obs)
    assert action['protection_orders'][0]['destination_id'] == 'west'


def test_blocked_public_order_reserves_destination_capacity():
    obs = observation('nearby_smoke')
    obs['districts'][0]['count'] = 4
    obs['districts'].append(dict(id='other', count=4, destination_id=None, status='blocked'))
    obs['destinations'][1]['capacity'] = 5
    obs['protection_orders'] = [dict(id='order-other', group_id='other', destination_id='east',
                                     action='evacuate', state='blocked', issued_at=90)]
    action = DayDispatcher().action(PublicRoutes(), obs)
    assert action['protection_orders'][0]['destination_id'] == 'west'


def test_new_closure_bypasses_cooldown_once_then_retries_after_cooldown():
    obs = observation('clear')
    obs['districts'][0].update(status='evacuating', destination_id=None)
    obs['destinations'][1].update(status='closed', observed_at=100)
    obs['protection_orders'] = [dict(id='order-original', group_id='g', destination_id='east',
                                     action='evacuate', state='executing', issued_at=99)]
    dispatcher = DayDispatcher()
    dispatcher.asked.add(('protection', 'g', 99))
    action = dispatcher.action(PublicRoutes(), obs)
    assert action['protection_orders'][0]['destination_id'] == 'west'
    assert 'protection_orders' not in dispatcher.action(PublicRoutes(), obs)
    obs['minute'] = 159
    assert 'protection_orders' not in dispatcher.action(PublicRoutes(), obs)
    obs['minute'] = 160
    retry = dispatcher.action(PublicRoutes(), obs)
    assert retry['protection_orders'][0]['destination_id'] == 'west'
    obs['minute'] = 161
    assert 'protection_orders' not in dispatcher.action(PublicRoutes(), obs)


@pytest.mark.parametrize('condition', ['clear', 'exposed'])
def test_open_pending_alternative_suppresses_repeated_closed_destination_redirect(condition):
    obs = observation(condition)
    obs['districts'][0].update(status='arrived', exposed=condition == 'exposed', destination_id='east')
    obs['destinations'][1].update(status='closed', observed_at=100)
    obs['protection_orders'] = [
        dict(id='old', group_id='g', destination_id='east', action='evacuate', state='blocked', issued_at=90),
        dict(id='new', group_id='g', destination_id='west', action='evacuate', state='issued', issued_at=99)]
    assert 'protection_orders' not in DayDispatcher().action(PublicRoutes(), obs)


@pytest.mark.parametrize('state', ['completed', 'cancelled', 'unable_to_comply'])
def test_inactive_old_order_does_not_create_false_closure(state):
    obs = observation('clear')
    obs['districts'][0].update(status='evacuating', destination_id=None)
    obs['destinations'][1].update(status='closed', observed_at=100)
    obs['protection_orders'] = [dict(id='old', group_id='g', destination_id='east',
                                     action='evacuate', state=state, issued_at=90)]
    assert 'protection_orders' not in DayDispatcher().action(PublicRoutes(), obs)


def test_exposure_at_arrival_can_trigger_relocation():
    obs = observation('exposed')
    obs['districts'][0].update(status='arrived', exposed=True, destination_id='east')
    action = DayDispatcher().action(PublicRoutes(), obs)
    assert action['protection_orders'][0]['destination_id'] == 'west'


def test_previously_exposed_group_at_clear_destination_does_not_hop():
    obs = observation('clear')
    obs['districts'][0].update(status='arrived', exposed=True, destination_id='east', observed_at=99)
    obs['reports'].insert(0, dict(source='g', kind='resident_update', actionable=True,
        observed_at=80, received_at=81, payload={'condition': 'exposed'}))
    dispatcher = DayDispatcher()
    assert 'protection_orders' not in dispatcher.action(PublicRoutes(), obs)
    obs['minute'] = 200
    assert 'protection_orders' not in dispatcher.action(PublicRoutes(), obs)


def test_old_prearrival_exposure_cannot_relocate_a_group_at_an_open_destination():
    obs = observation('exposed')
    obs['districts'][0].update(status='arrived', exposed=True, destination_id='east', observed_at=100)
    assert 'protection_orders' not in DayDispatcher().action(PublicRoutes(), obs)
    obs['districts'][0]['observed_at'] = 99
    obs['minute'] = 160
    assert 'protection_orders' not in DayDispatcher().action(PublicRoutes(), obs)


def test_closed_destination_can_still_trigger_relocation_without_recent_condition():
    obs = observation('clear')
    obs['districts'][0].update(status='arrived', exposed=True, destination_id='east')
    obs['destinations'][1]['status'] = 'closed'
    obs['minute'] = 200
    action = DayDispatcher().action(PublicRoutes(), obs)
    assert action['protection_orders'][0]['destination_id'] == 'west'


def test_probes_measure_causal_timing_and_bounded_sensitivities():
    probes = run_probes()
    assert probes == run_probes()
    assert probes['ground_setup_long']['work_minutes'][0] - probes['ground_setup_short']['work_minutes'][0] == 4
    assert probes['ground_blocked']['work_minutes'][0] > probes['ground_setup_short']['work_minutes'][0]
    assert len(probes['ground_low_stock']['work_minutes']) < len(probes['ground_setup_short']['work_minutes'])
    states = [item['state'] for item in probes['ground_setup_short']['trace']]
    assert states.index('travel') < states.index('on_scene') < states.index('working')
    assert states.index('refilling') < states.index('resting') < states.index('available')
    assert probes['ground_setup_short']['remaining_supply'] == 0
    assert probes['ground_low_supply']['trace'][-1]['state'] == 'needs_assistance'
    assert probes['ground_duty_limit']['trace'][-1]['state'] == 'off_duty'
    assert len(probes['aircraft_open']['drops']) == 1
    assert not probes['aircraft_window_closes']['drops'] and not probes['aircraft_wind_increases']['drops']
    assert not probes['aircraft_low_fuel']['dispatched'] and probes['aircraft_sufficient_fuel']['dispatched']
    assert probes['protection_early']['capacity_rejected'] and probes['protection_late']['capacity_rejected']
    assert probes['protection_early']['reservations']['g'] == 'b'
    assert not probes['protection_early']['trace'][4]['exposed']
    assert probes['protection_late']['trace'][4]['exposed']


def test_public_dispatcher_case001_paired_exposure_regression():
    inputs, network = fixture_inputs()
    results = {}
    for policy in ('passive', 'dispatcher'):
        env = IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                             expected_network_sha256=network['sha256'])
        obs, _ = env.reset('incident-day-dev-001')
        dispatcher = DayDispatcher(policy)
        while not (env.terminated or env.truncated):
            obs, _, _, _, _ = env.step(dispatcher.action(env, obs))
        assert env.terminated and not env.truncated
        results[policy] = env.outcomes()['new_exposed_population']
    assert results == {'passive': 0, 'dispatcher': 0}


def test_cli_report_records_budget_censoring_in_a_fresh_directory(tmp_path):
    output = tmp_path / 'report'
    report = run(output, ['incident-day-dev-001'], 1e-12)
    assert report['status'] == 'incomplete'
    assert [case['status'] for case in report['cases']] == ['censored', 'censored']
    assert all('new_exposed_population' not in case for case in report['cases'])
    assert json.loads((output / 'report.json').read_text())['version'] == report['version']
    assert 'Mechanism comparisons' in (output / 'report.html').read_text()
    with pytest.raises(FileExistsError):
        run(output, ['incident-day-dev-001'], 1)
    with pytest.raises(ValueError):
        run(tmp_path / 'nan', ['incident-day-dev-001'], float('nan'))


def test_cli_returns_nonzero_when_budget_censors(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['check_incident_day_calibration.py', '--output',
                                    str(tmp_path / 'censored'), '--wall-budget', '1e-12'])
    assert main() == 1


def test_cli_returns_nonzero_when_implementation_changes(monkeypatch):
    monkeypatch.setattr('eval.check_incident_day_calibration.run', lambda *args, **kwargs: dict(
        version='test', status='failed', cases=[], elapsed_seconds=0))
    monkeypatch.setattr(sys, 'argv', ['check_incident_day_calibration.py', '--output', '/tmp/unused-calibration'])
    assert main() == 1


def test_report_fails_when_implementation_changes(tmp_path, monkeypatch):
    digests = iter(['before', 'after'])
    monkeypatch.setattr('eval.check_incident_day_calibration.implementation_digest', lambda: next(digests))
    report = run(tmp_path / 'changed', ['incident-day-dev-001'], 1e-12)
    assert report['status'] == 'failed'
    assert not report['implementation_unchanged']
