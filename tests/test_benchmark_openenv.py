import unittest
from unittest.mock import patch


try:
    from wildfire_core.benchmark_v1 import BenchmarkEnv
    from wildfire_openenv.benchmark import BenchmarkAction, BenchmarkEnvironment, create_app
    from wildfire_openenv.prepared import PreparedEnvironment, PreparedObservation, PreparedState
    from envs.wildfire_response.tests.test_topographic_development import fixture_domain, fixture_inputs
    AVAILABLE = True
except ModuleNotFoundError as error:
    if error.name not in ('wildfire_core', 'wildfire_core.benchmark_v1', 'wildfire_openenv',
                          'openenv', 'uvicorn', 'pydantic'):
        raise
    AVAILABLE = False


@unittest.skipUnless(AVAILABLE, 'Install the existing optional OpenEnv test runtime')
class BenchmarkOpenEnvTests(unittest.TestCase):
    def setUp(self):
        self.prepared = fixture_inputs()
        self.domain = fixture_domain(self.prepared)
        self.options = dict(prepared_inputs=self.prepared, topographic_inputs=self.domain,
            expected_prepared_sha256=self.prepared['sha256'],
            expected_topographic_sha256=self.domain['sha256'],
            slope_enabled=True, allow_synthetic_conditions=True)

    def adapter(self, **options):
        return BenchmarkEnvironment(**dict(self.options, **options))

    def local(self):
        return BenchmarkEnv(**self.options)

    def task_id(self, environment):
        return next(group['cases'][0]['id'] for group in environment.catalog['groups']
                    if group['split'] == 'train' and group['family'] == 'false_alarm')

    def test_reuses_prepared_transport_and_discovery_never_exposes_private_factors(self):
        adapter = self.adapter()
        self.assertIsInstance(adapter, PreparedEnvironment)
        self.assertFalse(adapter.SUPPORTS_CONCURRENT_SESSIONS)
        self.assertEqual(adapter.list_splits(), ['train', 'validation', 'test', 'development'])
        for split, size in (('train', 160), ('validation', 40), ('test', 40), ('development', 12)):
            tasks = adapter.list_tasks(split)
            self.assertEqual(len(tasks), size)
            self.assertTrue(all(set(task) == {'id', 'split'} and task['split'] == split for task in tasks))
            self.assertEqual(adapter.num_tasks(split), size)
            self.assertEqual(adapter.get_task_range(split, 0, 2), tasks[:2])
            self.assertEqual(adapter.get_task(split, 0), tasks[0])
        for invalid in ('diagnostic', '', 'TRAIN'):
            with self.assertRaises(ValueError):
                adapter.list_tasks(invalid)
        for invalid in (True, -1, 160):
            with self.assertRaises(ValueError):
                adapter.get_task('train', invalid)
        for start, stop in ((True, 2), (-1, 2), (0, 161)):
            with self.assertRaises(ValueError):
                adapter.get_task_range('train', start, stop)

    def test_pins_and_explicit_optin_fail_closed(self):
        for field in ('expected_prepared_sha256', 'expected_topographic_sha256', 'expected_catalog_sha256'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.adapter(**{field: '0' * 64})
        with self.assertRaises(ValueError):
            self.adapter(allow_synthetic_conditions=False)
        with self.assertRaises(ValueError):
            self.adapter(profile='unfrozen-profile')

    def test_bounded_report_event_queries_validate_before_execution(self):
        for payload in (dict(operation='events', limit=21), dict(operation='reports', limit=True),
                        dict(operation='reports', report_ids=['report'] * 21),
                        dict(operation='events', event_ids=['event'] * 21),
                        dict(operation='events', after_cursor=-1), dict(operation='events', after_cursor=True),
                        dict(operation='events', event_ids=['event'], after_cursor=1),
                        dict(operation='reports', event_ids=['event']), dict(operation='advance', after_cursor=0),
                        dict(operation='events', requests=[dict(resource_id='unit', sector_id='sector', mission='recon')]),
                        dict(operation='advance', limit=2), dict(operation='events', report_ids=['report']),
                        dict(operation='situation', releases=['unit'])):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                BenchmarkAction(**payload)
        adapter = self.adapter()
        with self.assertRaises(RuntimeError):
            adapter.step(BenchmarkAction(operation='reports'))
        adapter.reset(task_id=self.task_id(adapter.core))
        before = adapter.state.model_dump()
        for operation in ('reports', 'events'):
            with patch.object(adapter.core, 'get_' + operation, return_value=[]) as query:
                result = adapter.step(BenchmarkAction(operation=operation, limit=2))
                self.assertEqual(query.call_args.kwargs['limit'], 2)
                self.assertEqual(result.data, {operation: [], 'next_cursor': 0} if operation == 'events' else {operation: []})
                self.assertEqual(result.reward, 0)
        self.assertEqual(adapter.state.model_dump(), before)

    def test_real_event_stream_paginates_past_twenty_and_rejects_future_ids(self):
        adapter = self.adapter()
        adapter.reset(task_id=self.task_id(adapter.core))
        core = adapter.core._core

        def emit(index):
            core.incidents._emit(core, f'pagination-fixture-{index}', index,
                kind='resource_status_update', source='fixture-radio', role='responder',
                channel='synthetic_radio', location=[2, 2], text=f'Fixture event {index}', status='resolved')

        for index in range(25):
            emit(index)
        with self.assertRaises(ValueError):
            adapter.step(BenchmarkAction(operation='events', event_ids=['incident-event-1']))
        adapter.step(BenchmarkAction())
        expected = adapter.core.get_events()
        self.assertEqual(len(expected), 25)
        before = adapter.state.model_dump()
        first = adapter.step(BenchmarkAction(operation='events', limit=20)).data
        self.assertEqual(first, dict(events=expected[:20], next_cursor=20))
        second = adapter.step(BenchmarkAction(operation='events', after_cursor=first['next_cursor'], limit=20)).data
        self.assertEqual(second, dict(events=expected[20:], next_cursor=25))
        self.assertEqual(adapter.step(BenchmarkAction(operation='events', after_cursor=25)).data,
                         dict(events=[], next_cursor=25))
        identifiers = [expected[0]['id'], expected[-1]['id']]
        selected = adapter.step(BenchmarkAction(operation='events', event_ids=identifiers))
        self.assertEqual(selected.data, dict(events=adapter.core.get_events(identifiers), next_cursor=None))
        self.assertEqual(BenchmarkAction(operation='events', after_cursor=20, limit=2).core_action(), {})
        self.assertEqual(BenchmarkAction(operation='events', event_ids=identifiers).core_action(), {})
        emit(25)
        for action in (BenchmarkAction(operation='events', event_ids=['incident-event-26']),
                       BenchmarkAction(operation='events', after_cursor=26)):
            with self.assertRaises(ValueError):
                adapter.step(action)
        self.assertEqual(adapter.state.model_dump(), before)
        adapter.step(BenchmarkAction())
        next_batch = adapter.step(BenchmarkAction(operation='events', after_cursor=25)).data
        self.assertEqual([event['id'] for event in next_batch['events']], ['incident-event-26'])
        self.assertEqual(next_batch['next_cursor'], 26)

    def test_local_adapter_matches_direct_core_full_episode_and_invalid_commits(self):
        local, adapter = self.local(), self.adapter()
        task_id = self.task_id(local)
        observation, info = local.reset(task_id)
        remote = adapter.reset(task_id=task_id)
        self.assertEqual((remote.data, remote.metadata), (observation, info))
        for decision in range(60):
            checked = local.check_orders({})
            self.assertEqual(adapter.step(BenchmarkAction(operation='check')).metadata['action_check'], checked)
            expected = local.step({})
            result = adapter.step(BenchmarkAction())
            self.assertEqual((result.data, result.reward, result.terminated, result.truncated, result.metadata), expected)
            if result.done:
                break
        self.assertTrue(result.done)
        self.assertEqual(adapter.state.minute, 120)
        self.assertEqual(adapter.step(BenchmarkAction(operation='trajectory')).trajectory, local.trajectory())
        self.assertEqual(adapter.core.verify_episode(), local.verify_episode())
        local.reset(task_id)
        adapter.reset(task_id=task_id)
        for decision in range(3):
            expected = local.step(dict(clarifications=['unknown']))
            result = adapter.step(BenchmarkAction(clarifications=['unknown']))
            self.assertEqual((result.data, result.reward, result.terminated, result.truncated, result.metadata), expected)
        self.assertTrue(result.truncated)

    def test_reset_isolation_and_read_state_cannot_mutate_active_episode(self):
        adapter = self.adapter()
        initial = adapter.reset()
        state = adapter.state
        state.minute = 100
        self.assertEqual(adapter.state.minute, 0)
        adapter.step(BenchmarkAction())
        self.assertEqual(adapter.reset().data, initial.data)
        self.assertEqual(set(adapter.state.model_dump()),
                         {'episode_id', 'step_count', 'minute', 'terminated', 'truncated'})
        with self.assertRaises(ValueError):
            adapter.reset(unknown=True)
        with self.assertRaises(ValueError):
            adapter.step(BenchmarkAction(), unknown=True)

    def test_server_factory_is_single_session_and_reuses_prepared_wire_types(self):
        options = {key: value for key, value in self.options.items() if key not in ('slope_enabled', 'allow_synthetic_conditions')}
        with patch('wildfire_openenv.benchmark.create_openenv_app') as server:
            create_app(slope_enabled=True, **options)
        self.assertEqual(server.call_args.args[1:], (BenchmarkAction, PreparedObservation))
        self.assertIs(server.call_args.kwargs['state_cls'], PreparedState)
        self.assertEqual(server.call_args.kwargs['max_concurrent_envs'], 1)
        self.assertEqual(server.call_args.kwargs['mode'], 'simulation')
        self.assertIsInstance(server.call_args.args[0](), BenchmarkEnvironment)

    def test_testclient_persistent_server_matches_same_direct_action_sequence_and_reward(self):
        from fastapi.testclient import TestClient
        local = self.local()
        task_id = self.task_id(local)
        initial, info = local.reset(task_id)
        options = {key: value for key, value in self.options.items() if key not in ('slope_enabled', 'allow_synthetic_conditions')}
        app = create_app(slope_enabled=True, **options)
        with TestClient(app) as client, client.websocket_connect('/ws') as websocket:
            websocket.send_json(dict(type='reset', data=dict(task_id=task_id)))
            reset = websocket.receive_json()
            self.assertEqual(reset['type'], 'observation', reset)
            self.assertEqual(reset['data']['observation']['data'], initial)
            self.assertEqual(reset['data']['metadata'], info)
            for decision in range(60):
                websocket.send_json(dict(type='step', data=dict(operation='advance')))
                response = websocket.receive_json()
                self.assertEqual(response['type'], 'observation', response)
                expected = local.step({})
                data = response['data']
                self.assertEqual(data['observation']['data'], expected[0])
                self.assertEqual(data['reward'], expected[1])
                self.assertEqual(data['done'], expected[2] or expected[3])
                self.assertEqual(data['metadata'], expected[4])
                if data['done']:
                    break
            self.assertTrue(data['done'])
            websocket.send_json(dict(type='step', data=dict(operation='trajectory')))
            trace = websocket.receive_json()['data']['observation']['trajectory']
            self.assertEqual(trace, local.trajectory())
            websocket.send_json(dict(type='state'))
            self.assertEqual(websocket.receive_json()['data']['minute'], 120)


if __name__ == '__main__':
    unittest.main()
