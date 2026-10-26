import base64
import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

try:
    import httpx
    from fastapi.testclient import TestClient
    from eval.ui_server import MAX_IMPORT_BYTES, SMOKE_GROUP_INDICES, create_app, main
    from wildfire_core.stress import StressEnv, build_catalog
    from wildfire_core.topographic import CASE_IDS, ProtectionBaseline, TopographicEnv
    from envs.wildfire_response.tests.test_prepared_development import fixture_inputs
    from envs.wildfire_response.tests.test_topographic_development import fixture_domain
    AVAILABLE = True
except ModuleNotFoundError as error:
    if error.name not in ('fastapi', 'starlette', 'pydantic', 'httpx', 'httpx2', 'wildfire_core'):
        raise
    AVAILABLE = False


PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aXZkAAAAASUVORK5CYII=')
TERRAIN = dict(png=PNG, extent=[297000, 4036500, 315500, 4054500],
               crs='EPSG:25830', source_label='Fixture terrain')
ACTION_FIELDS = ('requests', 'authorizations', 'releases', 'messages', 'readback_confirmations',
                 'readback_corrections', 'triage', 'clarifications', 'protection_orders')
PRIVATE_FIELDS = {'world_fire', 'evaluator_evidence', 'core_accounting', 'protection_checkpoint',
                  'factors', 'future_events', 'configuration_sha256'}


