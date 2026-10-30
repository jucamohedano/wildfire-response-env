import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'envs/wildfire_containment_v1/src'))
from wildfire_containment_v1 import ContainmentKernel


EDGE = [[0, 0], [1, 0]]


def kernel(**overrides):
    config = dict(work_gain=2, max_strength=5, decay=0.5, wind_factor=1, report_delay=2)
    config.update(overrides)
    return ContainmentKernel(**config)


def assignment(identifier='crew-1', edge=EDGE, **overrides):
    result = dict(resource_id=identifier, edge=edge, kind='crew', ready=True,
                  work_remaining=10, water_litres=0, water_cost=0)
    result.update(overrides)
    return result


def attack(source=(0, 0), target=(1, 0), pressure=1):
    return dict(source=source, target=target, pressure=pressure)


def tick(k, minute, assignments=(), attacks=(), wind=(0, 0), suppressing=()):
    return k.tick(minute, assignments=assignments, attacks=attacks, wind=wind, suppressing=suppressing)


def test_hold_decay_directional_wind_and_irreversible_breach():
    k = kernel()
    first = tick(k, 1, [assignment()], [attack()], wind=(1, 0))
    # Equal pressure breaches; reversing the wind does not increase pressure.
    assert first['crossings'][0]['blocked'] is False
    assert first['costs'] == [dict(resource_id='crew-1', work_minutes=1, water_litres=0)]
    assert tick(k, 2, [assignment()], [attack()], wind=(-1, 0)) == {
        'costs': [], 'crossings': [dict(source=[0, 0], target=[1, 0], blocked=False)]}
    other = kernel()
    assert tick(other, 1, [assignment()], [attack()], wind=(-1, 0))['crossings'][0]['blocked']
    assert tick(other, 2, attacks=[attack()], wind=(0, 0))['crossings'][0]['blocked']
    assert not tick(other, 3, attacks=[attack()], wind=(0, 0))['crossings'][0]['blocked']


def test_finite_resources_and_no_simultaneous_suppression_are_atomic():
    k = kernel()
    before = k.checkpoint()
    invalid = [
        ([assignment(work_remaining=0)], ()),
        ([assignment(kind='engine', water_litres=9, water_cost=10)], ()),
        ([assignment(), assignment()], ()),
        ([assignment()], ('crew-1',)),
        ([assignment(ready=False)], ()),
        ([assignment(kind='aircraft')], ()),
    ]
    for assignments, suppressing in invalid:
        with pytest.raises(ValueError):
            tick(k, 1, assignments, suppressing=suppressing)
        assert k.checkpoint() == before
    result = tick(k, 1, [assignment(kind='engine', water_litres=10, water_cost=10)])
    assert result['costs'][0]['water_litres'] == 10
    with pytest.raises(ValueError):
        tick(k, 2, [assignment(kind='engine', water_litres=0, water_cost=10)])


def test_reports_are_delayed_observations_and_private_reads_do_not_advance():
    k = kernel()
    tick(k, 1, [assignment()], [attack(pressure=10)])
    assert k.reports() == []
    k.observe(EDGE, status='unknown')
    checkpoint = k.checkpoint()
    assert k.reports() == []
    assert k.checkpoint() == checkpoint
    tick(k, 2)
    assert k.reports() == []
    tick(k, 3)
    assert k.reports() == [dict(edge=EDGE, status='unknown', observed_at=1, received_at=3)]
    result = k.reports()
    result[0]['status'] = 'breached'
    assert k.reports()[0]['status'] == 'unknown'


def test_checkpoint_json_roundtrip_and_deterministic_continuation():
    original = kernel()
    tick(original, 1, [assignment()])
    original.observe(EDGE, status='holding')
    restored = ContainmentKernel.restore(json.loads(json.dumps(original.checkpoint())))
    for minute, wind in [(2, (0, 0)), (3, (5, 0)), (4, (0, 0))]:
        assert tick(original, minute, attacks=[attack()], wind=wind) == tick(restored, minute, attacks=[attack()], wind=wind)
        assert original.reports() == restored.reports()
        assert original.checkpoint() == restored.checkpoint()
    invalid = copy.deepcopy(original.checkpoint())
    invalid['minute'] = 99
    with pytest.raises(ValueError):
        ContainmentKernel.restore(invalid)


def test_invalid_crossings_and_time_do_not_change_state():
    k = kernel()
    before = k.checkpoint()
    for kwargs in [dict(attacks=[attack(target=(1, 1))]),
                   dict(attacks=[attack(), attack()]),
                   dict(wind=(float('nan'), 0)),
                   dict(assignments=[assignment(edge=[[0, 0], [0, 0]])])]:
        with pytest.raises(ValueError):
            tick(k, 1, **kwargs)
        assert k.checkpoint() == before
    with pytest.raises(ValueError):
        tick(k, 2)


