import pytest

from envs.wildfire_response.incident_day.operations import Operations
from wildfire_core.mobility_routing import make_journey, make_leg


def _resource(kind, due_at):
    return dict(id='unit', kind=kind, home_base='base', position=[1000., 0.],
                base_position=[0., 0.], state='on_scene', due_at=due_at,
                water_litres=100, capacity_litres=100, work_remaining=240)


def _return_route(unit, unused_minute):
    start, end = unit['position'], unit['base_position']
    return make_journey([make_leg('road', [start, end], 30)], start, end, 'test recovery')


@pytest.mark.parametrize('kind', ['engine', 'crew'])
@pytest.mark.parametrize('due_at', [3, 10])
def test_unsafe_ground_setup_aborts_immediately_before_work(kind, due_at):
    effects, reports = [], []
    operations = Operations([_resource(kind, due_at)], [], [],
                            {'revalidate_setup_safety': True}, return_planner=_return_route)
    operations.tick(3, wind=0, daylight=True, hazard_check=lambda position, resource_kind: False,
                    suppress=effects.append,
                    emit=lambda source, event, payload, **kwargs: reports.append((event, payload)))
    unit = operations.resources['unit']
    assert unit['state'] == 'returning'
    assert unit['position'] == [1000., 0.]
    assert unit['journey']['origin'] == unit['position']
    assert unit['journey']['destination'] == unit['base_position']
    assert not effects
    assert [event for event, unused in reports] == ['movement_blocked']
    assert reports[0][1]['movement_state'] == 'on_scene'


def test_unsafe_setup_without_recovery_needs_assistance_and_legacy_waits():
    for enabled, expected in ((True, 'needs_assistance'), (False, 'on_scene')):
        effects, reports = [], []
        operations = Operations([_resource('crew', 10)], [], [],
                                {'revalidate_setup_safety': enabled})
        operations.tick(3, wind=0, daylight=True, hazard_check=lambda position, kind: False,
                        suppress=effects.append,
                        emit=lambda source, event, payload, **kwargs: reports.append(event))
        assert operations.resources['unit']['state'] == expected
        assert operations.resources['unit']['position'] == [1000., 0.]
        assert not effects
        assert reports == (['movement_blocked'] if enabled else [])
