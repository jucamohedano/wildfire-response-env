"""Early recovery is limited to source-map development profiles."""
import pytest

from envs.wildfire_response.incident_day.operations import Operations
from wildfire_core.mobility_routing import make_journey, make_leg


VERSIONS = ('wildfire-incident-day-realmap-development-profile-v2',)


def recovery(unit, minute):
    start, end = unit['position'], unit['base_position']
    return make_journey([make_leg('road', [start, end], 60)], start, end, 'return')


def environment(kind='crew', version=VERSIONS[0], planner=recovery):
    resource = dict(id='unit', kind=kind, position=[2000, 0], base_position=[0, 0],
                    state='working', available_at=0, duty_end_at=10,
                    total_work_remaining=10, work_remaining=10,
                    water_litres=100, capacity_litres=100)
    return Operations([resource], [], [], dict(version=version, require_return_within_shift=True),
                      return_planner=planner)


def tick(ops, minute, *, safe=True, moving=True, events=None):
    ops.tick(minute, wind=0, daylight=True, hazard_check=lambda position, kind: safe,
             movement_check=lambda journey, minute, kind: moving,
             suppress=lambda unit: False,
             emit=lambda source, kind, payload, **kw: events.append((kind, payload)) if events is not None else None)


@pytest.mark.parametrize('kind', ['crew', 'engine'])
@pytest.mark.parametrize('version', VERSIONS)
def test_unavailable_ground_work_starts_early_recovery(kind, version):
    ops = environment(kind, version)
    events = []
    tick(ops, 0, events=events)
    unit = ops.resources['unit']
    assert unit['state'] == 'returning'
    assert unit['position'] == [2000, 0]
    assert unit['journey']['origin'] == [2000, 0]
    assert unit['journey']['destination'] == [0, 0]
    assert unit['work_remaining'] == unit['total_work_remaining'] == 10
    assert [kind for kind, _ in events] == ['work_target_unavailable']
    tick(ops, 1)
    tick(ops, 2)
    assert unit['position'] == [0, 0]
    assert unit['state'] == 'available'


def test_invalid_recovery_leaves_ground_unit_needing_assistance():
    # The shift check can find a valid route, but it disappears before the
    # work-unavailable handler attempts recovery.
    calls = []
    def planner(unit, minute):
        calls.append(minute)
        return recovery(unit, minute) if len(calls) == 1 else None
    ops = environment(planner=planner)
    tick(ops, 0)
    assert ops.resources['unit']['state'] == 'needs_assistance'
    assert ops.resources['unit']['position'] == [2000, 0]


def test_unsafe_recovery_does_not_move_and_resumes_when_safe():
    ops = environment()
    events = []
    tick(ops, 0, events=events)
    tick(ops, 1, moving=False, events=events)
    unit = ops.resources['unit']
    assert unit['state'] == 'blocked'
    assert unit['blocked_state'] == 'returning'
    assert unit['position'] == [2000, 0]
    tick(ops, 2, safe=False, events=events)
    assert unit['position'] == [2000, 0]
    tick(ops, 3, events=events)
    assert unit['state'] == 'returning'
    assert unit['position'] == [1000, 0]
    assert [kind for kind, _ in events] == ['work_target_unavailable', 'movement_blocked', 'movement_resumed']


def test_expired_shift_continues_safe_recovery_but_cannot_dispatch():
    ops = environment()
    ops.resources['unit']['duty_end_at'] = 4
    tick(ops, 0)
    assert ops.resources['unit']['state'] == 'returning'
    for minute in range(1, 5):
        tick(ops, minute, moving=False)
    tick(ops, 5)
    tick(ops, 6)
    unit = ops.resources['unit']
    assert unit['position'] == [0, 0]
    assert unit['state'] == 'off_duty'
    assert not ops.dispatch('unit', 'incident', [0, 0], recovery(unit, 6), 6)


@pytest.mark.parametrize('version', ['wildfire-incident-day-realmap-profile-v2',
    'wildfire-incident-day-realmap-development-profile-v1'])
def test_legacy_profile_keeps_assistance_behavior(version):
    ops = environment(version=version)
    tick(ops, 0)
    assert ops.resources['unit']['state'] == 'needs_assistance'
    assert ops.resources['unit']['position'] == [2000, 0]