def test_order_independent_assignments_and_strength_cap():
    a, b = kernel(), kernel()
    units = [assignment('a'), assignment('b'), assignment('c')]
    assert tick(a, 1, units) == tick(b, 1, list(reversed(units)))
    assert a.checkpoint() == b.checkpoint()
    assert a.checkpoint()['lines'][0]['strength'] == 5


@pytest.fixture
def day_inputs():
    from wildfire_core.incident_day import fixture_inputs
    return fixture_inputs()[0]


def actual_fire(day_inputs, sources, available):
    from envs.wildfire_response.incident_day.scenarios import DayFire
    fire = DayFire(day_inputs, 123)
    fire.set_wind(x=0, y=0)
    fire.spread_multiplier = 1
    fire.rules['spread_factor'] = 4
    fire.rules['ignition_probability_per_eligible_cell'] = 100
    for row in fire.cells:
        for cell in row:
            cell.update(fuel=0., heat=0., age=0, wet=0)
    for x, y in available:
        fire.cells[y][x].update(fuel=1., heat=0., age=0, wet=0)
    for x, y in sources:
        fire.cells[y][x].update(heat=1., age=1)
    return fire


def contained(fire, **kwargs):
    from wildfire_containment_v1.fire import ContainmentDayFire
    return ContainmentDayFire.from_day_fire(fire, containment=kernel(decay=0), pressure_scale=1, **kwargs)


def defense(identifier, edge):
    return assignment(identifier, edge=[list(point) for point in edge])


def test_real_fire_blocks_direct_crossing_and_preserves_rng_draw(day_inputs):
    source, target = (8, 8), (9, 8)
    baseline = actual_fire(day_inputs, [source], [source, target])
    protected = contained(baseline)
    baseline.step()
    costs = protected.step_with_containment(assignments=[defense('crew', (source, target))])
    assert baseline.cells[8][9]['heat'] > 0
    assert protected.cells[8][9]['heat'] == 0
    assert costs[0]['work_minutes'] == 1
    assert baseline.rng.getstate() == protected.rng.getstate()
    assert baseline.suppression_rng.getstate() == protected.suppression_rng.getstate()


def test_actual_diagonal_requires_both_paths_defended(day_inputs):
    source, target = (8, 8), (9, 9)
    horizontal, vertical = (9, 8), (8, 9)
    baseline = actual_fire(day_inputs, [source], [source, target, horizontal, vertical])
    one_path = contained(baseline)
    two_paths = contained(baseline)
    one_path.step_with_containment(assignments=[defense('a', (source, horizontal))])
    two_paths.step_with_containment(assignments=[defense('a', (source, horizontal)),
                                                 defense('b', (source, vertical))])
    assert one_path.cells[9][9]['heat'] > 0
    assert two_paths.cells[9][9]['heat'] == 0
    # Every candidate target gets the same draw even when both paths are closed.
    assert one_path.rng.getstate() == two_paths.rng.getstate()


def test_alternate_ignition_source_survives_a_blocked_candidate(day_inputs):
    west, north, target = (8, 9), (9, 8), (9, 9)
    baseline = actual_fire(day_inputs, [west, north], [west, north, target])
    fire = contained(baseline)
    fire.step_with_containment(assignments=[defense('a', (west, target))])
    assert fire.cells[9][9]['heat'] > 0


def test_no_lines_matches_dayfire_across_weather_and_rng(day_inputs):
    from envs.wildfire_response.incident_day.scenarios import DayFire
    for seed in (0, 37, 802):
        original = DayFire(day_inputs, seed)
        original.set_wind(x=1, y=0)
        original.add_fire(8, 8)
        fire = contained(original)
        for minute in range(45):
            if minute == 18:
                original.set_wind(x=-1, y=2)
                fire.set_wind(x=-1, y=2)
            original.step()
            fire.step()
            assert fire.checkpoint()['fire'] == original.checkpoint()


def test_fire_and_kernel_checkpoint_replay_and_tamper(day_inputs):
    from wildfire_containment_v1.fire import ContainmentDayFire
    source, target = (8, 8), (9, 8)
    base = actual_fire(day_inputs, [source], [source, target])
    fire = contained(base)
    fire.step_with_containment(assignments=[defense('a', (source, target))])
    fire.observe_line([source, target], status='holding')
    saved = json.loads(json.dumps(fire.checkpoint()))
    resumed = ContainmentDayFire.restore(saved)
    for _ in range(6):
        assert fire.step_with_containment() == resumed.step_with_containment()
        assert json.dumps(fire.checkpoint(), sort_keys=True) == json.dumps(resumed.checkpoint(), sort_keys=True)
        assert fire.reports() == resumed.reports()
    assert resumed.reports()[0]['status'] == 'holding'
    saved['fire']['state']['tick'] += 1
    with pytest.raises(ValueError):
        ContainmentDayFire.restore(saved)