@unittest.skipUnless(AVAILABLE, 'Install the optional UI dependencies and wildfire core')
class UIServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prepared = fixture_inputs()
        cls.domain = fixture_domain(cls.prepared)
        cls.catalog = build_catalog(cls.prepared, cls.domain)

    def setUp(self):
        self.app = self.make_app()
        self.client = TestClient(self.app, base_url='http://127.0.0.1:8767')
        self.addCleanup(self.client.close)

    def options(self):
        return dict(expected_prepared_sha256=self.prepared['sha256'],
                    expected_topographic_sha256=self.domain['sha256'])

    def make_app(self, **kwargs):
        return create_app(self.prepared, self.domain, **dict(self.options(), terrain=TERRAIN, **kwargs))

    def core(self, task_id=None):
        task_id = CASE_IDS[0] if task_id is None else task_id
        options = dict(self.options(), slope_enabled=True, allow_synthetic_conditions=True)
        if task_id.startswith('stress-'):
            environment = StressEnv(self.prepared, self.domain, self.catalog,
                expected_catalog_sha256=self.catalog['sha256'], **options)
        else:
            environment = TopographicEnv(self.prepared, self.domain, **options)
        environment.reset(task_id)
        return environment

    def state(self):
        return self.client.get('/api/state').json()

    def test_reactive_case_api_delivers_updates_and_replays_export(self):
        from wildfire_core.incident import ReactiveOpsEnv
        environment = ReactiveOpsEnv(self.prepared, self.domain,
                                    **self.options(), slope_enabled=True, allow_synthetic_conditions=True)
        initial, _ = environment.reset('incident-dev-000')
        response = self.client.post('/api/reset', json=dict(task_id='incident-dev-000'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['observation'], initial)
        expected = environment.step({})
        actual = self.submit({}).json()
        self.assertEqual(actual['observation'], expected[0])
        self.assertTrue(actual['observation']['incident_events'])
        self.assert_public(actual)
        self.client.post('/api/reset', json=dict(task_id='incident-dev-001'))
        environment.reset('incident-dev-001')
        while True:
            expected = environment.step({})
            actual = self.submit({}).json()
            self.assertEqual(actual['observation'], expected[0])
            if expected[2] or expected[3]:
                break
        imported = self.client.post('/api/import', json=dict(trajectory=environment.trajectory()))
        self.assertEqual(imported.status_code, 200)
        self.assertTrue(imported.json()['read_only'])

    def test_terrain_validation_failure_is_explicit_and_never_silently_filled(self):
        app = create_app(self.prepared, self.domain, **self.options())
        with TestClient(app, base_url='http://127.0.0.1:8767') as client:
            with patch('eval.ui_terrain.render_terrain', side_effect=ValueError('changed source')):
                response = client.get('/api/map')
                self.assertEqual(response.status_code, 503)
                self.assertIn('failed validation', response.json()['detail'])
            self.assertEqual(client.get('/api/state').json()['observation']['minute'], 0)

    def token(self, state=None):
        state = self.state() if state is None else state
        return dict(episode_id=state['episode_id'], expected_decision=state['observation']['decision'])

    def submit(self, action, state=None):
        return self.client.post('/api/action', json=dict(self.token(state), action=action))

    def assert_public(self, value):
        if isinstance(value, dict):
            self.assertFalse(PRIVATE_FIELDS.intersection(value), PRIVATE_FIELDS.intersection(value))
            for item in value.values():
                self.assert_public(item)
        elif isinstance(value, list):
            for item in value:
                self.assert_public(item)

    def assert_step(self, actual, expected, environment, action):
        observation, reward, terminated, truncated, info = expected
        self.assertEqual(actual['observation'], observation)
        self.assertEqual(actual['reward'], reward)
        self.assertEqual(actual['total_reward'], environment.reward)
        self.assertEqual(actual['terminated'], terminated)
        self.assertEqual(actual['truncated'], truncated)
        self.assertEqual(actual['info'], info)
        self.assertEqual(actual['last_action'], action)

    def finish_invalid(self):
        for _ in range(3):
            response = self.submit({'invalid': True})
            self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(self.state()['truncated'])
        return self.client.get('/api/trajectory').json()

    def test_original_json_import_keeps_float_tokens_and_live_state(self):
        trace = self.finish_invalid()
        original = json.dumps(trace)
        current = self.state()
        response = self.client.post('/api/import', json={'trajectory_json': original})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['read_only'])
        self.assertEqual(self.state(), current)

        def browser_numbers(value):
            if isinstance(value, dict):
                return {key: browser_numbers(item) for key, item in value.items()}
            if isinstance(value, list):
                return [browser_numbers(item) for item in value]
            if type(value) is float and value.is_integer():
                return int(value)
            return value

        changed = browser_numbers(trace)
        self.assertNotEqual(json.dumps(changed), original)
        self.assertEqual(self.client.post('/api/import', json={'trajectory': changed}).status_code, 422)

    def test_original_json_import_rejects_ambiguous_and_malformed_payloads(self):
        for body in ({}, {'trajectory': {}, 'trajectory_json': '{}'},
                     {'trajectory_json': '[]'}, {'trajectory_json': '{"number": NaN}'},
                     {'trajectory_json': '{"duplicate":1,"duplicate":2}'}):
            self.assertEqual(self.client.post('/api/import', json=body).status_code, 422)
        self.assertEqual(self.state()['observation']['minute'], 0)

    def test_config_initial_state_map_and_caller_pins(self):
        config = self.client.get('/api/config').json()
        from wildfire_core.incident import CASE_IDS as INCIDENT_CASE_IDS
        expected_cases = ([dict(id=identifier, mode='development') for identifier in CASE_IDS]
            + [dict(id=identifier, mode='reactive') for identifier in INCIDENT_CASE_IDS]
            + [dict(id=self.catalog['groups'][index]['cases'][0]['id'], mode='stress')
               for index in SMOKE_GROUP_INDICES])
        self.assertEqual(config, dict(cases=expected_cases, ticks_per_action=2,
                                    max_decisions=60, evaluation_available=False))
        initial = self.state()
        self.assertEqual(initial['observation'], self.core().get_situation())
        self.assertEqual({key: initial[key] for key in ('reward', 'total_reward', 'terminated',
            'truncated', 'info', 'last_action')}, dict(reward=0, total_reward=0, terminated=False,
                                                    truncated=False, info={}, last_action=None))
        self.assertEqual(self.client.get('/api/map').json(), dict(extent=TERRAIN['extent'],
            crs='EPSG:25830', image_url='/api/map.png', source_label=TERRAIN['source_label']))
        response = self.client.get('/api/map.png?path=/etc/passwd')
        self.assertEqual(response.content, PNG)
        self.assertEqual(response.headers['content-type'], 'image/png')
        for field in ('expected_prepared_sha256', 'expected_topographic_sha256', 'expected_catalog_sha256'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                create_app(self.prepared, self.domain, **dict(self.options(), terrain=TERRAIN,
                                                            **{field: '0' * 64}))
        self.make_app(expected_catalog_sha256=self.catalog['sha256'])

    def test_terrain_renderer_is_lazy_and_cached(self):
        with patch.dict(sys.modules, {'eval.ui_terrain': SimpleNamespace(render_terrain=lambda *args: TERRAIN)}):
            app = create_app(self.prepared, self.domain, **self.options())
            with TestClient(app, base_url='http://localhost') as client:
                self.assertEqual(client.get('/api/map.png').content, PNG)
                self.assertEqual(client.get('/api/map').json()['extent'], TERRAIN['extent'])

    def test_full_raw_combined_action_matches_core_and_history(self):
        environment = self.core()
        action = {field: [] for field in ACTION_FIELDS}
        response = self.submit(action)
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_step(response.json(), environment.step(action), environment, action)
        self.assertIsNone(response.json()['info']['error'])
        self.assertEqual(self.client.get('/api/trajectory').json(), environment.trajectory())
        history = self.client.get('/api/history').json()
        self.assertEqual(history['live_decision'], 1)
        self.assertEqual(len(history['frames']), 2)
        self.assertIsNone(history['frames'][0]['action'])
        self.assertEqual(history['frames'][1]['action'], action)
        self.assertEqual(history['frames'][1]['observation'], response.json()['observation'])
        self.assertEqual(self.client.get('/api/history?after=0').json()['frames'], history['frames'][1:])
        self.assertEqual(self.state(), response.json())

    def test_every_action_field_uses_core_validation_and_penalty_semantics(self):
        for field in (*ACTION_FIELDS, 'unknown'):
            with self.subTest(field=field):
                self.client.post('/api/reset', json={'task_id': CASE_IDS[0]})
                environment = self.core()
                action = {'messages': [], field: {'bad': True}}
                response = self.submit(action)
                self.assertEqual(response.status_code, 200, response.text)
                self.assert_step(response.json(), environment.step(action), environment, action)
                self.assertIsNotNone(response.json()['info']['error'])
                self.assertEqual(response.json()['observation']['minute'], 2)
        self.client.post('/api/reset', json={'task_id': CASE_IDS[0]})
        self.finish_invalid()

    def test_checks_preserve_budget_and_time(self):
        environment = self.core()
        initial = self.state()
        action = {'clarifications': ['unknown']}
        for remaining in (2, 1, 0):
            response = self.client.post('/api/check', json=dict(self.token(initial), action=action))
            self.assertEqual(response.json(), dict(episode_id=initial['episode_id'], decision=0,
                                                  action_check=environment.check_orders(action)))
            self.assertEqual(response.json()['action_check']['remaining_checks'], remaining)
        self.assertEqual(self.client.post('/api/check', json=dict(self.token(), action={})).status_code, 422)
        self.assertEqual(self.state(), initial)
        self.assertEqual(len(self.client.get('/api/history').json()['frames']), 1)
        self.submit({})
        self.assertEqual(self.client.post('/api/check', json=dict(self.token(), action={})).json()
                         ['action_check']['remaining_checks'], 2)

    def test_stale_requests_after_step_and_reset_never_advance(self):
        old = self.token()
        self.submit({})
        current = self.state()
        for route, extra in (('action', {'action': {}}), ('advance', {'policy': 'passive'}),
                             ('check', {'action': {}}), ('verify', {})):
            with self.subTest(route=route):
                self.assertEqual(self.client.post('/api/' + route, json=dict(old, **extra)).status_code, 409)
        self.assertEqual(self.state(), current)
        reset = self.client.post('/api/reset', json={'task_id': CASE_IDS[0]}).json()
        self.assertNotEqual(old['episode_id'], reset['episode_id'])
        for route, extra in (('action', {'action': {}}), ('advance', {'policy': 'passive'}),
                             ('check', {'action': {}}), ('verify', {})):
            self.assertEqual(self.client.post('/api/' + route, json=dict(old, **extra)).status_code, 409)
        self.assertEqual(self.state(), reset)

    def test_concurrent_same_decision_has_only_one_commit(self):
        payload = dict(self.token(), action={})
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(lambda unused: self.client.post('/api/action', json=payload), range(2)))
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        self.assertEqual(self.state()['observation']['decision'], 1)

    def test_baselines_and_resets_are_persistent_and_isolated(self):
        for policy_name in ('passive', 'evacuate', 'shelter'):
            with self.subTest(policy=policy_name):
                initial = self.client.post('/api/reset', json={'task_id': CASE_IDS[0]}).json()
                environment = self.core()
                policy = ProtectionBaseline(prefer_shelter=policy_name == 'shelter')
                observation = environment.get_situation()
                for _ in range(8):
                    action = {} if policy_name == 'passive' else policy(observation)
                    response = self.client.post('/api/advance', json=dict(self.token(), policy=policy_name))
                    self.assertEqual(response.status_code, 200, response.text)
                    expected = environment.step(action)
                    self.assert_step(response.json(), expected, environment, action)
                    observation = expected[0]
                reset = self.client.post('/api/reset', json={'task_id': CASE_IDS[0]}).json()
                self.assertEqual(reset['observation'], initial['observation'])
                self.assertIsNone(reset['last_action'])
                self.assertEqual(reset['total_reward'], 0)
                self.assertEqual(len(self.client.get('/api/history').json()['frames']), 1)
                self.assertEqual(self.client.get('/api/trajectory').json()['transitions'], [])

    def test_allowlist_stress_core_parity_and_excluded_tasks(self):
        for index in SMOKE_GROUP_INDICES:
            task_id = self.catalog['groups'][index]['cases'][0]['id']
            with self.subTest(task_id=task_id):
                response = self.client.post('/api/reset', json={'task_id': task_id})
                self.assertEqual(response.status_code, 200, response.text)
                environment = self.core(task_id)
                self.assertEqual(response.json()['observation'], environment.get_situation())
                self.assert_step(self.submit({}).json(), environment.step({}), environment, {})
        previous = self.state()
        excluded = ['topo-boundary-probe', 'prepared-dev-000', 'unknown']
        excluded += [self.catalog['groups'][index]['cases'][variant]['id']
                     for index, variant in ((0, 1), (6, 0), (80, 0), (100, 0))]
        for task_id in excluded:
            self.assertEqual(self.client.post('/api/reset', json={'task_id': task_id}).status_code, 422)
        self.assertEqual(self.state(), previous)

    def test_transport_errors_never_advance_or_consume_checks(self):
        initial = self.state()
        payload = dict(self.token(), action={})
        invalid = [dict(payload, expected_decision=True), dict(payload, expected_decision='0'),
                   dict(payload, action=[]), dict(payload, extra=True), {}, None]
        for body in invalid:
            self.assertEqual(self.client.post('/api/action', json=body).status_code, 422)
        for body in ('{', '{"action":{},"action":{}}',
                     json.dumps(payload).replace('"action": {}', '"action": {"x": NaN}'),
                     json.dumps(payload).replace('"action": {}', '"action": {"x": 1e999}')):
            self.assertEqual(self.client.post('/api/action', content=body).status_code, 422)
        self.assertEqual(self.client.post('/api/advance', json=dict(self.token(), policy='other')).status_code, 422)
        self.assertEqual(self.state(), initial)
        response = self.client.post('/api/check', json=dict(self.token(), action={}))
        self.assertEqual(response.json()['action_check']['remaining_checks'], 2)

    def test_local_guards_and_streamed_body_limits(self):
        initial = self.state()
        for headers in ({'Host': 'evil.example'}, {'Host': '127.0.0.1.evil.example'},
                        {'Origin': 'https://evil.example'}, {'Origin': 'null'},
                        {'Origin': 'http://127.0.0.1@evil.example'}, {'Sec-Fetch-Site': 'cross-site'}):
            self.assertEqual(self.client.post('/api/reset', headers=headers,
                json={'task_id': CASE_IDS[0]}).status_code, 403)
        self.assertEqual(self.client.get('/api/state', headers={'Host': 'evil.example'}).status_code, 403)
        self.assertEqual(self.client.post('/api/action', json=dict(self.token(), action={}),
            headers={'Origin': 'http://localhost:5173'}).status_code, 200)
        current = self.state()
        self.assertEqual(self.client.post('/api/import', content=b'{}',
            headers={'Content-Length': str(MAX_IMPORT_BYTES + 1)}).status_code, 413)
        with patch('eval.ui_server.MAX_IMPORT_BYTES', 100):
            self.assertEqual(self.client.post('/api/import', content=iter([b' ' * 60, b' ' * 60])).status_code, 413)
        self.assertEqual(self.state(), current)
        self.assertEqual(initial['observation']['decision'], 0)

    def test_import_verified_dev_and_stress_are_read_only(self):
        task_ids = (CASE_IDS[0], self.catalog['groups'][1]['cases'][0]['id'])
        for task_id in task_ids:
            with self.subTest(task_id=task_id):
                self.client.post('/api/reset', json={'task_id': task_id})
                trace = self.finish_invalid()
                self.client.post('/api/reset', json={'task_id': CASE_IDS[1]})
                before = self.state()
                history = self.client.get('/api/history').json()
                current_trace = self.client.get('/api/trajectory').json()
                response = self.client.post('/api/import', json={'trajectory': trace})
                self.assertEqual(response.status_code, 200, response.text)
                imported = response.json()
                self.assertEqual(set(imported), {'frames', 'task_id', 'total_reward', 'read_only'})
                self.assertEqual(imported['task_id'], task_id)
                self.assertTrue(imported['read_only'])
                self.assertEqual(len(imported['frames']), 4)
                self.assertEqual(imported['frames'][0]['observation'], trace['initial_observation'])
                self.assertTrue(imported['frames'][-1]['truncated'])
                self.assertEqual(imported['total_reward'], sum(frame['reward'] for frame in imported['frames']))
                self.assert_public(imported)
                self.assertEqual(self.state(), before)
                self.assertEqual(self.client.get('/api/history').json(), history)
                self.assertEqual(self.client.get('/api/trajectory').json(), current_trace)

    def test_import_rejects_incomplete_untrusted_tampered_and_held_out(self):
        incomplete = self.client.get('/api/trajectory').json()
        self.assertEqual(self.client.post('/api/import', json={'trajectory': incomplete}).status_code, 422)
        trace = self.finish_invalid()
        mutations = [lambda item: item.update(extra=True),
                     lambda item: item.update(prepared_inputs_sha256='0' * 64),
                     lambda item: item.update(topographic_inputs_sha256='0' * 64),
                     lambda item: item.update(slope_enabled=False),
                     lambda item: item.update(max_decisions=60000000),
                     lambda item: item.update(ticks_per_action=1),
                     lambda item: item.update(seed=0),
                     lambda item: item['transitions'][-1].update(objective_reward=999),
                     lambda item: item['transitions'][0].update(world_fire=[]),
                     lambda item: item['initial_observation'].update(world_fire=[]),
                     lambda item: item.update(transitions=item['transitions'][:1]),
                     lambda item: item.update(transitions=[None])]
        before = self.state()
        for mutate in mutations:
            changed = copy.deepcopy(trace)
            mutate(changed)
            self.assertEqual(self.client.post('/api/import', json={'trajectory': changed}).status_code, 422)
        for task_id in ('topo-boundary-probe', self.catalog['groups'][80]['cases'][0]['id'],
                        self.catalog['groups'][100]['cases'][0]['id'],
                        self.catalog['groups'][0]['cases'][1]['id']):
            changed = dict(trace, task_id=task_id)
            with patch('eval.ui_server.verify_topographic_trajectory') as dev_verify, \
                    patch('eval.ui_server.verify_stress_trajectory') as stress_verify:
                self.assertEqual(self.client.post('/api/import', json={'trajectory': changed}).status_code, 422)
                dev_verify.assert_not_called()
                stress_verify.assert_not_called()
        self.assertEqual(self.state(), before)

    def test_verification_only_after_done_and_public_only(self):
        self.assertEqual(self.client.post('/api/verify', json=self.token()).status_code, 409)
        self.finish_invalid()
        response = self.client.post('/api/verify', json=self.token())
        self.assertEqual(response.status_code, 200, response.text)
        environment = self.core()
        for _ in range(3):
            environment.step({'invalid': True})
        expected = environment.verify_episode()
        self.assertEqual(response.json(), dict(passed=expected['passed'],
            objective_score=expected['objective_score'], by_kind=expected['objective_result']['by_kind'],
            boundary_censored=expected['boundary_censored'], suitable_for_training=expected['suitable_for_training']))
        self.assert_public(response.json())
        before = self.state()
        self.assertEqual(self.submit({}).status_code, 409)
        self.assertEqual(self.client.post('/api/check', json=dict(self.token(), action={})).status_code, 409)
        self.assertEqual(self.state(), before)

    def test_truth_default_denial_and_evaluator_is_separate(self):
        self.assertEqual(self.client.get('/api/evaluator').status_code, 404)
        with TestClient(self.make_app(enable_evaluator=True), base_url='http://[::1]:8767') as client:
            self.assertTrue(client.get('/api/config').json()['evaluation_available'])
            initial = client.get('/api/state').json()
            response = client.get('/api/evaluator').json()
            self.assertEqual(set(response), {'episode_id', 'decision', 'world_fire'})
            self.assertEqual(response['episode_id'], initial['episode_id'])
            self.assertEqual(response['decision'], 0)
            environment = self.core()
            expected = [dict(column=column, row=row, heat=cell['heat'], burned=cell['burned'])
                for row, cells in enumerate(environment._core.sim.cells)
                for column, cell in enumerate(cells) if cell['heat'] > 0 or cell['burned'] > 0]
            self.assertEqual(response['world_fire'], expected)
            for endpoint in ('state', 'history', 'trajectory', 'map'):
                self.assert_public(client.get('/api/' + endpoint).json())
            self.assertEqual(client.get('/api/state').json(), initial)

    def test_static_files_only_fixed_ui_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'assets').mkdir()
            (directory / 'index.html').write_text('<html>local UI</html>')
            (directory / 'assets' / 'app.js').write_text('export {};')
            (directory / 'private.txt').write_text('private')
            (directory / 'assets' / 'leak.txt').symlink_to(directory / 'private.txt')
            with TestClient(self.make_app(dist=directory), base_url='http://localhost') as client:
                self.assertEqual(client.get('/').text, '<html>local UI</html>')
                self.assertEqual(client.get('/assets/app.js').status_code, 200)
                for path in ('/private.txt', '/.runtime/secret', '/assets/leak.txt',
                             '/assets/%2e%2e/private.txt', '/api/unknown'):
                    self.assertEqual(client.get(path).status_code, 404)
                self.assertEqual(client.get('/api/state').status_code, 200)

    def test_cli_pins_and_loopback_only(self):
        arguments = ['--prepared-inputs', 'prepared.json', '--prepared-sha256', self.prepared['sha256'],
            '--topographic-inputs', 'domain.json', '--topographic-sha256', self.domain['sha256']]
        with patch('eval.ui_server.create_app') as factory, patch('uvicorn.run') as run:
            main([*arguments, '--enable-evaluator', '--catalog-sha256', self.catalog['sha256']])
            factory.assert_called_once_with('prepared.json', 'domain.json', **self.options(),
                expected_catalog_sha256=self.catalog['sha256'], enable_evaluator=True, dist='ui/dist')
            run.assert_called_once_with(factory.return_value, host='127.0.0.1', port=8767)
        with self.assertRaises(SystemExit):
            main([*arguments, '--host', '0.0.0.0'])


if __name__ == '__main__':
    unittest.main()
