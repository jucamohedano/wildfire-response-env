import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


try:
    from wildfire_openenv.prepared import (Allocation, PreparedAction, PreparedEnvironment,
                                           ReadbackCorrection, create_app)
    from wildfire_core.prepared import CASE_IDS, PreparedDataEnv, ProtectionBaseline
    from envs.wildfire_response.tests.test_prepared_development import fixture_inputs
    from envs.wildfire_response.regional.ops.communications import ORDER_FIELDS
    AVAILABLE = True
except ImportError:
    AVAILABLE = False


@unittest.skipUnless(AVAILABLE, 'Install the optional wildfire-openenv distribution')
class PreparedAdapterTests(unittest.TestCase):
    def setUp(self):
        self.inputs = fixture_inputs()

    def adapter(self, **kwargs):
        return PreparedEnvironment(self.inputs, expected_sha256=self.inputs['sha256'],
                                   allow_synthetic_conditions=True, **kwargs)

    def test_configuration_requires_explicit_opt_in_and_pin(self):
        with self.assertRaises(TypeError):
            PreparedEnvironment(self.inputs, expected_sha256=self.inputs['sha256'])
        with self.assertRaises(ValueError):
            PreparedEnvironment(self.inputs, expected_sha256=self.inputs['sha256'],
                                allow_synthetic_conditions=False)
        with self.assertRaises(ValueError):
            PreparedEnvironment(self.inputs, expected_sha256='0' * 64,
                                allow_synthetic_conditions=True)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                PreparedEnvironment(allow_synthetic_conditions=True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'inputs.json'
            path.write_text(json.dumps(self.inputs))
            with patch.dict(os.environ, WILDFIRE_PREPARED_INPUTS=str(path),
                            WILDFIRE_PREPARED_SHA256=self.inputs['sha256']):
                adapter = PreparedEnvironment(allow_synthetic_conditions=True)
                self.assertEqual(adapter.reset().data, self.adapter().reset().data)

    def test_discovery_exposes_only_six_opaque_development_cases(self):
        adapter = self.adapter()
        self.assertEqual(adapter.list_splits(), ['development'])
        self.assertEqual(adapter.num_tasks('development'), 6)
        self.assertEqual(adapter.get_task_range('development'),
                         [dict(id=identifier, split='development') for identifier in CASE_IDS])
        for split in ('train', 'validation', 'test'):
            with self.assertRaises(ValueError):
                adapter.list_tasks(split)
        for index in (-1, 6, True):
            with self.assertRaises(ValueError):
                adapter.get_task('development', index)

    def test_wire_fields_match_readback_contract_and_reject_unknowns(self):
        self.assertEqual(set(Allocation.model_fields), set(ORDER_FIELDS))
        self.assertEqual(set(ReadbackCorrection.model_fields), {'readback_id', *ORDER_FIELDS})
        payloads = [dict(explanation='not an ops field'), dict(events=[]),
                    dict(metadata={'private': 'payload'}), dict(operation='forecast'),
                    dict(requests=[{}]), dict(clarifications=['claim'] * 5),
                    dict(triage=[dict(report_ids=[], assessment='defer')]),
                    dict(readback_corrections=[dict(readback_id='r', offer_id='o')]),
                    dict(protection_orders=[dict(recipient='r', action='evacuate',
                                                channel='broadcast')]),
                    dict(operation='situation', releases=['crew'])]
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                PreparedAction(**payload)
        for field in ('authorizations', 'releases', 'readback_confirmations'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                PreparedAction(**{field: ['identifier'] * 13})
        assessment = dict(report_ids=['x' * 255] * 20, assessment='defer')
        with self.assertRaises(ValueError):
            PreparedAction(triage=[assessment] * 12,
                           authorizations=['y' * 255] * 12, releases=['z' * 255] * 12)

    def test_checks_and_reads_do_not_advance_and_state_is_public_only(self):
        adapter = self.adapter()
        self.assertEqual(adapter.state.step_count, 0)
        with self.assertRaises(RuntimeError):
            adapter.step(PreparedAction(operation='trajectory'))
        adapter.reset()
        for remaining in (2, 1, 0):
            checked = adapter.step(PreparedAction(operation='check', clarifications=['unknown']))
            self.assertFalse(checked.metadata['action_check']['valid'])
            self.assertEqual(checked.metadata['action_check']['remaining_checks'], remaining)
        with self.assertRaises(ValueError):
            adapter.step(PreparedAction(operation='check'))
        before = adapter.state.model_dump()
        adapter.step(PreparedAction(operation='situation'))
        adapter.step(PreparedAction(operation='trajectory'))
        self.assertEqual(adapter.state.model_dump(), before)
        self.assertEqual(set(before), {'episode_id', 'step_count', 'minute', 'terminated', 'truncated'})
        with self.assertRaises(ValueError):
            adapter.reset(unknown=True)
        with self.assertRaises(ValueError):
            adapter.step(PreparedAction(), unknown=True)

    def test_semantically_invalid_commits_match_core_atomic_failure(self):
        local = PreparedDataEnv(self.inputs, expected_sha256=self.inputs['sha256'],
                                allow_synthetic_conditions=True)
        adapter = self.adapter()
        local.reset()
        adapter.reset()
        action = dict(clarifications=['unknown'])
        for decision in range(3):
            observation, reward, terminated, truncated, info = local.step(action)
            remote = adapter.step(PreparedAction(**action))
            self.assertEqual(remote.data, observation)
            self.assertEqual(remote.metadata, info)
            self.assertEqual(remote.reward, reward)
            self.assertEqual(remote.done, terminated or truncated)
            self.assertEqual(adapter.state.step_count, decision + 1)
        self.assertTrue(remote.truncated)

    def test_complete_episode_matches_all_public_trajectory_fields(self):
        local = PreparedDataEnv(self.inputs, expected_sha256=self.inputs['sha256'],
                                allow_synthetic_conditions=True)
        adapter = self.adapter()
        observation, info = local.reset(CASE_IDS[1], 771101)
        remote = adapter.reset(task_id=CASE_IDS[1], seed=771101)
        self.assertEqual(remote.metadata, info)
        actor = ProtectionBaseline(prefer_shelter=False)
        for decision in range(60):
            action = actor(observation)
            observation, reward, terminated, truncated, info = local.step(action)
            remote = adapter.step(PreparedAction(**action))
            self.assertEqual(remote.data, observation)
            self.assertEqual(remote.metadata, info)
            self.assertEqual(remote.reward, reward)
            self.assertEqual(remote.done, terminated or truncated)
            if remote.done:
                break
        self.assertTrue(remote.done)
        trace = adapter.step(PreparedAction(operation='trajectory')).trajectory
        self.assertEqual(trace, local.trajectory())
        self.assertTrue(local.verify_episode()['passed'])
        self.assertEqual(sum(record['objective_reward'] for record in trace['transitions']), local.reward)
        self.assertTrue(adapter.core.verify_episode()['passed'])

    def test_separate_adapters_keep_independent_episodes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'inputs.json'
            path.write_text(json.dumps(self.inputs))
            with patch.dict(os.environ, WILDFIRE_PREPARED_INPUTS=str(path),
                            WILDFIRE_PREPARED_SHA256=self.inputs['sha256']):
                with patch('wildfire_openenv.prepared.create_openenv_app') as server:
                    create_app()
                    factory = server.call_args.args[0]
                    self.assertEqual(server.call_args.kwargs['max_concurrent_envs'], 1)
                    first, second = factory(), factory()
        self.assertIsNot(first, second)
        initial = second.reset().data
        first.reset()
        first.step(PreparedAction())
        self.assertEqual(second.state.minute, 0)
        self.assertEqual(second.core.get_situation(), initial)
        self.assertEqual(first.reset().data, initial)


if __name__ == '__main__':
    unittest.main()