def test_invalid_fire_assignment_does_not_advance_fire_or_rng(day_inputs):
    fire = contained(actual_fire(day_inputs, [(8, 8)], [(8, 8), (9, 8)]))
    before = fire.checkpoint()
    with pytest.raises(ValueError):
        fire.step_with_containment(assignments=[assignment(work_remaining=0)])
    assert fire.checkpoint() == before
    with pytest.raises(ValueError):
        fire.step_with_containment(assignments=[assignment(edge=[[-1, 0], [0, 0]])])
    assert fire.checkpoint() == before
    with pytest.raises(ValueError):
        fire.step_with_containment(assignments=[assignment()], suppressing=['crew-1'])
    assert fire.checkpoint() == before


def test_actual_fire_wind_drives_breach_and_then_spread(day_inputs):
    source, target = (8, 8), (9, 8)
    baseline = actual_fire(day_inputs, [source], [source, target])
    downwind = contained(baseline)
    upwind = contained(baseline)
    for fire, wind in ((downwind, 1), (upwind, -1)):
        fire.set_wind(x=wind, y=0)
        fire.cells[8][8]['age'] = fire.spread_interval(1, 0) - 1
        fire.step_with_containment(assignments=[defense('a', (source, target))])
    assert downwind.cells[8][9]['heat'] > 0
    assert upwind.cells[8][9]['heat'] == 0
    assert downwind.checkpoint()['containment']['lines'][0]['breached']
    assert not upwind.checkpoint()['containment']['lines'][0]['breached']


def test_restore_rejects_inconsistent_clocks_even_with_valid_digest(day_inputs):
    from wildfire_containment_v1.fire import ContainmentDayFire
    from wildfire_containment_v1.kernel import _digest
    fire = contained(actual_fire(day_inputs, [(8, 8)], [(8, 8), (9, 8)]))
    fire.step()
    value = fire.checkpoint()
    value['start_tick'] -= 1
    value['sha256'] = _digest({k: v for k, v in value.items() if k != 'sha256'})
    with pytest.raises(ValueError, match='clocks'):
        ContainmentDayFire.restore(value)


def test_cumulative_pressure_wear_wind_and_explicit_restart():
    down = kernel(work_gain=4, max_strength=8, decay=0, wind_factor=1, wear_factor=.25)
    up = kernel(work_gain=4, max_strength=8, decay=0, wind_factor=1, wear_factor=.25)
    tick(down, 1, [assignment()])
    tick(up, 1, [assignment()])
    for minute in (2, 3):
        assert tick(down, minute, attacks=[attack(pressure=1)], wind=(1, 0))['crossings'][0]['blocked']
        assert tick(up, minute, attacks=[attack(pressure=1)], wind=(-1, 0))['crossings'][0]['blocked']
    assert down.checkpoint()['lines'][0]['strength'] == 3
    assert up.checkpoint()['lines'][0]['strength'] == 3.5
    for minute in range(4, 8):
        tick(down, minute, attacks=[attack(pressure=1)], wind=(1, 0))
    assert down.checkpoint()['lines'][0]['breached']
    assert tick(down, 8, [assignment()])['costs'] == []
    down.restart_line(EDGE)
    assert tick(down, 9, [assignment()])['costs']
    assert not down.checkpoint()['lines'][0]['breached']


def test_decay_exhaustion_requires_restart():
    k = kernel(work_gain=1, decay=1)
    tick(k, 1, [assignment()])
    assert tick(k, 2, [assignment()])['costs'] == []
    assert k.checkpoint()['lines'][0]['breached']
    k.restart_line(EDGE)
    assert tick(k, 3, [assignment()])['costs']


def make_adapter(day_inputs):
    from wildfire_core.incident_day import fixture_inputs
    from wildfire_containment_v1.incident_day import ContainmentIncidentDayEnv
    inputs, network = fixture_inputs()
    return ContainmentIncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
        expected_network_sha256=network['sha256'],
        containment_config=dict(work_gain=2, max_strength=10, decay=0,
                                wind_factor=1, wear_factor=.05, report_delay=2), pressure_scale=1)


