import pytest

from wildfire_core.incident_day import fixture_inputs
from wildfire_core.mobility_routing import RoadNetwork
from envs.wildfire_response.incident_day.routing import HazardRouter


@pytest.mark.parametrize('mode', ('engine', 'crew'))
def test_revision_rebinding_matches_uncached_planner_including_ties(mode):
    inputs, document = fixture_inputs(24)
    network = RoadNetwork(document, document['sha256'])
    router = HazardRouter(network, inputs, inputs['heights'], walk_mask=inputs['walk_mask'])
    origin = tuple(network.nodes['4:18'])
    target = (12, 12)
    burning = ((12, 12),)
    wind = (1., 0.)
    hazards = router._hazards(burning, wind)
    for revision in (0, 1, 7, 100):
        for limit in (1, 3, 32):
            expected = router._plan(origin, target, mode, burning, wind, revision, limit, hazards, (), None)
            actual = router.options(origin, target, mode=mode, burning=burning, wind=wind,
                                    revision=revision, limit=limit)
            assert actual == expected
    assert router._cached_options.cache_info().misses == 1


def test_indexed_segment_origins_match_full_scan_and_remain_isolated():
    inputs, document = fixture_inputs(24)
    network = RoadNetwork(document, document['sha256'])
    router = HazardRouter(network, inputs, inputs['heights'], walk_mask=inputs['walk_mask'])
    for edge in list(network.edges.values())[:12]:
        first, second = edge['coordinates'][:2]
        for fraction in (.25, .5, .75):
            point = [first[axis] + fraction * (second[axis] - first[axis]) for axis in range(2)]
            for mode in (None, 'crew', 'engine'):
                expected = router._find_origins(point, mode, use_index=False)
                actual = router._origins(point, mode)
                assert actual == expected
                actual.clear()
                assert router._origins(point, mode) == expected
