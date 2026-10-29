import copy
import json
from pathlib import Path
import tempfile
import unittest

from wildfire_core.incident_day import CASE_IDS, IncidentDayEnv, IncidentDayTools, fixture_inputs, tools
from envs.wildfire_response.incident_day.outcomes import recompute
from envs.wildfire_response.incident_day.scenarios import PROFILE


def create(**kwargs):
    inputs, network = fixture_inputs()
    return IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                          expected_network_sha256=network['sha256'], **kwargs)


class IncidentDayExecutionTests(unittest.TestCase):
    def test_timely_protection_improves_full_day_without_changing_fire_randomness(self):
        waiting, protection = create(), create()
        waiting.reset(CASE_IDS[4])
        protection.reset(CASE_IDS[4])
        option = protection.evacuation_options('population-1', 'destination-1')['options'][0]
        protection.step({'protection_orders': [dict(group_id='population-1', action='evacuate',
            destination_id='destination-1', approach_id=option['id'])]})
        for env in (waiting, protection):
            while not env.terminated and not env.truncated:
                env.advance_time(60)
        self.assertEqual(waiting.fire.checkpoint(), protection.fire.checkpoint())
        self.assertGreater(waiting.outcomes()['new_exposed_population'], 0)
        self.assertEqual(protection.outcomes()['new_exposed_population'], 0)
        self.assertGreater(protection.reward, waiting.reward)

    def test_documented_runtime_profile_matches_executed_values(self):
        profile = json.loads((Path(__file__).resolve().parents[1] / 'data/incident_day_v2/profile.json').read_text())
        self.assertEqual(profile['runtime'], PROFILE)

    def test_dispatch_queries_replay_and_checkpoint_resume(self):
        env = create()
        env.reset()
        option = env.route_options('crew-1')['options'][0]
        env.step({'requests': [dict(resource_id='crew-1', sector_id='incident', approach_id=option['id'])]})
        while not env.get_situation()['offers']:
            env.advance_time(60)
        offer = env.get_situation()['offers'][0]
        env.step({'authorizations': [offer['id']]})
        other = create()
        other.restore(env.checkpoint())
        while not env.terminated and not env.truncated:
            self.assertEqual(other.advance_time(60), env.advance_time(60))
        self.assertEqual(other.checkpoint(), env.checkpoint())
        self.assertTrue(env.verify_episode()['independent_reward_agreement'])

    def test_public_route_preview_ignores_true_hazard_and_extra_query_preserves_physics(self):
        env, control = create(), create()
        env.reset()
        control.reset()
        before = env.route_options('crew-1')
        for unused in range(10):
            self.assertEqual(env.route_options('crew-1'), before)
            env.get_reports()
        env.advance_time(60)
        control.advance_time(60)
        self.assertEqual(env.fire.checkpoint(), control.fire.checkpoint())
        env.fire.cells[1][1]['heat'] = 1.
        self.assertEqual(env.route_options('crew-1'), control.route_options('crew-1'))

    def test_simultaneous_offers_are_unique_and_finite(self):
        env = create()
        env.reset()
        orders = []
        for identifier in ('crew-0', 'crew-1'):
            option = env.route_options(identifier)['options'][0]
            orders.append(dict(resource_id=identifier, sector_id='incident', approach_id=option['id']))
        env.step({'requests': orders})
        while len(env.get_situation()['offers']) < 2:
            env.advance_time(10)
        offers = env.get_situation()['offers']
        self.assertEqual(len({offer['id'] for offer in offers}), 2)
        self.assertEqual({offer['resource_id'] for offer in offers}, {'crew-0', 'crew-1'})

    def test_population_destination_and_approach_are_explicit(self):
        env = create()
        env.reset()
        options = env.evacuation_options('population-0', 'destination-0')
        self.assertTrue(options['options'])
        order = dict(group_id='population-0', action='evacuate', destination_id='destination-0',
                     approach_id=options['options'][0]['id'])
        self.assertTrue(env.check_action({'protection_orders': [order]})['valid'])
        env.step({'protection_orders': [order]})
        for unused in range(15):
            env.advance_time(20)
            if env.ops.groups['population-0']['status'] == 'arrived':
                break
        self.assertEqual(env.ops.groups['population-0']['destination_id'], 'destination-0')

    def test_audit_stream_and_fresh_resume_segment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = create(artifact_dir=root / 'segment-1')
            env.reset()
            env.advance_time(60)
            records = [json.loads(line) for line in (root / 'segment-1/private/audit.jsonl').read_text().splitlines()]
            self.assertEqual(records, env._audit)
            self.assertEqual((root / 'segment-1/private').stat().st_mode & 0o777, 0o700)
            other = create(artifact_dir=root / 'segment-2')
            other.restore(env.checkpoint())
            self.assertEqual(other.advance_time(60), env.advance_time(60))
            with self.assertRaises(ValueError):
                env.reset()

    def test_tools_offer_bounded_wait_and_record_rejected_arguments(self):
        env = create()
        env.reset()
        adapter = IncidentDayTools(env)
        self.assertEqual(len(tools()), 8)
        self.assertEqual(adapter.execute('get_situation', {})['minute'], 0)
        invalid = {'action': {'requests': [{'oops': 'not-an-order'}]}}
        result = adapter.execute('commit_orders', invalid)
        self.assertIn('Request needs', result['info']['error'])
        self.assertEqual(adapter.calls[-1]['arguments'], invalid)
        self.assertEqual(env.minute, 1)

    def test_independent_audit_detects_exposure_inventory_corruption(self):
        env = create()
        env.reset()
        env.advance_time(5)
        audit = copy.deepcopy(env._audit)
        audit[-1]['population']['population-0']['count'] += 1
        with self.assertRaises(ValueError):
            recompute(audit)


if __name__ == '__main__':
    unittest.main()