@pytest.mark.parametrize('side', [0, 1])
def test_incident_day_authorized_hold_consumes_shared_resources_reports_and_replays(day_inputs, side):
    env = make_adapter(day_inputs)
    env.reset('incident-day-dev-000')
    option = env.route_options('engine-1')['options'][1]
    env.step({'requests': [dict(resource_id='engine-1', sector_id='incident', approach_id=option['id'])]})
    while not env.get_situation()['offers']:
        env.advance_time(1)
    offer = env.get_situation()['offers'][0]
    options = env.propose_hold(offer['id'])
    assert options[0]['edge'] == options[1]['edge']
    assert {tuple(option['protected_cell']) for option in options[:2]} == {tuple(point) for point in options[0]['edge']}
    assert options[0]['id'] != options[1]['id']
    assert env.propose_hold(offer['id']) == options
    pending = make_adapter(day_inputs)
    pending.restore(json.loads(json.dumps(env.checkpoint())))
    assert pending.propose_hold(offer['id']) == options
    proposal = options[side]
    with pytest.raises(ValueError):
        env.authorize_hold(offer['id'], 'invented')
    env.authorize_hold(offer['id'], proposal['id'])
    assert env.ops.resources['engine-1']['state'] == 'mobilizing'
    assert env._missions['engine-1']['edge'] == proposal['edge']
    assert env._missions['engine-1']['protected_cell'] == proposal['protected_cell']
    saved = json.loads(json.dumps(env.checkpoint()))
    other = make_adapter(day_inputs)
    assert other.restore(saved) == env.get_situation()
    initial_water = env.ops.resources['engine-1']['water_litres']
    initial_duty = env.ops.resources['engine-1']['work_remaining']
    while not env.fire.checkpoint()['containment']['lines']:
        assert env.minute < 70 and not env.truncated
        assert env.advance_time(1) == other.advance_time(1)
    unit = env.ops.resources['engine-1']
    assert unit['work_remaining'] == initial_duty - 1
    assert unit['water_litres'] == initial_water - env.ops.profile['engine_litres_per_minute']
    assert not any(item['resource_id'] == 'engine-1' for item in env._audit[-1]['applied_work'])
    assert env._containment_audit[-1]['resource_id'] == 'engine-1'
    with pytest.raises(RuntimeError, match='not implemented'):
        env.trajectory()
    assert not any(report['kind'] == 'containment_status' for report in env.get_situation()['reports'])
    assert 'strength' not in json.dumps(env.get_situation())
    # Checkpoint while a report is still in flight and while the assignment is active.
    resumed = make_adapter(day_inputs)
    resumed.restore(json.loads(json.dumps(env.checkpoint())))
    for _ in range(8):
        assert env.advance_time(1) == other.advance_time(1) == resumed.advance_time(1)
        assert env.checkpoint() == other.checkpoint() == resumed.checkpoint()
    assert env.ops.resources['engine-1']['water_litres'] == 0
    assert env.ops.resources['engine-1']['state'] != 'working'
    reports = [r for r in env.get_situation()['reports'] if r['kind'] == 'containment_status']
    assert reports and reports[0]['observed_at'] < reports[0]['received_at']
    assert reports[0]['payload']['proposal_id'] == proposal['id']
    assert 'strength' not in json.dumps(reports)
    # Controlled synthetic spread probe after the actual mission: place a single
    # burning source on the completed edge, with no alternate burning sources.
    from envs.wildfire_response.incident_day.scenarios import DayFire
    target = proposal['protected_cell']
    source = next(point for point in proposal['edge'] if point != target)
    for row in env.fire.cells:
        for cell in row:
            cell.update(heat=0., wet=0, age=0)
    for x, y in (source, target):
        env.fire.cells[y][x].update(fuel=1.)
    sx, sy = source
    tx, ty = target
    env.fire.cells[sy][sx].update(heat=1., age=1)
    env.fire.set_wind(x=0, y=0)
    env.fire.spread_multiplier = 1
    env.fire.rules['spread_factor'] = 4
    env.fire.rules['ignition_probability_per_eligible_cell'] = 100
    baseline = DayFire.restore(env.fire.checkpoint()['fire'])
    baseline.step()
    assert baseline.cells[ty][tx]['heat'] > 0
    env.advance_time(1)
    assert env.fire.cells[ty][tx]['heat'] == 0
    corrupted = copy.deepcopy(env.checkpoint())
    corrupted['pressure_scale'] += 1
    with pytest.raises(ValueError):
        make_adapter(day_inputs).restore(corrupted)


def test_adapter_refuses_aircraft_and_stale_proposals(day_inputs):
    env = make_adapter(day_inputs)
    env.reset('incident-day-dev-000')
    with pytest.raises(ValueError):
        env.propose_hold('not-a-delivered-offer')
    with pytest.raises(ValueError):
        env.authorize_hold('not-a-delivered-offer', 'invented')
    assert env.minute == 0


