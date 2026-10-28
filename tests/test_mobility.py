import copy
import gc
import math
import unittest
import weakref

from wildfire_core.mobility import CoastalMobilityEnv, MobilityBaseline, verify_mobility_trajectory
from wildfire_core.mobility_routing import RoadNetwork, journey_position, make_journey, make_leg
from envs.wildfire_response.regional.ops.generator import digest
from envs.wildfire_response.tests.test_prepared_development import fixture_inputs
from envs.wildfire_response.tests.test_topographic_development import fixture_domain


def network_fixture():
    positions = {'base': [303250, 4035250], 'junction': [303250, 4040250],
                 'fire': [303250, 4045250], 'isolated': [310250, 4045250]}
    edges = []
    for start, end in [('base', 'junction'), ('junction', 'fire')]:
        for source, target in [(start, end), (end, start)]:
            edges.append(dict(id=source + ':' + target, source=source, target=target,
                coordinates=[positions[source], positions[target]],
                length_m=math.dist(positions[source], positions[target]), speed_kph=30, road_class='fixture'))
    value = dict(version='coastal-road-network-v1', crs='EPSG:25830',
        nodes=[dict(id=identifier, position=position) for identifier, position in sorted(positions.items())],
        edges=edges, source=dict(name='Synthetic topology test only', url='', license='test fixture', attribution='test'))
    value['sha256'] = digest(value)
    return value


def environment(**limits):
    prepared, network = fixture_inputs(), network_fixture()
    domain = fixture_domain(prepared)
    return CoastalMobilityEnv(prepared, domain, network, expected_network_sha256=network['sha256'],
        expected_prepared_sha256=prepared['sha256'], expected_topographic_sha256=domain['sha256'],
        slope_enabled=True, allow_synthetic_conditions=True,
        base_nodes=dict(valverde='base', cabezudos='base'), **limits)


class RoutingTests(unittest.TestCase):
    def test_path_cache_does_not_retain_finished_network_instances(self):
        document = network_fixture()
        network = RoadNetwork(document, document['sha256'])
        network.path('base', 'fire')
        reference = weakref.ref(network)
        del network
        gc.collect()
        self.assertIsNone(reference())

    def setUp(self):
        self.document = network_fixture()
        self.network = RoadNetwork(self.document, self.document['sha256'])

    def test_pin_geometry_speed_and_endpoint_validation(self):
        for mutate in [lambda value: value['edges'][0].update(length_m=1),
                       lambda value: value['edges'][0].update(speed_kph=0),
                       lambda value: value['edges'][0].update(coordinates=[[0, 0], [1, 1]])]:
            value = copy.deepcopy(self.document)
            mutate(value)
            value['sha256'] = digest({key: item for key, item in value.items() if key != 'sha256'})
            with self.assertRaises(ValueError):
                RoadNetwork(value, value['sha256'])
        with self.assertRaises(ValueError):
            RoadNetwork(self.document, '0' * 64)

    def test_directed_routes_do_not_cross_disconnected_or_one_way_links(self):
        self.assertEqual(self.network.path('base', 'fire'), ('base:junction', 'junction:fire'))
        with self.assertRaises(ValueError):
            self.network.path('base', 'isolated')
        value = copy.deepcopy(self.document)
        value['edges'] = [edge for edge in value['edges'] if edge['id'] != 'fire:junction']
        value['sha256'] = digest({key: item for key, item in value.items() if key != 'sha256'})
        directed = RoadNetwork(value, value['sha256'])
        with self.assertRaises(ValueError):
            directed.approach('base', directed.nodes['fire'])

    def test_metric_travel_positions_and_return_start_without_teleporting(self):
        journey = self.network.approach('base', self.network.nodes['fire'])
        self.assertEqual(journey['duration_minutes'], 20)
        journey.update(departed_at=4)
        self.assertEqual(journey_position(journey, 4)[0], self.network.nodes['base'])
        self.assertEqual(journey_position(journey, 14)[0], self.network.nodes['junction'])
        self.assertEqual(journey_position(journey, 24)[0], self.network.nodes['fire'])
        position = journey_position(journey, 9)[0]
        returning = self.network.return_journey(journey, 9, 'base')
        self.assertEqual(returning['origin'], position)
        self.assertEqual(returning['destination'], self.network.nodes['base'])
        self.assertEqual(returning['legs'][0]['coordinates'][0], position)
        self.assertEqual(returning['legs'][0]['destination_node'], 'junction')

    def test_engines_do_not_gain_an_offroad_leg_and_crews_walk_explicitly(self):
        target = [303750, 4045250]
        with self.assertRaises(ValueError):
            self.network.approach('base', target)
        journey = self.network.approach('base', target, walk=True, max_access_m=1500)
        self.assertEqual(journey['legs'][-1]['mode'], 'walk')
        self.assertEqual(journey['destination'], target)
        journey['departed_at'] = 0
        returning = self.network.return_journey(journey, journey['duration_minutes'] - 1, 'base')
        self.assertEqual(returning['legs'][0]['mode'], 'walk')
        self.assertEqual(returning['origin'], journey_position(journey, journey['duration_minutes'] - 1)[0])

    def test_minute_rounding_slows_rather_than_accelerates_geometry(self):
        journey = make_journey([make_leg('road', [[0, 0], [1100, 0]], 30)], [0, 0], [1100, 0], 'test')
        self.assertEqual(journey['duration_minutes'], 3)
        self.assertGreaterEqual(journey['legs'][0]['duration_minutes'], 2.2)
        journey['departed_at'] = 0
        self.assertEqual(journey_position(journey, 3)[0], [1100, 0])


