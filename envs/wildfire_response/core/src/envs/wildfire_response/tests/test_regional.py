import copy
import json
from pathlib import Path
import tempfile
import unittest

from envs.wildfire_response import WildfireResponseEnv
from envs.wildfire_response.regional import RegionalResponseEnv
from envs.wildfire_response.regional.data import geometry, grid_to_lonlat, load_pack, text_digest
from envs.wildfire_response.regional.run import replay, run
from envs.wildfire_response.eval.run import load


def request(resource='aircraft-las-arenas'):
    return dict(requests=[dict(resource_id=resource, sector_id='report:report-0', mission='suppress')])


class RegionalTests(unittest.TestCase):
    def test_source_text_hash_ignores_changing_markup_but_not_facts(self):
        original = b'<h1 id="123">Aircraft 3100 litres</h1><script>dynamic_id=123</script>'
        changed_markup = b'<h1 id="456">Aircraft 3100 litres</h1><script>dynamic_id=456</script>'
        changed_fact = b'<h1>Aircraft 5000 litres</h1>'
        self.assertEqual(text_digest(original), text_digest(changed_markup))
        self.assertNotEqual(text_digest(original), text_digest(changed_fact))

    def test_evidence_and_geometry_do_not_invent_base_coordinates(self):
        pack = load_pack()
        self.assertEqual(len(pack['facilities']), 3)
        self.assertTrue(all(base['coordinates'] is None for base in pack['facilities']))
        self.assertEqual(geometry(pack)['width'], 53)
        point = grid_to_lonlat(pack, 0, 0)
        self.assertGreater(point[0], pack['region']['bbox_lon_lat'][0])
        self.assertLess(point[1], pack['region']['bbox_lon_lat'][3])

    def test_no_demo_vehicles_or_future_truth_in_observation(self):
        env = RegionalResponseEnv()
        observation, _ = env.reset('regional-003')
        text = json.dumps(observation)
        for field in ('family', 'events', 'report_events', 'fire_checkpoint', 'phone_number', 'chat_id'):
            self.assertNotIn('"'+field+'"', text)
        self.assertEqual(env.sim.vehicles(), [])
        self.assertEqual(env.sim.roads, set())
        self.assertEqual(len(observation['reports']), 1)
        changed = copy.deepcopy(env.task)
        changed['events'][1]['location'] = [1, 1]
        other = RegionalResponseEnv()
        self.assertEqual(observation, other.reset(scenario=changed)[0])

    def test_request_offer_authorization_and_no_double_allocation(self):
        env = RegionalResponseEnv(ticks_per_action=1)
        env.reset()
        observation, _, _, _, info = env.step(request('engine-valverde'))
        self.assertIsNone(info['error'])
        offer = observation['offers'][0]
        self.assertEqual(env.resources['engine-valverde']['state'], 'reserved')
        self.assertGreater(offer['lead_time_minutes'], 0)
        self.assertEqual(env.acks[-1]['minute'], 1)
        env.step(dict(authorizations=[offer['id']]))
        self.assertEqual(env.resources['engine-valverde']['state'], 'mobilizing')
        *_, info = env.step(dict(authorizations=[offer['id']]))
        self.assertIsNotNone(info['error'])
        self.assertEqual(env.resources['engine-valverde']['state'], 'mobilizing')

    def test_invalid_action_is_atomic_and_three_invalids_truncate(self):
        env = RegionalResponseEnv(ticks_per_action=1)
        env.reset()
        bad = request('engine-valverde')
        bad['messages'] = [dict(recipient='unknown', purpose='evacuate')]
        for _ in range(3):
            *_, truncated, info = env.step(bad)
            self.assertIsNotNone(info['error'])
        self.assertTrue(truncated)
        self.assertEqual(env.resources['engine-valverde']['state'], 'available')
        self.assertGreater(env.evaluator_state()['cost'], 102)

    def test_unavailable_aircraft_and_expiring_offers(self):
        env = RegionalResponseEnv(ticks_per_action=1)
        env.reset('regional-004')
        env.step(request())
        self.assertEqual(env.acks[-1]['kind'], 'request_denied')
        env.step(request('crew-cabezudos'))
        for _ in range(8):
            env.step({})
        self.assertEqual(env.resources['crew-cabezudos']['state'], 'available')
        self.assertEqual(next(iter(env.offers.values()))['status'], 'expired')

    def test_aircraft_makes_single_finite_drop_then_returns_and_refills(self):
        env = RegionalResponseEnv(ticks_per_action=1, max_decisions=120)
        env.reset()
        env.step(request())
        env.step(dict(authorizations=[next(iter(env.offers))]))
        for _ in range(60):
            if env.resources['aircraft-las-arenas']['state'] == 'available':
                break
            env.step({})
        drops = [ack for ack in env.acks if ack['kind'] == 'water_drop']
        self.assertEqual(len(drops), 1)
        self.assertEqual(drops[0]['litres'], 3100)
        self.assertEqual(env.resources['aircraft-las-arenas']['water_litres'], 3100)
        self.assertEqual(env.resources['aircraft-las-arenas']['state'], 'available')
        self.assertTrue(any(ack['kind'] == 'resource_ready' for ack in env.acks))

    def test_grounded_aircraft_aborts_without_dropping(self):
        env = RegionalResponseEnv(ticks_per_action=1)
        env.reset()
        task = copy.deepcopy(env.task)
        task['events'].append(dict(tick=3, kind='daylight', value=False))
        env.reset(scenario=task)
        env.step(request())
        env.step(dict(authorizations=[next(iter(env.offers))]))
        env.step({})
        self.assertEqual(env.resources['aircraft-las-arenas']['state'], 'available')
        self.assertTrue(any(ack['kind'] == 'sortie_cancelled' for ack in env.acks))
        self.assertFalse(any(ack['kind'] == 'water_drop' for ack in env.acks))

    def test_airborne_aircraft_returns_when_conditions_change(self):
        env = RegionalResponseEnv(ticks_per_action=1)
        env.reset()
        task = copy.deepcopy(env.task)
        task['events'].append(dict(tick=10, kind='daylight', value=False))
        env.reset(scenario=task)
        env.step(request())
        env.step(dict(authorizations=[next(iter(env.offers))]))
        while env.sim.tick < 10:
            env.step({})
        self.assertEqual(env.resources['aircraft-las-arenas']['state'], 'returning')
        self.assertTrue(any(ack['kind'] == 'sortie_aborted' for ack in env.acks))
        self.assertFalse(any(ack['kind'] == 'water_drop' for ack in env.acks))

    def test_two_bases_offer_separate_resources_and_cannot_double_dispatch(self):
        env = RegionalResponseEnv(ticks_per_action=1)
        env.reset()
        env.step(dict(requests=[request('engine-valverde')['requests'][0], request('crew-cabezudos')['requests'][0]]))
        offers = [offer['id'] for offer in env.offers.values()]
        self.assertEqual(len(offers), 2)
        env.step(dict(authorizations=offers))
        while env.sim.tick < 24:
            env.step({})
        arrived = {ack['resource_id'] for ack in env.acks if ack['kind'] == 'resource_arrived'}
        self.assertEqual(arrived, {'engine-valverde', 'crew-cabezudos'})
        self.assertTrue(any(ack['kind'] == 'mutual_aid_update' and ack['source'] == 'cabezudos'
                            and ack['recipient'] == 'valverde' for ack in env.acks))
        env.step(request('crew-cabezudos'))
        self.assertEqual(env.acks[-1]['kind'], 'request_denied')
        self.assertEqual(len(env.resources), 5)

    def test_population_inform_does_not_evacuate_and_delivery_is_delayed(self):
        env = RegionalResponseEnv(ticks_per_action=1)
        env.reset('regional-002')
        recipient = 'settlement-west'
        env.step(dict(messages=[dict(recipient=recipient, purpose='inform')]))
        env.step({})
        self.assertEqual(env.sim.groups[recipient]['status'], 'unwarned')
        env.step(dict(messages=[dict(recipient=recipient, purpose='evacuate')]))
        self.assertEqual(env.sim.groups[recipient]['status'], 'unwarned')
        env.step({})
        self.assertEqual(env.sim.groups[recipient]['status'], 'evacuating')

    def test_reset_and_v1_isolation(self):
        old = WildfireResponseEnv()
        before, _ = old.reset('train-000')
        env = RegionalResponseEnv()
        initial, _ = env.reset()
        initial_checkpoint = env.evaluator_state()['fire_checkpoint']
        env.step(request())
        self.assertEqual(env.reset()[0], initial)
        self.assertEqual(env.evaluator_state()['fire_checkpoint'], initial_checkpoint)
        self.assertEqual(old.observe(), before)

    def test_complete_rollout_and_serialized_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'rollout'
            summary = run(output, scenario_id='regional-002', policy_name='passive')
            self.assertTrue(summary['terminated'])
            self.assertEqual(summary['total_reward'], 0)
            trajectory = load(output/'trajectory.json.gz')
            self.assertEqual(replay(trajectory), 0)


if __name__ == '__main__':
    unittest.main()