def test_adapter_without_hold_matches_public_incident_lifecycle(day_inputs):
    from wildfire_core.incident_day import IncidentDayEnv, fixture_inputs
    inputs, network = fixture_inputs()
    original = IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                              expected_network_sha256=network['sha256'])
    adapted = make_adapter(day_inputs)
    assert original.reset('incident-day-dev-000') == adapted.reset('incident-day-dev-000')
    for action in ({'advance_minutes': 5}, {'status_requests': [dict(center_id='center-0', subject='resources')]},
                   {'advance_minutes': 15}, {'advance_minutes': 10}):
        assert original.step(action) == adapted.step(action)
        assert original.fire.checkpoint() == adapted.fire.checkpoint()['fire']


def test_adapter_rejects_real_aircraft_offer_and_legacy_exports(day_inputs):
    env = make_adapter(day_inputs)
    env.reset('incident-day-dev-000')
    option = env.route_options('plane-0')['options'][0]
    env.step({'requests': [dict(resource_id='plane-0', sector_id='incident', approach_id=option['id'])]})
    while not env.get_situation()['offers']:
        env.advance_time(1)
    offer = env.get_situation()['offers'][0]
    with pytest.raises(ValueError, match='ground'):
        env.propose_hold(offer['id'])
    with pytest.raises(RuntimeError, match='not implemented'):
        env.verify_episode()


def test_wear_respects_explicit_protected_side():
    k = kernel(work_gain=5, max_strength=10, decay=0, wear_factor=.5)
    tick(k, 1, [assignment(protected_cell=[1, 0])])
    tick(k, 2, attacks=[attack(source=(1, 0), target=(0, 0), pressure=1)], wind=(-1, 0))
    assert k.checkpoint()['lines'][0]['strength'] == 5
    tick(k, 3, attacks=[attack(pressure=1)], wind=(1, 0))
    assert k.checkpoint()['lines'][0]['strength'] == 4
    restored = ContainmentKernel.restore(json.loads(json.dumps(k.checkpoint())))
    assert tick(restored, 4, attacks=[attack()], wind=(1, 0)) == tick(k, 4, attacks=[attack()], wind=(1, 0))
    tick(k, 5, [assignment(protected_cell=[0, 0])])
    assert k.checkpoint()['lines'][0]['protected_cell'] == (0, 0)
    with pytest.raises(ValueError, match='Conflicting protected side'):
        tick(k, 6, [assignment('a', protected_cell=[0, 0]), assignment('b', protected_cell=[1, 0])])


def test_opposite_protected_sides_swap_wind_wear_and_replay():
    attacks = [attack(), attack(source=(1, 0), target=(0, 0))]
    for wind in ((1, 0), (-1, 0)):
        east = kernel(work_gain=5, max_strength=10, decay=0, wear_factor=.5)
        west = kernel(work_gain=5, max_strength=10, decay=0, wear_factor=.5)
        tick(east, 1, [assignment(protected_cell=[1, 0])])
        tick(west, 1, [assignment(protected_cell=[0, 0])])
        checkpoint = json.loads(json.dumps(west.checkpoint()))
        restored = ContainmentKernel.restore(checkpoint)
        tick(east, 2, attacks=attacks, wind=wind)
        assert tick(west, 2, attacks=attacks, wind=wind) == tick(restored, 2, attacks=attacks, wind=wind)
        assert west.checkpoint() == restored.checkpoint()
        east_strength = east.checkpoint()['lines'][0]['strength']
        west_strength = west.checkpoint()['lines'][0]['strength']
        if wind[0] > 0:
            assert east_strength < west_strength
        else:
            assert west_strength < east_strength


def test_adapter_rejects_weather_style_hooks_before_mutation(day_inputs, monkeypatch):
    from envs.wildfire_response.incident_day import environment as day
    from envs.wildfire_response.incident_day.events import EventBus
    env = make_adapter(day_inputs)
    env.reset('incident-day-dev-000')
    before = env.checkpoint()
    with monkeypatch.context() as patch:
        patch.setattr(day, 'DayFire', object())
        with pytest.raises(RuntimeError, match='weather runtime'):
            env.step({'advance_minutes': 1})
        with pytest.raises(RuntimeError, match='weather runtime'):
            make_adapter(day_inputs)
    with monkeypatch.context() as patch:
        patch.setattr(EventBus, 'tick', lambda *args: None)
        with pytest.raises(RuntimeError, match='weather runtime'):
            env.step({'advance_minutes': 1})
    assert env.checkpoint() == before


