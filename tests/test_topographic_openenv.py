import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


try:
    from wildfire_core.topographic import BOUNDARY_CASE_ID, CASE_IDS, TopographicEnv
    from wildfire_core.prepared import ProtectionBaseline
    from wildfire_openenv.prepared import PreparedAction, PreparedClient, PreparedObservation, PreparedState
    from wildfire_openenv.topographic import (TopographicEnvironment, configured_slope,
                                              create_app, main)
    from envs.wildfire_response.tests.test_topographic_development import fixture_domain, fixture_inputs
    AVAILABLE = True
except ModuleNotFoundError as exception:
    if exception.name not in ('wildfire_core', 'wildfire_core.topographic', 'wildfire_openenv',
                              'openenv', 'uvicorn', 'pydantic'):
        raise
    AVAILABLE = False


@unittest.skipUnless(AVAILABLE, 'Install optional OpenEnv dependencies and the topographic core')
class TopographicAdapterTests(unittest.TestCase):
    def setUp(self):
        self.prepared = fixture_inputs()
        self.topographic = fixture_domain(self.prepared)

    def options(self, slope_enabled=True):
        return dict(expected_prepared_sha256=self.prepared['sha256'],
                    expected_topographic_sha256=self.topographic['sha256'],
                    slope_enabled=slope_enabled, allow_synthetic_conditions=True)

    def adapter(self, slope_enabled=True, **kwargs):
        return TopographicEnvironment(self.prepared, self.topographic,
                                      **dict(self.options(slope_enabled), **kwargs))

    def core(self, slope_enabled=True):
        return TopographicEnv(self.prepared, self.topographic, **self.options(slope_enabled))

    def test_configuration_requires_both_pins_opt_in_and_explicit_slope(self):
        with self.assertRaises(TypeError):
            TopographicEnvironment(self.prepared, self.topographic, slope_enabled=True)
        with self.assertRaises(ValueError):
            self.adapter(allow_synthetic_conditions=False)
        for field in ('expected_prepared_sha256', 'expected_topographic_sha256'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.adapter(**{field: '0' * 64})
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                TopographicEnvironment(allow_synthetic_conditions=True, slope_enabled=False)
            with self.assertRaises(ValueError):
                self.adapter(slope_enabled=None)
        for invalid in (0, 1, 'true', 'false', [], {}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.adapter(slope_enabled=invalid)

    def test_environment_configuration_uses_only_exact_boolean_spellings(self):
        with tempfile.TemporaryDirectory() as directory:
            prepared_path, topographic_path = Path(directory) / 'prepared.json', Path(directory) / 'topo.json'
            prepared_path.write_text(json.dumps(self.prepared))
            topographic_path.write_text(json.dumps(self.topographic))
            environment = dict(WILDFIRE_PREPARED_INPUTS=str(prepared_path),
                WILDFIRE_PREPARED_SHA256=self.prepared['sha256'],
                WILDFIRE_TOPOGRAPHIC_INPUTS=str(topographic_path),
                WILDFIRE_TOPOGRAPHIC_SHA256=self.topographic['sha256'])
            for flag in ('true', 'false'):
                with patch.dict(os.environ, dict(environment, WILDFIRE_TOPOGRAPHIC_SLOPE_ENABLED=flag), clear=True):
                    adapter = TopographicEnvironment(allow_synthetic_conditions=True)
                    self.assertEqual(adapter.reset().data, self.adapter(flag == 'true').reset().data)
                    self.assertEqual(configured_slope(None), flag == 'true')
            for invalid in ('', 'True', 'False', '1', '0', ' true ', 'yes'):
                with self.subTest(invalid=invalid), patch.dict(os.environ,
                        dict(environment, WILDFIRE_TOPOGRAPHIC_SLOPE_ENABLED=invalid), clear=True):
                    with self.assertRaises(ValueError):
                        create_app()
            for missing in environment:
                incomplete = {key: value for key, value in environment.items() if key != missing}
                with self.subTest(missing=missing), patch.dict(os.environ, incomplete, clear=True):
                    with self.assertRaises(ValueError):
                        TopographicEnvironment(allow_synthetic_conditions=True, slope_enabled=False)

    def test_discovery_and_reset_default_are_topographic_only(self):
        adapter = self.adapter()
        self.assertFalse(adapter.SUPPORTS_CONCURRENT_SESSIONS)
        self.assertEqual(adapter.list_splits(), ['development'])
        self.assertEqual(adapter.num_tasks('development'), 6)
        self.assertEqual(adapter.get_task_range('development'),
                         [dict(id=f'topo-dev-{index:03}', split='development') for index in range(6)])
        self.assertEqual(adapter.get_task('development', 0)['id'], CASE_IDS[0])
        self.assertNotIn(BOUNDARY_CASE_ID, [task['id'] for task in adapter.list_tasks('development')])
        for split in ('train', 'validation', 'test', 'diagnostic'):
            with self.subTest(split=split), self.assertRaises(ValueError):
                adapter.list_tasks(split)
        for index in (-1, 6, True):
            with self.subTest(index=index), self.assertRaises(ValueError):
                adapter.get_task('development', index)
        self.assertEqual(adapter.reset().data['task_id'], CASE_IDS[0])
        with self.assertRaises(ValueError):
            adapter.reset(task_id='prepared-dev-000')
        with self.assertRaises(ValueError):
            adapter.reset(unknown=True)

    def test_reuses_prepared_wire_models_and_public_state(self):
        import wildfire_openenv.topographic as transport
        self.assertIs(transport.PreparedAction, PreparedAction)
        self.assertIs(transport.PreparedClient, PreparedClient)
        self.assertIs(transport.PreparedObservation, PreparedObservation)
        self.assertIs(transport.PreparedState, PreparedState)
        adapter = self.adapter()
        self.assertEqual(adapter.state.step_count, 0)
        with self.assertRaises(RuntimeError):
            adapter.step(PreparedAction(operation='trajectory'))
        local = self.core()
        local.reset()
        adapter.reset(episode_id='custom-episode')
        for remaining in (2, 1, 0):
            check = local.check_orders(dict(clarifications=['unknown']))
            remote = adapter.step(PreparedAction(operation='check', clarifications=['unknown']))
            self.assertEqual(remote.metadata, {'action_check': check})
            self.assertEqual(check['remaining_checks'], remaining)
        with self.assertRaises(ValueError):
            adapter.step(PreparedAction(operation='check'))
        before = adapter.state.model_dump()
        for operation in ('situation', 'trajectory'):
            remote = adapter.step(PreparedAction(operation=operation))
            self.assertEqual(remote.data, local.get_situation())
            self.assertEqual(remote.reward, 0.0)
            self.assertEqual(adapter.state.model_dump(), before)
        self.assertEqual(before, dict(episode_id='custom-episode', step_count=0, minute=0,
                                      terminated=False, truncated=False))
        with self.assertRaises(ValueError):
            adapter.step(PreparedAction(), unknown=True)

    def test_invalid_commits_match_core_atomic_failure(self):
        local, adapter = self.core(), self.adapter()
        local.reset()
        adapter.reset()
        for decision in range(3):
            observation, reward, terminated, truncated, info = local.step(dict(clarifications=['unknown']))
            remote = adapter.step(PreparedAction(clarifications=['unknown']))
            self.assertEqual(remote.data, observation)
            self.assertEqual(remote.metadata, info)
            self.assertEqual(remote.reward, reward)
            self.assertEqual(remote.done, terminated or truncated)
            self.assertEqual(adapter.state.step_count, decision + 1)
        self.assertTrue(remote.truncated)

    def test_complete_six_cases_for_both_slope_settings(self):
        from eval.check_topographic_openenv import replay_export
        for slope_enabled in (False, True):
            for identifier in CASE_IDS:
                with self.subTest(slope_enabled=slope_enabled, identifier=identifier):
                    local, adapter = self.core(slope_enabled), self.adapter(slope_enabled)
                    observation, info = local.reset(identifier)
                    remote = adapter.reset(task_id=identifier)
                    self.assertEqual(remote.data, observation)
                    self.assertEqual(remote.metadata, info)
                    actor = ProtectionBaseline(prefer_shelter=False)
                    total_reward = 0.0
                    for decision in range(60):
                        action = actor(observation)
                        check = local.check_orders(action)
                        remote_check = adapter.step(PreparedAction(operation='check', **action))
                        self.assertEqual(remote_check.metadata, {'action_check': check})
                        self.assertTrue(check['valid'], check)
                        observation, reward, terminated, truncated, info = local.step(action)
                        remote = adapter.step(PreparedAction(**action))
                        self.assertEqual(remote.data, observation)
                        self.assertEqual(remote.metadata, info)
                        self.assertEqual(remote.reward, reward)
                        self.assertEqual(remote.done, terminated or truncated)
                        self.assertEqual(remote.terminated, terminated)
                        self.assertEqual(remote.truncated, truncated)
                        total_reward += reward
                        if remote.done:
                            break
                    self.assertTrue(remote.done)
                    trace = adapter.step(PreparedAction(operation='trajectory')).trajectory
                    self.assertEqual(trace, local.trajectory())
                    exported = json.loads(json.dumps(trace, allow_nan=False))
                    self.assertEqual(exported, trace)
                    self.assertEqual(total_reward, local.reward)
                    self.assertEqual(adapter.core.verify_episode(), local.verify_episode())
                    self.assertEqual(replay_export(exported, lambda: self.core(slope_enabled)),
                                     local.verify_episode())
                    changed = copy.deepcopy(exported)
                    changed['transitions'][-1]['objective_reward'] += 1
                    with self.assertRaises(RuntimeError):
                        replay_export(changed, lambda: self.core(slope_enabled))

    def test_boundary_probe_is_explicit_diagnostic_zero_and_not_trainable(self):
        for slope_enabled in (False, True):
            adapter = self.adapter(slope_enabled)
            adapter.reset(task_id=BOUNDARY_CASE_ID)
            remote = adapter.step(PreparedAction())
            self.assertTrue(remote.done)
            self.assertFalse(remote.terminated)
            self.assertTrue(remote.truncated)
            self.assertEqual(remote.reward, 0.0)
            self.assertEqual(adapter.state.step_count, 1)
            self.assertEqual(remote.metadata['boundary_contact_at'], 2)
            trace = adapter.step(PreparedAction(operation='trajectory')).trajectory
            self.assertIs(trace['transitions'][-1]['observation']['topographic_status']['boundary_censored'], True)
            self.assertIs(remote.metadata['suitable_for_training'], False)
            verification = adapter.core.verify_episode()
            self.assertTrue(verification['passed'])
            self.assertIs(verification['suitable_for_training'], False)

    def test_app_factory_keeps_sessions_independent_and_explicit_mode(self):
        with patch('wildfire_openenv.topographic.create_openenv_app') as server:
            create_app(slope_enabled=False)
            factory = server.call_args.args[0]
            self.assertEqual(server.call_args.args[1:], (PreparedAction, PreparedObservation))
            self.assertEqual(server.call_args.kwargs['max_concurrent_envs'], 1)
            self.assertEqual(server.call_args.kwargs['mode'], 'simulation')
            self.assertIs(server.call_args.kwargs['state_cls'], PreparedState)
            with patch('wildfire_openenv.topographic.TopographicEnvironment',
                       side_effect=lambda **kwargs: self.adapter(**kwargs)) as constructor:
                first, second = factory(), factory()
                self.assertFalse(constructor.call_args.kwargs['slope_enabled'])
        initial = second.reset().data
        first.reset()
        first.step(PreparedAction())
        self.assertEqual(second.state.minute, 0)
        self.assertEqual(second.core.get_situation(), initial)
        self.assertEqual(first.reset().data, initial)

    def test_cli_requires_explicit_mode_slope_and_localhost(self):
        for arguments in ([], ['--mode', 'simulation'], ['--slope-enabled', 'false'],
                          ['--mode', 'production', '--slope-enabled', 'true'],
                          ['--mode', 'simulation', '--slope-enabled', 'true', '--host', '0.0.0.0']):
            with self.subTest(arguments=arguments), self.assertRaises(SystemExit) as error:
                main(arguments)
            self.assertEqual(error.exception.code, 2)
        with patch('wildfire_openenv.topographic.create_app') as app, patch('uvicorn.run') as run:
            main(['--mode', 'simulation', '--slope-enabled', 'false'])
            app.assert_called_once_with(slope_enabled=False)
            run.assert_called_once_with(app.return_value, host='127.0.0.1', port=8003)


if __name__ == '__main__':
    unittest.main()
