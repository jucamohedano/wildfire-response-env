"""Early recovery is limited to source-map development profiles."""
import pytest

from envs.wildfire_response.incident_day.operations import Operations
from wildfire_core.mobility_routing import make_journey, make_leg


VERSIONS = ('wildfire-incident-day-realmap-development-profile-v1',
            'wildfire-incident-day-realmap-development-profile-v2')


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


def test_legacy_profile_keeps_assistance_behavior():
    ops = environment(version='wildfire-incident-day-realmap-profile-v2')
    tick(ops, 0)
    assert ops.resources['unit']['state'] == 'needs_assistance'
    assert ops.resources['unit']['position'] == [2000, 0]