def test_adapter_rejects_nonempty_uncertain_land_guard(day_inputs, monkeypatch):
    from envs.wildfire_response.incident_day.environment import IncidentDayEnv
    original = IncidentDayEnv.__init__
    def set_unresolved(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self._uncertain_land = {(0, 0)}
    monkeypatch.setattr(IncidentDayEnv, '__init__', set_unresolved)
    with pytest.raises(ValueError, match='Unresolved-land'):
        make_adapter(day_inputs)


def test_actual_weather_runtime_context_is_rejected(day_inputs, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'envs/wildfire_control_v3/src'))
    from eval.incident_weather_runtime_v1 import IncidentWeatherRuntimeV1
    from eval.incident_eval_bridge_v1 import CATALOG_VERSION
    from envs.wildfire_response.incident_day.scenarios import build_scenario
    from envs.wildfire_response.regional.ops.generator import digest
    from wildfire_core.incident_day import fixture_inputs
    inputs, network = fixture_inputs()
    env = make_adapter(day_inputs)
    env.reset('incident-day-dev-000')
    saved = env.checkpoint()
    case = build_scenario(env.inputs, env.network, 'incident-day-egress-v1-000')
    case.update(id='containment-weather-test-000', seed=8140000, warmup_minutes=60,
                weather_schedule=[dict(minute=0, wind=case['wind'], temperature_c=27,
                                       relative_humidity_pct=40, rain_mm_per_hour=0)])
    case['events'] = [event for event in case['events'] if event['kind'] != 'wind']
    case['sha256'] = digest({key: value for key, value in case.items() if key != 'sha256'})
    catalog = dict(version=CATALOG_VERSION, inputs_sha256=inputs['sha256'],
                   network_sha256=network['sha256'], cases=[case])
    catalog['sha256'] = digest(catalog)
    draft = IncidentWeatherRuntimeV1(catalog, expected_pins={})
    with IncidentWeatherRuntimeV1(catalog, expected_pins=draft.pins):
        with pytest.raises(RuntimeError, match='weather runtime'):
            env.step({'advance_minutes': 1})
        with pytest.raises(RuntimeError, match='weather runtime'):
            make_adapter(day_inputs)
    assert env.checkpoint() == saved


@pytest.mark.parametrize('replacement_kind', ['ordinary', 'hold'])
def test_declined_replacement_preserves_hold_mission_and_reservation(day_inputs, replacement_kind):
    env = make_adapter(day_inputs)
    env.reset('incident-day-dev-000')
    options = env.route_options('engine-1')['options']
    for option in (options[1], options[2]):
        env.step({'requests': [dict(resource_id='engine-1', sector_id='incident', approach_id=option['id'])]})
        while len(env.get_situation()['offers']) < (1 if option is options[1] else 2):
            assert env.minute < 25
            env.advance_time(1)
    offers = env.get_situation()['offers']
    first = next(o for o in offers if o['approach_id'] == options[1]['id'])
    second = next(o for o in offers if o['approach_id'] == options[2]['id'])
    proposal = env.propose_hold(first['id'])[1]
    env.authorize_hold(first['id'], proposal['id'])
    assert env.ops.resources['engine-1']['state'] == 'mobilizing'
    old_mission = copy.deepcopy(env._missions['engine-1'])
    old_reservation = copy.deepcopy(env._requested['engine-1'])
    if replacement_kind == 'ordinary':
        env.step({'authorizations': [second['id']]})
    else:
        replacement = env.propose_hold(second['id'])[0]
        env.authorize_hold(second['id'], replacement['id'])
    assert env._missions['engine-1'] == old_mission
    assert env._requested['engine-1'][:len(old_reservation)] == old_reservation
    if replacement_kind == 'ordinary':
        assert env._requested['engine-1'] == old_reservation
    else:
        assert env._requested['engine-1'][-1]['edge'] == replacement['edge']
    resumed = make_adapter(day_inputs)
    resumed.restore(json.loads(json.dumps(env.checkpoint())))
    while not env._containment_audit:
        assert env.minute < 70 and not env.truncated
        assert env.advance_time(1) == resumed.advance_time(1)
    assert env._containment_audit[0]['edge'] == proposal['edge']
    assert env.fire.checkpoint()['containment']['lines'][0]['protected_cell'] == tuple(proposal['protected_cell'])
    assert not any(work['resource_id'] == 'engine-1' for work in env._audit[-1]['applied_work'])
    assert env.checkpoint() == resumed.checkpoint()


