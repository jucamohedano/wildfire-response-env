"""Real Operations movement through the private, pinned segment seam."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = [Path('/tmp/expanded-post-anchors-v4/network.json'),
           Path('/tmp/expanded-population-anchors-v1-secure/candidate.json'),
           Path('/tmp/expanded-road-segment-exercise-v1-r4/candidate.json'),
           Path('/tmp/extended-access-lattice-v1/directed-road-candidate-v2.json.gz')]


@pytest.mark.skipif(not all(p.exists() for p in PRIVATE), reason='private pinned source artifacts unavailable')
def test_real_ops_delayed_and_blocked_segment_movement():
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PROJ_NETWORK='OFF', PYTHONPATH=os.pathsep.join([
        str(ROOT/'envs'), str(ROOT/'envs/wildfire_extended_inputs_v1'),
        str(ROOT/'envs/wildfire_response/core/src'), str(ROOT/'envs/wildfire_containment_v1/src'), str(ROOT)]))
    result = subprocess.run([sys.executable, __file__], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def main():
    import copy
    import hashlib
    import json
    from eval import expanded_road_segment_exercise_v1 as component
    from eval.expanded_road_runtime_v1 import load_candidate
    from envs.wildfire_response.incident_day.operations import Operations
    from wildfire_source_joint_v1.segment_movement import PopulationSegmentMovementV1

    graph, anchors, overlay = [json.loads(p.read_bytes()) for p in PRIVATE[:3]]
    source = load_candidate()
    pin = hashlib.sha256(Path(component.__file__).read_bytes()).hexdigest()
    seam = PopulationSegmentMovementV1(graph, source, anchors, overlay,
                                       expected_segment_source_sha256=pin, opt_in=True)
    try:
        PopulationSegmentMovementV1(graph, source, anchors, overlay,
                                    expected_segment_source_sha256='0'*64, opt_in=True)
    except ValueError:
        pass
    else:
        raise AssertionError('untrusted component pin accepted')
    record = next(r for r in overlay['paths'] if r['status'] == 'assumed_exercise_path')
    case, group, dest = record['original_case_id'], record['group_id'], record['destination_id']
    origin = seam._route._network.nodes[record['source_node']]
    target = seam._route._network.nodes[record['target_node']]
    option = seam.options(case, group, dest, origin, burning=(), wind=(0,0), excluded_cells=())['options'][0]
    journey = option['journey']
    ops = Operations([], [dict(id=dest, position=target, capacity=100, status='open')],
                     [dict(id=group, position=origin, home=origin, count=10)],
                     dict(order_delivery_minutes=1, order_ack_minutes=1, order_execution_minutes=1))
    ops.protect(group, 'evacuate', dest, journey, 0)
    events = []
    def emit(*args, **kwargs): events.append((args, kwargs))
    def tick(minute, excluded):
        def movement(path, m, kind):
            assert kind == 'group'
            return seam.movement_safe(case, group, dest, path, m,
                current_position=ops.groups[group]['position'], burning=(), wind=(0,0), excluded_cells=excluded)
        ops.tick(minute, wind=(0,0), daylight=True, hazard_check=lambda p,k: True,
                 movement_check=movement, suppress=lambda u: False, emit=emit)
    for minute in (1,2,3): tick(minute, ())
    assert ops.groups[group]['position'] == origin
    order = ops.orders[ops.groups[group]['order_id']]
    assert order['state'] == 'executing'
    blocked_cell = seam._route._guard._router._cell(origin)
    assert not seam.movement_safe(case, group, dest, order['journey'], 4, current_position=origin,
                                  burning=[blocked_cell], wind=(0,0), excluded_cells=())
    tick(4, [blocked_cell])
    assert ops.groups[group]['status'] == 'blocked' and ops.groups[group]['position'] == origin
    state = ops.checkpoint()
    ops = Operations.restore(state)
    tick(5, ())
    assert ops.groups[group]['status'] in ('evacuating', 'arrived')
    assert ops.groups[group]['position'] != origin
    resumed = ops.checkpoint()
    ops = Operations.restore(state)
    tick(5, ())
    assert ops.checkpoint() == resumed
    path = ops.orders[ops.groups[group]['order_id']]['journey']
    bad = copy.deepcopy(path);bad['legs'][0]['coordinates'][0][0] += 1
    assert not seam.movement_safe(case, group, dest, bad, 6, current_position=ops.groups[group]['position'],
                                  burning=(), wind=(0,0), excluded_cells=())
    assert not seam.movement_safe(case, group, 'other', path, 6, current_position=ops.groups[group]['position'],
                                  burning=(), wind=(0,0), excluded_cells=())
    assert not seam.movement_safe(case, group, dest, path, 6, current_position=[0,0],
                                  burning=(), wind=(0,0), excluded_cells=())
    assert any(args[1] == 'movement_blocked' for args, _ in events)


if __name__ == '__main__':
    main()