class MobilityTests(unittest.TestCase):
    def test_one_minute_policy_does_not_double_order_an_aircraft_with_a_live_offer(self):
        env = environment(max_decisions=120, ticks_per_action=1)
        observation, _ = env.reset()
        policy = MobilityBaseline(prefer_aircraft=True)
        for unused in range(120):
            observation, reward, terminated, truncated, info = env.step(policy(observation))
            self.assertIsNone(info['error'])
            if terminated or truncated:
                break
        self.assertGreaterEqual(observation['minute'], 30)

    def test_cancelled_mobilization_has_the_return_eta_not_the_outbound_eta(self):
        env = environment()
        env.reset()
        identifier = 'engine-valverde'
        env.step(dict(requests=[dict(resource_id=identifier, sector_id='report:claim-0', mission='recon')]))
        observation = env.get_situation()
        env.step(dict(readback_confirmations=[observation['readbacks'][0]['id']], authorizations=[observation['offers'][0]['id']]))
        self.assertEqual(env._core.resources[identifier]['state'], 'mobilizing')
        env._core._apply(dict(releases=[identifier]))
        route = env._core.journeys[identifier]
        self.assertEqual(route['distance_m'], 0)
        self.assertEqual(env._core.resources[identifier]['due_at'], route['arrives_at'])
        self.assertEqual(route['arrives_at'], env._core.sim.tick + 1)

    def test_mixed_baseline_reconnoitres_unknown_claims_without_duplicate_air_requests(self):
        env = environment()
        observation, _ = env.reset('coastal-dev-000')
        policy = MobilityBaseline(prefer_aircraft=True)
        action = policy(observation)
        identifiers = [item['resource_id'] for item in action['requests']]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        air = [item for item in action['requests'] if item['resource_id'] == 'aircraft-las-arenas']
        self.assertEqual(len(air), 1)
        self.assertEqual(air[0]['mission'], 'recon')
        cleared = copy.deepcopy(observation)
        column, row = cleared['reports'][0]['location']
        cleared['sensor_memory'] = [dict(x=column, y=row, burning=False, observed_at=cleared['minute'])]
        self.assertFalse(any(item['resource_id'] == 'aircraft-las-arenas' for item in policy(cleared)['requests']))

    def launch_aircraft(self, env):
        env.reset('coastal-dev-000')
        env.step(dict(requests=[dict(resource_id='aircraft-las-arenas', sector_id='report:claim-0', mission='suppress')]))
        observation = env.get_situation()
        env.step(dict(readback_confirmations=[observation['readbacks'][0]['id']],
                      authorizations=[observation['offers'][0]['id']]))
        while env._core.resources['aircraft-las-arenas']['state'] != 'en_route':
            env.step({})

    def test_aircraft_has_a_real_origin_and_finite_flight_return_and_refill(self):
        env = environment()
        self.launch_aircraft(env)
        unit = env._core.resources['aircraft-las-arenas']
        outbound = copy.deepcopy(env._core.journeys[unit['id']])
        self.assertEqual(outbound['legs'][0]['mode'], 'flight')
        self.assertGreater(outbound['distance_m'], 10000)
        self.assertEqual(unit['due_at'], outbound['arrives_at'])
        states = set()
        while env._core.sim.tick < 110 and unit['state'] != 'available':
            env.step({})
            states.add(unit['state'])
        self.assertTrue({'returning', 'refilling', 'available'} <= states)
        self.assertEqual(env._core.journeys[unit['id']]['destination'], outbound['origin'])
        self.assertLessEqual(sum(item['kind'] == 'water_drop' for item in env._core.acks), 1)
        self.assertEqual(unit['water_litres'], unit['capacity_litres'])

    def test_weather_abort_returns_from_the_current_aircraft_position(self):
        env = environment()
        self.launch_aircraft(env)
        identifier = 'aircraft-las-arenas'
        for unused in range(3):
            env.step({})
        expected = journey_position(env._core.journeys[identifier], env._core.sim.tick + 1)[0]
        env._core._air_operable = lambda: False
        env.step({})
        self.assertEqual(env._core.resources[identifier]['state'], 'returning')
        self.assertEqual(env._core.journeys[identifier]['origin'], expected)
        self.assertTrue(any(item['kind'] == 'sortie_aborted' for item in env._core.acks))

    def test_public_reported_positions_have_causal_timestamps_and_reset_isolation(self):
        env = environment()
        first, _ = env.reset('coastal-dev-000')
        self.assertTrue(first['aircraft_operable'])
        before = env.trajectory()
        env.check_orders({})
        self.assertEqual(env.trajectory(), before)
        observation, *_ = env.step({})
        for unit in observation['resources']:
            self.assertLess(unit['observed_at'], unit['received_at'])
            self.assertLessEqual(unit['received_at'], observation['minute'])
            self.assertNotIn('world_fire', unit)
        again, _ = env.reset('coastal-dev-000')
        self.assertEqual(first, again)
        self.assertEqual(env._core.journeys, {})

    def test_dispatch_arrival_and_releasing_mid_route_follow_recorded_geometry(self):
        env = environment()
        observation, _ = env.reset()
        identifier = 'engine-valverde'
        env.step(dict(requests=[dict(resource_id=identifier, sector_id='report:claim-0', mission='suppress')]))
        observation = env.get_situation()
        offer = observation['offers'][0]
        readback = observation['readbacks'][0]
        env.step(dict(readback_confirmations=[readback['id']], authorizations=[offer['id']]))
        while env._core.resources[identifier]['state'] != 'en_route':
            env.step({})
        route = env._core.journeys[identifier]
        self.assertEqual(route['arrives_at'], env._core.resources[identifier]['due_at'])
        env.step({})
        position = journey_position(env._core.journeys[identifier], env._core.sim.tick)[0]
        env.step(dict(releases=[identifier]))
        returning = env._core.journeys[identifier]
        self.assertEqual(returning['origin'], position)
        self.assertEqual(returning['direction'], 'return')
        self.assertEqual(returning['destination'], env.network.nodes['base'])

    def test_night_aircraft_requests_cannot_launch_a_flight(self):
        env = environment()
        observation, _ = env.reset('coastal-dev-001')
        self.assertFalse(observation['aircraft_operable'])
        observation, *_ = env.step(dict(requests=[dict(resource_id='aircraft-las-arenas',
            sector_id='report:claim-0', mission='suppress')]))
        self.assertFalse(env._core.journeys)
        self.assertTrue(any(item['kind'] == 'request_denied' for item in observation['acknowledgements']))

    def test_complete_false_alarm_replays_and_rejects_tampered_positions(self):
        env = environment()
        env.reset('coastal-dev-002')
        while not (env._core.terminated or env._core.truncated):
            env.step({})
        trajectory = env.trajectory()
        result = verify_mobility_trajectory(trajectory, env._inputs, env._domain,
            env.network.document, base_nodes=dict(valverde='base', cabezudos='base'))
        self.assertTrue(result['verified'])
        altered = copy.deepcopy(trajectory)
        altered['transitions'][0]['observation']['resources'][0]['map_position'][0] += 1
        with self.assertRaises(ValueError):
            verify_mobility_trajectory(altered, env._inputs, env._domain,
                env.network.document, base_nodes=dict(valverde='base', cabezudos='base'))