def test_full_day_explicit_hold_calls_replay_on_cpu(day_inputs):
    left, right = make_adapter(day_inputs), make_adapter(day_inputs)
    assert left.reset('incident-day-dev-000') == right.reset('incident-day-dev-000')
    routes = [env.route_options('engine-1')['options'][1] for env in (left, right)]
    assert routes[0] == routes[1]
    request = {'requests': [dict(resource_id='engine-1', sector_id='incident', approach_id=routes[0]['id'])]}
    assert left.step(request) == right.step(request)
    while not left.get_situation()['offers']:
        assert left.advance_time(1) == right.advance_time(1)
    offer = left.get_situation()['offers'][0]
    assert offer == right.get_situation()['offers'][0]
    first = left.propose_hold(offer['id'])
    assert first == right.propose_hold(offer['id'])
    assert left.authorize_hold(offer['id'], first[1]['id']) == right.authorize_hold(offer['id'], first[1]['id'])
    while not left.terminated and not left.truncated:
        assert left.advance_time(60) == right.advance_time(60)
    assert left.terminated and not left.truncated and left.minute == 1440
    assert len(left._containment_audit) == 5
    assert left.checkpoint() == right.checkpoint()
    with pytest.raises(RuntimeError, match='not implemented'):
        left.trajectory()
    with pytest.raises(RuntimeError, match='not implemented'):
        left.verify_episode()


def test_release_reservation_waits_for_delivered_ack_and_failed_release_keeps_mission(day_inputs, monkeypatch):
    env = make_adapter(day_inputs)
    env.reset('incident-day-dev-000')
    route = env.route_options('engine-1')['options'][1]
    env.step({'requests': [dict(resource_id='engine-1', sector_id='incident', approach_id=route['id'])]})
    while not env.get_situation()['offers']:
        env.advance_time(1)
    offer = env.get_situation()['offers'][0]
    proposal = env.propose_hold(offer['id'])[0]
    env.authorize_hold(offer['id'], proposal['id'])
    mission = copy.deepcopy(env._missions['engine-1'])
    reservation = copy.deepcopy(env._requested['engine-1'])
    with monkeypatch.context() as patch:
        patch.setattr(type(env.ops), 'release', lambda self, *args: False)
        env.step({'releases': ['engine-1']})
    assert env._missions['engine-1'] == mission
    assert env._requested['engine-1'] == reservation
    env.step({'releases': ['engine-1']})
    assert 'engine-1' not in env._missions
    assert env._requested['engine-1'] == reservation
    while 'engine-1' in env._requested:
        assert env.minute < 30
        env.advance_time(1)
    reports = [r for r in env.get_situation()['reports']
               if r['kind'] == 'release_accepted' or (r['kind'] == 'resource_status'
                   and r['payload'].get('resource', {}).get('id') == 'engine-1'
                   and r['payload']['resource']['state'] in ('returning', 'refilling', 'available'))]
    assert reports and all(r['received_at'] > r['observed_at'] for r in reports)


def _two_offers_for_engine(env, options):
    result = []
    for option in options:
        env.step({'requests': [dict(resource_id='engine-1', sector_id='incident', approach_id=option['id'])]})
        while len(env.get_situation()['offers']) < len(result) + 1:
            assert env.minute < 25
            env.advance_time(1)
        result = env.get_situation()['offers']
    return result


