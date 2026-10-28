import pytest

from wildfire_core.incident_day import fixture_inputs
from wildfire_core.mobility_routing import RoadNetwork
from envs.wildfire_response.incident_day.routing import HazardRouter


def routers():
    inputs, source = fixture_inputs(24)
    network = RoadNetwork(source, source['sha256'])
    args = dict(walk_mask=inputs['walk_mask'])
    return inputs, network, args


def test_all_edges_scope_preserves_default_plans_and_empty_scope_denies_driving():
    inputs, network, args = routers()
    default = HazardRouter(network, inputs, inputs['heights'], **args)
    scoped = HazardRouter(network, inputs, inputs['heights'], allowed_edge_ids=list(network.edges), **args)
    closed = HazardRouter(network, inputs, inputs['heights'], allowed_edge_ids=[], **args)
    query = dict(mode='engine', burning=[], wind=[0, 0], limit=32)
    origin = network.nodes['4:18']
    expected = default.options(origin, [12, 12], **query)
    assert expected['options']
    assert scoped.options(origin, [12, 12], **query) == expected
    assert not closed.options(origin, [12, 12], **query)['options']
    journey = expected['options'][0]['journey']
    assert default.safe_journey_segment(journey, 0, [], [0, 0], mode='engine')
    assert not closed.safe_journey_segment(journey, 0, [], [0, 0], mode='engine')


def test_scope_cannot_invent_an_edge_and_owns_a_copy_of_clearance():
    inputs, network, args = routers()
    with pytest.raises(ValueError, match='existing source edges'):
        HazardRouter(network, inputs, inputs['heights'], allowed_edge_ids=['invented-road'], **args)
    edges = list(network.edges)
    scoped = HazardRouter(network, inputs, inputs['heights'], allowed_edge_ids=edges, **args)
    edges.clear()
    assert scoped.allowed_edge_ids == frozenset(network.edges)