def outbound(ops):
    unit = ops.resources['unit']
    route = make_journey([make_leg('road', [[2000, 0], [3000, 0]], 60)],
                         [2000, 0], [3000, 0], 'outbound')
    route.update(departed_at=0, arrives_at=route['duration_minutes'])
    unit.update(state='travel', journey=route)
    return unit


@pytest.mark.parametrize('kind', ['engine', 'crew'])
def test_blocked_outbound_immediately_recovers_and_return_stays_safety_checked(kind):
    ops = environment(kind=kind)
    unit = outbound(ops)
    events = []
    tick(ops, 1, moving=False, events=events)
    assert unit['position'] == [2000, 0]
    assert unit['state'] == 'returning' and unit['journey']['destination'] == [0, 0]
    assert [item[0] for item in events] == ['movement_blocked']
    tick(ops, 2, moving=False, events=events)
    assert unit['state'] == 'blocked' and unit['blocked_state'] == 'returning'
    assert unit['position'] == [2000, 0]
    tick(ops, 3, moving=True, events=events)
    assert unit['state'] == 'returning' and unit['position'] != [2000, 0]


def test_blocked_outbound_without_current_recovery_reports_assistance():
    calls = []
    def disappearing_route(unit, minute):
        calls.append(minute)
        return recovery(unit, minute) if len(calls) == 1 else None
    ops = environment(planner=disappearing_route)
    unit = outbound(ops)
    events = []
    tick(ops, 1, moving=False, events=events)
    assert unit['state'] == 'needs_assistance' and unit['position'] == [2000, 0]
    assert [item[0] for item in events] == ['movement_blocked', 'egress_unavailable']
    tick(ops, 2, moving=False, events=events)
    assert [item[0] for item in events].count('egress_unavailable') == 1


def test_preexisting_blocked_outbound_is_recovered_on_next_tick():
    ops = environment()
    unit = outbound(ops)
    unit.update(state='blocked', blocked_state='travel')
    tick(ops, 1)
    assert unit['state'] == 'returning' and unit['position'] == [2000, 0]


@pytest.mark.parametrize('version', ['wildfire-incident-day-realmap-development-profile-v1',
                                      'wildfire-incident-day-realmap-profile-v2'])
def test_legacy_blocked_outbound_remains_blocked(version):
    ops = environment(version=version)
    unit = outbound(ops)
    tick(ops, 1, moving=False)
    assert unit['state'] == 'blocked' and unit['blocked_state'] == 'travel'
    assert unit['position'] == [2000, 0]


def test_assistance_retries_return_and_cannot_be_reassigned_until_recovered():
    import json
    available = [True]
    calls = [0]
    def planner(unit, minute):
        calls[0] += 1
        # The initial guard sees a route, then it disappears as movement blocks.
        if calls[0] == 2 or not available[0]:
            return None
        return recovery(unit, minute)
    ops = environment(planner=planner)
    unit = outbound(ops)
    events = []
    tick(ops, 1, moving=False, events=events)
    assert unit['state'] == 'needs_assistance' and unit['blocked_outbound_recovery'] is True
    assert not ops.dispatch('unit', 'incident', [1, 1], recovery(unit, 1), 1)
    available[0] = False
    tick(ops, 2, events=events)
    assert unit['state'] == 'needs_assistance'
    # Checkpoint persistence also applies while the route is unavailable.
    restored = Operations.restore(json.loads(json.dumps(ops.checkpoint())), return_planner=planner)
    ops = restored
    unit = ops.resources['unit']
    assert unit['blocked_outbound_recovery'] is True
    available[0] = True
    tick(ops, 3, events=events)
    assert unit['state'] == 'returning' and unit['position'] == [2000, 0]
    tick(ops, 4, moving=False, events=events)
    assert unit['state'] == 'blocked' and unit['blocked_state'] == 'returning'
    assert not ops.dispatch('unit', 'incident', [1, 1], recovery(unit, 4), 4)
    tick(ops, 5, events=events)
    tick(ops, 6, events=events)
    assert unit['state'] == 'available' and unit['position'] == [0, 0]
    assert 'blocked_outbound_recovery' not in unit
    assert [name for name, _ in events].count('egress_unavailable') == 1