@pytest.mark.parametrize('exhaustion', ['water', 'duty'])
def test_auto_return_reservation_waits_for_public_evidence_then_fresh_hold(day_inputs, exhaustion):
    env = make_adapter(day_inputs)
    env.reset('incident-day-dev-000')
    # A synthetic constrained handover keeps the second offer live at return.
    field, remaining = ('water_litres', 1000) if exhaustion == 'water' else ('work_remaining', 1)
    env.ops.resources['engine-1'][field] = remaining
    env._known_resources['engine-1'][field] = remaining
    original_route = env.route_options('engine-1')['options'][1]
    first, second = _two_offers_for_engine(env, [original_route, original_route])
    proposal = env.propose_hold(first['id'])[0]
    env.authorize_hold(first['id'], proposal['id'])
    # The second offer is deliberately still live during the automatic return.
    competing = next(p for p in env.propose_hold(second['id'])
                     if p['edge'] == proposal['edge'] and p['protected_cell'] == proposal['protected_cell'])
    while env.ops.resources['engine-1']['state'] != 'returning':
        assert env.minute < 70
        env.advance_time(1)
    assert env.ops.resources['engine-1'][field] == 0
    assert env._known_resources['engine-1']['state'] in ('on_scene', 'working')
    with pytest.raises(ValueError, match='already has a hold'):
        env.authorize_hold(second['id'], competing['id'])
    before_delivery = make_adapter(day_inputs)
    before_delivery.restore(json.loads(json.dumps(env.checkpoint())))
    with pytest.raises(ValueError, match='already has a hold'):
        before_delivery.authorize_hold(second['id'], competing['id'])
    while env._requested.get('engine-1'):
        assert env.minute < 70
        assert env.advance_time(1) == before_delivery.advance_time(1)
    report = next(r for r in env.bus.reports if r['kind'] == 'resource_status'
                  and r['payload'].get('resource', {}).get('id') == 'engine-1'
                  and r['payload']['resource']['state'] == 'returning')
    assert report['received_at'] > report['observed_at']
    assert env.checkpoint() == before_delivery.checkpoint()
    # Wait for the public refill/availability report, then request a new offer
    # from an actual publicly proposed route to the same grid approach.
    while env._known_resources['engine-1']['state'] != 'available':
        assert env.minute < 180
        env.advance_time(1)
    assert env.ops.resources['engine-1']['water_litres'] > 0
    assert env.ops.resources['engine-1']['work_remaining'] > 0
    route = next(o for o in env.route_options('engine-1')['options']
                 if o['approach_grid'] == original_route['approach_grid'])
    prior_ids = {o['id'] for o in env.get_situation()['offers']}
    env.step({'requests': [dict(resource_id='engine-1', sector_id='incident', approach_id=route['id'])]})
    while True:
        fresh = [o for o in env.get_situation()['offers'] if o['id'] not in prior_ids]
        if fresh:
            break
        assert env.minute < 200
        env.advance_time(1)
    fresh_proposal = next(p for p in env.propose_hold(fresh[0]['id'])
                          if p['edge'] == proposal['edge'] and p['protected_cell'] == proposal['protected_cell'])
    env.authorize_hold(fresh[0]['id'], fresh_proposal['id'])
    assert env._missions['engine-1']['edge'] == proposal['edge']
    assert env.ops.resources['engine-1']['state'] == 'mobilizing'


def test_declined_hold_reservation_clears_only_on_delivered_decline(day_inputs):
    env = make_adapter(day_inputs)
    env.reset('incident-day-dev-000')
    route = env.route_options('engine-1')['options'][1]
    first, second = _two_offers_for_engine(env, [route, route])
    original = env.propose_hold(first['id'])[0]
    env.authorize_hold(first['id'], original['id'])
    replacement = next(p for p in env.propose_hold(second['id']) if p['edge'] != original['edge'])
    env.authorize_hold(second['id'], replacement['id'])
    assert len(env._requested['engine-1']) == 2
    declined_at = env.minute
    assert not any(r['kind'] == 'dispatch_declined' and r['observed_at'] == declined_at for r in env.bus.reports)
    resumed = make_adapter(day_inputs)
    resumed.restore(json.loads(json.dumps(env.checkpoint())))
    while not any(r['kind'] == 'dispatch_declined' and r['observed_at'] == declined_at for r in env.bus.reports):
        assert len(env._requested['engine-1']) == 2
        assert env.minute < declined_at + 10
        assert env.advance_time(1) == resumed.advance_time(1)
    report = next(r for r in env.bus.reports if r['kind'] == 'dispatch_declined' and r['observed_at'] == declined_at)
    assert report['received_at'] > report['observed_at']
    assert [item['edge'] for item in env._requested['engine-1']] == [original['edge']]
    assert env._missions['engine-1']['edge'] == original['edge']
    assert env.checkpoint() == resumed.checkpoint()


def test_late_public_evidence_does_not_retire_newer_hold(day_inputs):
    env = make_adapter(day_inputs)
    env.reset('incident-day-dev-000')
    route = env.route_options('engine-1')['options'][1]
    env.step({'requests': [dict(resource_id='engine-1', sector_id='incident', approach_id=route['id'])]})
    while not env.get_situation()['offers']:
        env.advance_time(1)
    offer = env.get_situation()['offers'][0]
    proposal = env.propose_hold(offer['id'])[0]
    env.authorize_hold(offer['id'], proposal['id'])
    before = copy.deepcopy(env._requested['engine-1'])
    old = before[0]['requested_at']
    resource = copy.deepcopy(env._known_resources['engine-1'])
    resource.update(state='available', observed_at=old)
    # Model late-delivered observations from before this authorization took effect.
    env._receive([dict(kind='resource_status', observed_at=old, received_at=env.minute,
                       payload=dict(resource=resource)),
                  dict(kind='release_accepted', observed_at=old, received_at=env.minute,
                       payload=dict(resource_id='engine-1')),
                  dict(kind='dispatch_declined', observed_at=old, received_at=env.minute,
                       payload=dict(resource_id='engine-1'))])
    assert env._requested['engine-1'] == before
