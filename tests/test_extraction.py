import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wildfire_core import WildfireResponseEnv
from wildfire_core.baselines import CooperativeBaseline
from wildfire_core.benchmark import verify_lock
from wildfire_core.training import RegionalTrainingEnv
from simulator.contacts import directory


class ExtractionTests(unittest.TestCase):
    def test_frozen_benchmark_identity_and_public_api(self):
        self.assertEqual(verify_lock()['sha256'], 'e882e0f8ba58246ef3e4c3d271d32a6d54a1756d980be52bcccb524ff84ad840')
        environment = WildfireResponseEnv()
        observation, _ = environment.reset('regional-train-001')
        self.assertEqual(len(observation['resources']), 5)
        self.assertTrue(any(unit['kind']=='aircraft' for unit in observation['resources']))
        while True:
            observation, _, terminated, truncated, _ = environment.step(CooperativeBaseline()(observation))
            if terminated or truncated:
                break
        self.assertTrue(terminated)

    def test_contact_shim_never_loads_credentials_or_returns_live_destinations(self):
        environment = WildfireResponseEnv()
        environment.reset()
        with patch.dict(os.environ, {'DEMO_FARM_PHONE': 'private-phone', 'DEMO_TOWN_CHAT_ID': 'private-chat'}), \
                patch('builtins.open', side_effect=AssertionError('Unexpected file access')):
            contacts = directory(environment.sim.groups)
        self.assertNotIn('private-phone', json.dumps(contacts))
        self.assertNotIn('private-chat', json.dumps(contacts))
        self.assertTrue(all(person['phone_number'] is None and person['chat_id'] is None for person in contacts['people']))
        self.assertTrue(all(district['chat_id'] is None for district in contacts['districts']))

    def test_training_bridge_is_local_and_full_horizon(self):
        with tempfile.TemporaryDirectory() as directory_path:
            environment = RegionalTrainingEnv(Path(directory_path))
            environment.reset('regional-train-001')
            self.assertLess(environment._finish(), -2)
            self.assertTrue(environment._verification['passed'])


if __name__ == '__main__':
    unittest.main()
