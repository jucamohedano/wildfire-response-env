import builtins
import copy
from contextlib import redirect_stdout
from dataclasses import FrozenInstanceError
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from train import benchmark as training


def fixture_catalog():
    groups = [dict(id='training-group', split='train', cases=[dict(id='train-0'), dict(id='train-1')])]
    groups.extend(dict(id=f'validation-group-{index}', split='validation', family=family,
        cases=[dict(id=f'validation-{index}-{variant}', variant=variant, seed=1000 + index * 10 + variant)
               for variant in range(2)]) for index, family in enumerate(training.READINESS_FAMILIES))
    groups.append(dict(id='later-uncertain-smoke', split='validation', family='uncertain_smoke',
        cases=[dict(id='validation-later-0', variant=0, seed=2000),
               dict(id='validation-later-1', variant=1, seed=2001)]))
    groups.append(dict(id='test-group', split='test', cases=[dict(id='test-0')]))
    return training.seal(dict(groups=groups, development=[dict(id='development-0')],
        prepared_inputs_sha256='1' * 64, topographic_inputs_sha256='2' * 64))


class FixtureCore:
    def __init__(self, *, invalid=False, invalid_decisions=(), censored=False, verified=True, suitable=True):
        self.catalog = fixture_catalog()
        self.invalid, self.censored = invalid, censored
        self.invalid_decisions = frozenset(invalid_decisions)
        self.verified, self.suitable = verified, suitable
        self.reset_calls = 0
        self.verify_calls = 0
        self.reward = 0.0

    def reset(self, task_id, seed=None):
        self.reset_calls += 1
        self.minute, self.task_id, self.seed = 0, task_id, seed
        self.actions = []
        self.action_validity = []
        self.invalid_streak = 0
        self.abandoned = False
        return self.get_situation(), dict(evaluator_truth='SECRET_RESET', suitable_for_training=True)

    def get_situation(self):
        return dict(minute=self.minute, task_id=self.task_id, sectors=[], resources=[], districts=[
            dict(id='district', home=[1, 1], population=10, smoke_present=None, exposed='SECRET_EXPOSURE')],
            sensor_memory=[], reports=[], readbacks=[], offers=[], active_incidents=[],
            evaluator_truth='SECRET_OBSERVATION', future_ignitions=['SECRET_FUTURE'])

    def step(self, action):
        self.actions.append(copy.deepcopy(action))
        self.minute += 2
        invalid = self.invalid or len(self.actions) in self.invalid_decisions or '_invalid_model_action' in action
        self.action_validity.append(not invalid)
        self.invalid_streak = self.invalid_streak + 1 if invalid else 0
        self.abandoned = self.invalid_streak >= 3
        terminated = self.minute == 120 and not self.abandoned and not self.censored
        self.reward = .75 if terminated else 0.0
        info = dict(error='invalid' if invalid else None, boundary_censored=self.censored,
                    suitable_for_training=not self.censored, evaluator_truth='SECRET_INFO')
        return self.get_situation(), self.reward, terminated, self.censored or self.abandoned, info

    def check_orders(self, action):
        return dict(valid=True, error=None, remaining_checks=2, evaluator_truth='SECRET_CHECK')

    def trajectory(self):
        return dict(task_id=self.task_id, seed=self.seed, catalog_sha256=self.catalog['sha256'],
            transitions=[dict(action=copy.deepcopy(action), action_valid=valid)
                         for action, valid in zip(self.actions, self.action_validity)])

    def verify_episode(self):
        self.verify_calls += 1
        return dict(passed=self.verified, suitable_for_training=self.suitable,
            outcomes=dict(boundary_censored=self.censored, invalid_abandonment=self.abandoned,
                          graded_score=self.reward), evaluator_truth='SECRET_VERIFY')


def wait_policy(context):
    return dict(name='advance_time', arguments={})


class TrainingGuardTests(unittest.TestCase):
    def test_all_nontraining_splits_rejected_before_reset(self):
        for task_id in ('validation-0-0', 'test-0', 'development-0', 'unknown'):
            core = FixtureCore()
            rollout = training.BenchmarkRollout(lambda: core)
            with self.subTest(task_id=task_id), self.assertRaises(ValueError):
                rollout.reset(task_id)
            self.assertEqual(core.reset_calls, 0)

    def test_tools_only_return_causal_actor_data_and_keep_unknowns(self):
        core = FixtureCore()
        rollout = training.BenchmarkRollout(lambda: core)
        initial = rollout.reset('train-0')
        checked = rollout.dispatch('check_action', {})
        committed = rollout.dispatch('advance_time', {})
        for public in (initial, checked, committed):
            self.assertNotIn('SECRET', public)
            self.assertNotIn('exposed', public)
            self.assertNotIn('outcomes', public)
        self.assertIsNone(json.loads(initial)['observation']['districts'][0]['smoke_present'])
        self.assertEqual(len(core.actions), 1)
        self.assertEqual(core.verify_calls, 0)
        with self.assertRaises(training.RolloutRejected):
            rollout.reset('train-1')

    def test_finalization_is_bounded_passive_and_reward_verified_once(self):
        core = FixtureCore()
        rollout = training.BenchmarkRollout(lambda: core)
        rollout.reset('train-0')
        rollout.dispatch('advance_time', {})
        self.assertEqual(rollout.finish(), .75)
        self.assertEqual(rollout.finish(), .75)
        self.assertEqual(core.minute, 120)
        self.assertEqual(len(core.actions), 60)
        self.assertEqual(core.actions[1:], [{}] * 59)
        self.assertEqual(rollout._origins, ['model'] + ['passive_finalization'] * 59)
        self.assertEqual(core.verify_calls, 1)
        self.assertEqual(rollout.valid_commits, 1)

    def test_censored_unverified_and_provider_failures_never_receive_labels(self):
        for options in (dict(censored=True), dict(verified=False), dict(suitable=False)):
            core = FixtureCore(**options)
            rollout = training.BenchmarkRollout(lambda: core)
            rollout.reset('train-0')
            with self.subTest(options=options), self.assertRaises(training.RolloutRejected):
                rollout.dispatch('advance_time', {})
                rollout.finish()
            with self.assertRaises(training.RolloutRejected):
                rollout.finish()
        rollout = training.BenchmarkRollout(FixtureCore)
        rollout.reset('train-0')
        rollout.abort('provider_failure')
        with self.assertRaises(training.RolloutRejected):
            rollout.finish()
        self.assertEqual(rollout._core.actions, [])

    def test_unknown_tools_abort_without_tactical_fallback(self):
        rollout = training.BenchmarkRollout(FixtureCore)
        rollout.reset('train-0')
        with self.assertRaises(ValueError):
            rollout.dispatch('private_audit', {})
        self.assertEqual(rollout._core.actions, [])
        with self.assertRaises(training.RolloutRejected):
            rollout.finish()

    def test_one_invalid_commit_returns_feedback_and_time_then_recovers(self):
        core = FixtureCore(invalid_decisions={1})
        rollout = training.BenchmarkRollout(lambda: core)
        rollout.reset('train-0')
        failed = json.loads(rollout.dispatch('advance_time', {}))
        self.assertFalse(failed['last_action_valid'])
        self.assertEqual(failed['last_action_error'], 'invalid')
        self.assertEqual(core.minute, 2)
        self.assertEqual((rollout.model_commits, rollout.valid_commits, rollout._invalid_streak), (1, 0, 1))
        recovered = json.loads(rollout.dispatch('advance_time', {}))
        self.assertTrue(recovered['last_action_valid'])
        self.assertIsNone(recovered['last_action_error'])
        self.assertEqual((rollout.model_commits, rollout.valid_commits, rollout._invalid_streak), (2, 1, 0))
        self.assertEqual(rollout.finish(), .75)
        self.assertEqual(rollout.label_origin, 'rl')
        self.assertEqual(core.minute, 120)

    def test_three_invalid_commits_verify_engine_abandonment_and_yield_zero_failed_rl(self):
        core = FixtureCore(invalid=True)
        tools = training.ToolRollout(lambda: core)
        tools.reset('train-0')
        for decision in range(3):
            public = json.loads(tools.advance_time())
            self.assertFalse(public['last_action_valid'])
            self.assertEqual(public['done'], decision == 2)
        self.assertEqual(training.outcome_reward([tools]), [0.0])
        self.assertEqual(training.outcome_reward([tools]), [0.0])
        rollout = tools._rollout
        self.assertEqual(rollout.label_origin, 'failed_rl')
        self.assertEqual((rollout.model_commits, rollout.valid_commits), (3, 0))
        self.assertEqual(core.minute, 6)
        self.assertEqual(core.verify_calls, 1)
        self.assertTrue(rollout._verification['outcomes']['invalid_abandonment'])
        self.assertEqual(rollout._origins, ['model'] * 3)

    def test_unverified_or_nonzero_abandonment_is_never_scored(self):
        for verified in (False, True):
            core = FixtureCore(invalid=True, verified=verified)
            rollout = training.BenchmarkRollout(lambda: core)
            rollout.reset('train-0')
            for unused in range(3):
                rollout.dispatch('advance_time', {})
            if verified:
                core.reward = 1.0
            with self.subTest(verified=verified), self.assertRaises(training.RolloutRejected):
                rollout.finish()
            with self.assertRaises(training.RolloutRejected):
                rollout.finish()

    def test_bad_committing_arguments_execute_explicit_invalid_engine_action_with_diagnostics(self):
        for name, arguments in (('advance_time', {'future': 1}),
                                ('submit_action', '{"requests":[],"requests":[]}')):
            rollout = training.BenchmarkRollout(FixtureCore)
            rollout.reset('train-0')
            result = json.loads(rollout.dispatch(name, arguments))
            self.assertEqual(rollout._core.actions, [{'_invalid_model_action': True}])
            self.assertEqual(rollout._core.minute, 2)
            self.assertEqual((rollout.model_commits, rollout.valid_commits), (1, 0))
            self.assertIn('Action parse failed', result['last_action_error'])
            self.assertEqual(rollout._parse_diagnostics[0]['tool'], name)
            self.assertEqual(rollout.finish(), .75)
        rollout = training.BenchmarkRollout(FixtureCore)
        rollout.reset('train-0')
        for unused in range(3):
            rollout.dispatch('advance_time', {'future': 1})
        self.assertEqual(rollout.finish(), 0.0)
        self.assertEqual(rollout.label_origin, 'failed_rl')

    def test_cpu_inspection_and_dryrun_never_import_model_stacks_or_call_network(self):
        original_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name.split('.')[0] in ('torch', 'transformers', 'trl', 'peft', 'datasets', 'huggingface_hub'):
                raise AssertionError('CPU command imported model stack: ' + name)
            return original_import(name, *args, **kwargs)

        with patch('builtins.__import__', side_effect=guarded_import), patch('socket.socket.connect',
                side_effect=AssertionError('CPU command attempted network')):
            inspected = training.inspect(FixtureCore)
            result = training.dry_run(FixtureCore, 'train-0')
        self.assertFalse(inspected['model_loaded'])
        self.assertFalse(result['expert_label'])
        self.assertEqual(result['provider_calls'], 0)
        self.assertEqual(result['passive_finalization_decisions'], 59)

    def test_trl_tools_do_not_expose_evaluator_finalization_or_generic_dispatch(self):
        tools = training.ToolRollout(FixtureCore)
        public_methods = {name for name in dir(tools) if not name.startswith('_') and callable(getattr(tools, name))}
        self.assertEqual(public_methods, {'reset', 'submit_action', 'check_action', 'advance_time',
            'request_resources', 'authorize_dispatch', 'protect_population', 'assess_reports',
            'clarify_reports', 'release_resources', 'send_updates', 'correct_readbacks', 'get_reports', 'get_events'})
        tools.reset('train-0')
        self.assertNotIn('SECRET', tools.submit_action())
        self.assertEqual(training.outcome_reward([tools]), [.75])

    def test_fake_trainer_forwards_complete_grpo_row_without_leaking_prompt_into_core_reset(self):
        class FakeTrainer:
            def __init__(self, environment_factory):
                self.environment_factory = environment_factory

            def rollout(self, row):
                environment = self.environment_factory()
                observation = environment.reset(**row)
                environment.advance_time()
                return observation, training.outcome_reward([environment])

        row = dict(task_id='train-0', prompt=[dict(role='system', content=training.instructions()),
            dict(role='user', content='Manage the synthetic incident using tools and delivered public observations. '
                 'If generation stops, evaluator finalization adds no tactical decisions.')])
        original = copy.deepcopy(row)
        core = FixtureCore()
        trainer = FakeTrainer(lambda: training.ToolRollout(lambda: core))
        with patch.object(core, 'reset', wraps=core.reset) as reset:
            observation, rewards = trainer.rollout(row)
            reset.assert_called_once_with('train-0', None)
        self.assertEqual(json.loads(observation)['observation']['task_id'], 'train-0')
        self.assertEqual(rewards, [.75])
        self.assertEqual(row, original)
        with self.assertRaisesRegex(ValueError, 'Unknown training reset options'):
            training.BenchmarkRollout(FixtureCore).reset(**row)
        with self.assertRaisesRegex(ValueError, 'Unknown training reset options'):
            trainer.rollout(dict(row, completion='Unrecognized trainer metadata'))
        with self.assertRaisesRegex(ValueError, 'frozen train tasks only'):
            trainer.rollout(dict(row, task_id='validation-0-0'))

    def test_benchmark_goal_contract_and_bounded_queries_preserve_older_received_evidence(self):
        class ReportsCore(FixtureCore):
            def get_situation(self):
                observation = super().get_situation()
                observation['reports'] = [dict(id=f'report-{index}', observed_at=0, received_at=0,
                    location=[1, 1], text='delivered', evaluator_truth='SECRET_REPORT') for index in range(30)]
                observation['reports'].append(dict(id='future-report', observed_at=80, received_at=80))
                return observation

        core = ReportsCore()
        rollout = training.BenchmarkRollout(lambda: core)
        initial = json.loads(rollout.reset('train-0'))['observation']
        self.assertEqual(initial['reward_weights'], dict(population_protection=.8, unburned_footprint=.2))
        self.assertEqual(initial['reports_count'], 30)
        self.assertEqual(initial['reports'][0]['id'], 'report-10')
        self.assertIn('unburned', training.instructions())
        self.assertIn('get_reports', training.instructions())
        for remaining in (2, 1, 0):
            response = json.loads(rollout.dispatch('get_reports', dict(offset=0, limit=2)))
            self.assertEqual([report['id'] for report in response['records']], ['report-0', 'report-1'])
            self.assertEqual(response['remaining_queries'], remaining)
            self.assertNotIn('SECRET', training.canonical(response))
        self.assertEqual(core.minute, 0)
        rollout.dispatch('advance_time', {})
        self.assertEqual(json.loads(rollout.dispatch('get_reports', dict(ids=['report-0'])))['remaining_queries'], 2)
        with self.assertRaises(ValueError):
            rollout.dispatch('get_reports', dict(ids=['future-report']))
        with self.assertRaises(training.RolloutRejected):
            rollout.finish()

    def test_query_bounds_and_budget_cannot_be_bypassed(self):
        for arguments in (dict(limit=21), dict(limit=True), dict(ids=['future']), dict(offset=-1)):
            rollout = training.BenchmarkRollout(FixtureCore)
            rollout.reset('train-0')
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                rollout.dispatch('get_events', arguments)
            self.assertEqual(rollout._core.actions, [])
        rollout = training.BenchmarkRollout(FixtureCore)
        rollout.reset('train-0')
        for unused in range(3):
            rollout.dispatch('get_events', {})
        with self.assertRaises(ValueError):
            rollout.dispatch('get_reports', {})


class DemonstrationTests(unittest.TestCase):
    def setUp(self):
        self.demonstration = training.build_demonstration(FixtureCore, 'train-0', policy=wait_policy)

    def test_complete_causal_demonstration_replays_and_never_embeds_evaluator_context(self):
        replayed = training.verify_demonstration(FixtureCore, self.demonstration)
        self.assertEqual(replayed['sha256'], self.demonstration['sha256'])
        self.assertEqual(replayed['trajectory'], self.demonstration['trajectory'])
        self.assertEqual(len(self.demonstration['examples']), 60)
        self.assertNotIn('SECRET', json.dumps(self.demonstration['examples']))
        self.assertEqual(self.demonstration['origins'], ['model'] * 60)

    def test_expert_demonstrations_reject_any_invalid_commit_or_failed_rl_source(self):
        for options in (dict(invalid_decisions={1}), dict(invalid=True)):
            core = FixtureCore(**options)
            with self.subTest(options=options), self.assertRaisesRegex(training.RolloutRejected, 'every invalid'):
                training.build_demonstration(lambda: core, 'train-0', policy=wait_policy)
            self.assertEqual(len(core.actions), 1)
        with self.assertRaisesRegex(training.RolloutRejected, 'every invalid'):
            training.build_demonstration(FixtureCore, 'train-0',
                policy=lambda context: dict(name='advance_time', arguments={'future': 1}))
        failed = training.seal(dict(self.demonstration, source='failed_rl'))
        with self.assertRaises(ValueError):
            training.verify_demonstration(FixtureCore, failed)

    def test_wrong_split_checksum_catalog_censor_and_forged_examples_fail_closed(self):
        changes = (lambda item: item.update(task_id='test-0'),
                   lambda item: item.update(catalog_sha256='f' * 64),
                   lambda item: item['examples'][0]['observation'].update(minute=80),
                   lambda item: item['origins'].__setitem__(0, 'passive_finalization'),
                   lambda item: item.update(source='provider_failure'))
        for change in changes:
            modified = copy.deepcopy(self.demonstration)
            change(modified)
            with self.subTest(change=change), self.assertRaises(ValueError):
                training.verify_demonstration(FixtureCore, training.seal(modified))
        modified = copy.deepcopy(self.demonstration)
        modified['reward'] = 100
        with self.assertRaises(ValueError):
            training.verify_demonstration(FixtureCore, modified)
        with self.assertRaises(training.RolloutRejected):
            training.build_demonstration(lambda: FixtureCore(censored=True), 'train-0', policy=wait_policy)

    def test_recorded_training_trajectory_requires_exact_replay_and_external_pin(self):
        trace = self.demonstration['trajectory']
        recording = dict(version='wildfire-recorded-training-trajectory-v1', source='recorded_training_trajectory',
            provider_failures=0, fallback_actions=0, origins=['model'] * 60, trajectory=trace)
        imported = training.demonstration_from_trajectory(FixtureCore, recording, expected_sha256=training.checksum(recording))
        self.assertEqual(imported['source'], 'recorded_training_trajectory')
        training.verify_demonstration(FixtureCore, imported)
        with self.assertRaises(ValueError):
            training.demonstration_from_trajectory(FixtureCore, recording, expected_sha256='0' * 64)
        with self.assertRaises(ValueError):
            training.demonstration_from_trajectory(FixtureCore, trace, expected_sha256=training.checksum(trace))
        modified = copy.deepcopy(recording)
        modified['trajectory']['transitions'][0]['action_valid'] = False
        with self.assertRaises(ValueError):
            training.demonstration_from_trajectory(FixtureCore, modified, expected_sha256=training.checksum(modified))
        for field in ('provider_failures', 'fallback_actions'):
            modified = dict(recording, **{field: 1})
            with self.assertRaises(ValueError):
                training.demonstration_from_trajectory(FixtureCore, modified, expected_sha256=training.checksum(modified))

    def test_frozen_retrieval_is_stable_bounded_and_cannot_learn_from_heldout_queries(self):
        bank = training.FrozenRetrievalBank.from_demonstrations(FixtureCore, [self.demonstration], max_cases=12)
        before = bank.entries
        observation = FixtureCore()
        observation.reset('test-0')
        context = observation.get_situation()
        retrieved = bank.nearest(context)
        context['future_outcome'] = 'SECRET_FUTURE'
        context['evaluator_state'] = dict(reward=10000)
        self.assertEqual(bank.nearest(context), retrieved)
        self.assertEqual(bank.entries, before)
        self.assertLessEqual(len(retrieved), 3)
        self.assertLessEqual(len(training.canonical(retrieved).encode()), 16384)
        self.assertNotIn('SECRET', training.canonical(retrieved))
        self.assertEqual(bank.nearest(context, max_bytes=2), [])
        with self.assertRaises(FrozenInstanceError):
            bank.entries = ()
        for limit in (0, 5, True):
            with self.assertRaises(ValueError):
                bank.nearest(context, limit=limit)
        bad = training.seal(dict(self.demonstration, task_id='validation-0-0'))
        with self.assertRaises(ValueError):
            training.FrozenRetrievalBank.from_demonstrations(FixtureCore, [bad])


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.adapter_pin = {'adapter_model.safetensors': '3' * 64, 'adapter_config.json': '4' * 64}

    def gate(self, report):
        return training.validation_gate(report, FixtureCore(), expected_sha256=training.checksum(report),
                                        expected_adapter_files_sha256=self.adapter_pin)

    def report(self):
        environment = FixtureCore()
        report = dict(version=training.VALIDATION_VERSION, status='complete', verified=True, model_loaded=True,
            model_inference_calls=16, scripted_actions=0, provider_failures=0,
            adapter_files_sha256=self.adapter_pin,
            readiness_plan_sha256=training.checksum(training.readiness_plan(environment)),
            **training.expected_model_provenance(environment), groups=[])
        for index, planned in enumerate(training.readiness_plan(environment)):
            group = dict(planned, verified=True, boundary_censored=False, rollouts=[])
            for rollout_id in range(4):
                group['rollouts'].append(dict(rollout_id=rollout_id, task_id=planned['task_id'], seed=planned['seed'],
                    source='model_evaluation', verified=True, boundary_censored=False,
                    reward=.75 if index == 0 and rollout_id == 3 else .5,
                    model_inference_calls=1, scripted_actions=0, provider_failures=0,
                    committed_actions=100, valid_committed_actions=95))
            report['groups'].append(group)
        return report

    def test_gate_recomputes_exact_thresholds_on_preregistered_same_task_seed_rollouts(self):
        report = self.report()
        gate = self.gate(report)
        self.assertEqual(gate['valid_commit_fraction'], .95)
        self.assertEqual(gate['reward_variation_fraction'], .25)
        self.assertEqual(gate['validation_groups'], 4)
        self.assertEqual(gate['rollouts'], 16)
        self.assertNotIn('later-uncertain-smoke', [group['group_id'] for group in report['groups']])
        for modify in (lambda item: item['groups'].pop(),
                       lambda item: item['groups'][0]['rollouts'][0].update(valid_committed_actions=94),
                       lambda item: item['groups'][0]['rollouts'][3].update(reward=.5),
                       lambda item: item['groups'][0].update(group_id='invented'),
                       lambda item: item['groups'][0].update(family='false_alarm'),
                       lambda item: item['groups'][0].update(task_id='test-0'),
                       lambda item: item['groups'][0].update(seed=2000),
                       lambda item: item['groups'][0]['rollouts'][0].update(reward=float('nan')),
                       lambda item: item['groups'][0]['rollouts'][0].update(reward=100),
                       lambda item: item['groups'][0]['rollouts'][0].update(seed=1001),
                       lambda item: item['groups'][0]['rollouts'][0].update(task_id='validation-0-1'),
                       lambda item: item['groups'][0]['rollouts'][0].update(rollout_id=1),
                       lambda item: item['groups'][0]['rollouts'].pop(),
                       lambda item: item['groups'][0]['rollouts'][0].update(source='cpu_scripted_policy'),
                       lambda item: item['groups'][0]['rollouts'][0].update(boundary_censored=True),
                       lambda item: item['groups'][0]['rollouts'][0].update(provider_failures=1)):
            changed = copy.deepcopy(report)
            modify(changed)
            with self.subTest(modify=modify), self.assertRaises(ValueError):
                self.gate(changed)

    def test_cross_seed_variants_and_two_rollouts_cannot_claim_grpo_reward_diversity(self):
        report = self.report()
        for group in report['groups']:
            group['rollouts'] = group['rollouts'][:2]
            group['rollouts'][1]['seed'] += 1
            group['rollouts'][1]['reward'] = .9
        with self.assertRaises(ValueError):
            self.gate(report)

    def test_cpu_scripted_metrics_or_wrong_model_contract_environment_reward_pins_cannot_pass(self):
        report = self.report()
        for field, value in (('source', 'cpu_scripted_policy'), ('model_loaded', False),
                             ('status', 'incomplete'),
                             ('model_id', 'other'), ('model_revision', '0' * 40),
                             ('contract_version', 'v1'), ('env_version', 'legacy'),
                             ('reward_version', 'legacy'), ('catalog_sha256', '0' * 64),
                             ('observation_version', 'legacy'),
                             ('environment_implementation_sha256', '0' * 64), ('reward_rules_sha256', '0' * 64),
                             ('model_contract_sha256', '0' * 64),
                             ('readiness_plan_sha256', '0' * 64), ('adapter_files_sha256', {}),
                             ('provider_failures', 1), ('scripted_actions', 1), ('model_inference_calls', 0)):
            changed = dict(report, **{field: value})
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.gate(changed)
        with self.assertRaises(ValueError):
            training.validation_gate(report, FixtureCore(), expected_sha256='0' * 64,
                                     expected_adapter_files_sha256=self.adapter_pin)

    def test_sft_requires_verified_train_demos_but_never_the_model_quality_gate(self):
        demonstration = training.build_demonstration(FixtureCore, 'train-0', policy=wait_policy)
        arguments = dict(stage='sft', output='/tmp/unused-benchmark-training-output',
            enable_gpu=True, agreed_budget_seconds=1, wall_seconds=1, agreed_budget_eur=0)
        with patch.object(training, 'require_host_timeout'), patch.object(training, 'validation_gate',
                side_effect=AssertionError('SFT must not require model readiness')) as gate, patch(
                'train.benchmark.importlib.metadata.version', side_effect=RuntimeError('dependency sentinel')):
            with self.assertRaisesRegex(ValueError, 'SFT requires complete'):
                training.train(FixtureCore, **arguments)
            with self.assertRaisesRegex(RuntimeError, 'dependency sentinel'):
                training.train(FixtureCore, demonstrations=[demonstration], **arguments)
            gate.assert_not_called()
        with patch.object(training, 'require_host_timeout'):
            with self.assertRaisesRegex(ValueError, 'GRPO requires an actual SFT-adapter'):
                training.train(FixtureCore, **dict(arguments, stage='grpo'))

    def test_cli_validation_report_is_optional_for_sft_and_required_for_grpo(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'config.json'
            config.write_text('{}')
            arguments = ['--config', str(config), '--output', str(Path(directory) / 'output'), '--allow-archived-catalog']
            with patch.object(training, 'make_factory', return_value=FixtureCore), patch.object(
                    training, 'train', return_value={'status': 'not_executed'}) as train, redirect_stdout(io.StringIO()):
                training.main(['sft', *arguments])
                self.assertIsNone(train.call_args.kwargs['validation_report'])
                self.assertIsNone(train.call_args.kwargs['validation_sha256'])
                train.reset_mock()
                with self.assertRaises(SystemExit), patch('sys.stderr', io.StringIO()):
                    training.main(['grpo', *arguments])
                train.assert_not_called()
                training.main(['validate', *arguments, '--sft-adapter', str(Path(directory) / 'sft')])
                self.assertEqual(train.call_args.kwargs['stage'], 'validate')
                self.assertIsNone(train.call_args.kwargs['validation_report'])

    def test_no_gpu_optin_budget_or_verified_host_timeout_means_no_model_import(self):
        arguments = dict(stage='sft', output='/tmp/unused-benchmark-training-output', agreed_budget_eur=0,
                         validation_report={}, validation_sha256='0' * 64)
        with patch('train.benchmark.importlib.metadata.version', side_effect=AssertionError('Dependency/GPU gate bypass')):
            with self.assertRaisesRegex(ValueError, 'enable-gpu'):
                training.train(FixtureCore, **arguments)
            for budget, wall in ((None, 7200), (7201, 7200), (10, 11)):
                with self.assertRaises(ValueError):
                    training.train(FixtureCore, enable_gpu=True, agreed_budget_seconds=budget,
                                   wall_seconds=wall, **arguments)
            with patch.object(training, 'require_host_timeout', side_effect=RuntimeError('host wrapper missing')):
                with self.assertRaisesRegex(RuntimeError, 'host wrapper'):
                    training.train(FixtureCore, enable_gpu=True, agreed_budget_seconds=7200, **arguments)

    def test_host_wrapper_requires_exact_kill_signal_and_agreed_wall(self):
        for arguments in (b'python\0train/benchmark.py\0', b'timeout\0--signal=TERM\x007200s\0python\0',
                          b'timeout\0--signal=KILL\x007201s\0python\0'):
            with patch.object(Path, 'read_bytes', return_value=arguments), self.assertRaises(RuntimeError):
                training.require_host_timeout(7200)
        with patch.object(Path, 'read_bytes', return_value=b'/usr/bin/timeout\0--signal=KILL\x007200s\0python\0'):
            training.require_host_timeout(7200)


class ReadinessAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.adapter_pin = {'adapter_model.safetensors': '3' * 64, 'adapter_config.json': '4' * 64}

    def transport_factory(self, index):
        count = 0

        def transport(messages, tools):
            nonlocal count
            self.assertNotIn('SECRET', training.canonical(messages))
            self.assertNotIn('"outcomes":', training.canonical(messages))
            available = {tool['function']['name'] for tool in tools}
            self.assertFalse(available.intersection({'private_audit', 'finish', '_finish', 'trajectory'}))
            name = ('get_reports', 'check_action')[count] if count < 2 else 'advance_time'
            count += 1
            return dict(name=name, arguments={})

        return transport

    def test_fake_transport_assembles_all_fixed_seed_rollouts_without_attesting_actual_inference(self):
        with tempfile.TemporaryDirectory() as directory:
            report = training.assemble_readiness_report(FixtureCore, self.transport_factory,
                adapter_files_sha256=self.adapter_pin, tool_iterations=4, audit_directory=directory)
            self.assertEqual(len(list(Path(directory).glob('*/private/verification.json'))), 16)
        self.assertEqual(report['source'], 'fake_model_transport')
        self.assertFalse(report['model_loaded'])
        self.assertEqual(report['model_inference_calls'], 0)
        self.assertEqual(report['transport_calls'], 64)
        self.assertEqual(report['scripted_actions'], 32)
        self.assertNotIn('SECRET', training.canonical(report))
        self.assertEqual(len(report['groups']), 4)
        for group in report['groups']:
            self.assertEqual(len(group['rollouts']), 4)
            self.assertEqual({row['task_id'] for row in group['rollouts']}, {group['task_id']})
            self.assertEqual({row['seed'] for row in group['rollouts']}, {group['seed']})
            self.assertTrue(all(row['verified'] and row['passive_finalization_decisions'] == 58
                                for row in group['rollouts']))
        with self.assertRaisesRegex(ValueError, 'model_evaluation provenance'):
            training.validation_gate(report, FixtureCore(), expected_sha256=training.checksum(report),
                                     expected_adapter_files_sha256=self.adapter_pin)
        with self.assertRaises(ValueError):
            training.BenchmarkRollout(FixtureCore).reset(report['groups'][0]['task_id'])

    def test_failed_or_timed_out_transport_leaves_only_partial_audits_no_readiness_artifact(self):
        def factory(index):
            if index == 2:
                raise TimeoutError('simulated model deadline')
            return self.transport_factory(index)

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(TimeoutError):
                training.assemble_readiness_report(FixtureCore, factory,
                    adapter_files_sha256=self.adapter_pin, tool_iterations=4, audit_directory=directory)
            self.assertEqual(len(list(Path(directory).glob('*/private/verification.json'))), 2)
            self.assertFalse((Path(directory) / 'validation.json').exists())

    def test_verified_abandonment_is_a_zero_failed_rl_sample_in_readiness_audits(self):
        with tempfile.TemporaryDirectory() as directory:
            report = training.assemble_readiness_report(lambda: FixtureCore(invalid=True),
                lambda index: lambda messages, tools: dict(name='advance_time', arguments={}),
                adapter_files_sha256=self.adapter_pin, tool_iterations=4, audit_directory=directory)
            labels = [json.loads(path.read_text()) for path in Path(directory).glob('*/label.json')]
        self.assertEqual(labels, [dict(origin='failed_rl', reward=0.0, expert=False)] * 16)
        for group in report['groups']:
            for row in group['rollouts']:
                self.assertEqual((row['reward'], row['label_origin'], row['invalid_abandonment']), (0.0, 'failed_rl', True))
                self.assertEqual((row['committed_actions'], row['valid_committed_actions']), (3, 0))

    def test_validation_task_and_seed_guard_and_unavailable_model_tools_fail_closed(self):
        for task_id, seed in (('train-0', None), ('validation-0-1', 1001), ('validation-0-0', 1001),
                              ('validation-later-0', 2000)):
            with self.subTest(task=task_id), self.assertRaises(ValueError):
                training._ReadinessRollout(FixtureCore).reset(task_id, seed)
        with self.assertRaises(training.RolloutRejected):
            training.assemble_readiness_report(FixtureCore,
                lambda index: lambda messages, tools: dict(name='private_audit', arguments={}),
                adapter_files_sha256=self.adapter_pin, tool_iterations=1)

    def test_local_generation_parser_accepts_one_complete_call_and_rejects_ambiguous_completions(self):
        call = training.decode_named_call('<tool_call>{"name":"advance_time","arguments":{}}</tool_call>')
        self.assertEqual(call, dict(name='advance_time', arguments={}))
        for raw in ('<tool_call>{"name":"advance_time","arguments":{}}',
                    '{"name":"advance_time","name":"submit_action","arguments":{}}',
                    '[{"name":"advance_time","arguments":{}}]',
                    '{"name":"advance_time","arguments":{},"extra":1}',
                    '{"name":"submit_action","arguments":{"explanation":NaN}}',
                    'reasoning <tool_call>{"name":"advance_time","arguments":{}}</tool_call>'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                training.decode_named_call(raw)


class BenchmarkCoreIntegrationTests(unittest.TestCase):
    def test_real_engine_invalid_recovery_and_verified_three_invalid_abandonment(self):
        from envs.wildfire_response.tests.test_topographic_development import fixture_domain, fixture_inputs
        prepared = fixture_inputs()
        domain = fixture_domain(prepared)
        factory = training.make_factory(dict(prepared_inputs=prepared, topographic_inputs=domain,
            expected_prepared_sha256=prepared['sha256'], expected_topographic_sha256=domain['sha256'],
            slope_enabled=True, allow_synthetic_conditions=True))
        environment = factory()
        task_id = next(group['cases'][0]['id'] for group in environment.catalog['groups']
                       if group['split'] == 'train' and group['family'] == 'false_alarm')
        rollout = training.BenchmarkRollout(factory)
        rollout.reset(task_id)
        invalid = json.loads(rollout.dispatch('submit_action', dict(clarifications=['unreceived-report'])))
        self.assertFalse(invalid['last_action_valid'])
        self.assertEqual(invalid['observation']['minute'], 2)
        recovered = json.loads(rollout.dispatch('advance_time', {}))
        self.assertTrue(recovered['last_action_valid'])
        self.assertEqual(rollout.finish(), rollout._core.reward)
        self.assertEqual(rollout.label_origin, 'rl')
        failed = training.BenchmarkRollout(factory)
        failed.reset(task_id)
        for unused in range(3):
            failed.dispatch('advance_time', {'malformed': True})
        self.assertEqual(failed.finish(), 0.0)
        self.assertEqual(failed.label_origin, 'failed_rl')
        self.assertEqual(failed._observation['minute'], 6)
        self.assertTrue(failed._verification['outcomes']['invalid_abandonment'])

    def test_real_train_rollout_baseline_export_replay_and_frozen_bank_use_current_contract(self):
        from envs.wildfire_response.tests.test_topographic_development import fixture_domain, fixture_inputs
        prepared = fixture_inputs()
        domain = fixture_domain(prepared)
        factory = training.make_factory(dict(prepared_inputs=prepared, topographic_inputs=domain,
            expected_prepared_sha256=prepared['sha256'], expected_topographic_sha256=domain['sha256'],
            slope_enabled=True, allow_synthetic_conditions=True))
        environment = factory()
        plan = training.readiness_plan(environment)
        self.assertEqual(len(plan), 4)
        self.assertEqual(len(training.split_ids(environment.catalog, 'validation')), 40)
        self.assertEqual([item['family'] for item in plan], list(training.READINESS_FAMILIES))
        for item in plan:
            first_group = next(group for group in environment.catalog['groups']
                               if group['split'] == 'validation' and group['family'] == item['family'])
            case = next(case for case in first_group['cases'] if case['variant'] == 0)
            self.assertEqual((item['group_id'], item['task_id'], item['seed']),
                             (first_group['id'], case['id'], case['seed']))
        task_id = next(group['cases'][0]['id'] for group in environment.catalog['groups']
                       if group['split'] == 'train' and group['family'] == 'false_alarm')
        original_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name.split('.')[0] in ('torch', 'transformers', 'trl', 'peft', 'datasets', 'huggingface_hub'):
                raise AssertionError('Real-core CPU path imported model stack: ' + name)
            return original_import(name, *args, **kwargs)

        with patch('builtins.__import__', side_effect=guarded_import), patch('socket.socket.connect',
                side_effect=AssertionError('Real-core CPU path attempted network')):
            dry_run = training.dry_run(factory, task_id)
            demonstration = training.baseline_demonstration(factory, task_id, policy='cooperative')
            bank = training.FrozenRetrievalBank.from_demonstrations(factory, [demonstration], max_cases=8)
        self.assertTrue(dry_run['verified'])
        self.assertFalse(dry_run['expert_label'])
        self.assertEqual(dry_run['passive_finalization_decisions'], 59)
        self.assertEqual(len(demonstration['calls']), 60)
        self.assertEqual(demonstration['examples'][0]['observation']['reward_weights'],
                         dict(population_protection=.8, unburned_footprint=.2))
        self.assertGreater(len(bank.entries), 0)
        self.assertLessEqual(len(bank.entries), 8)
        environment.reset(task_id)
        self.assertTrue(bank.nearest(environment.get_situation()))
        self.assertEqual(training.expected_model_provenance(environment)['contract_version'],
                         'wildfire-benchmark-model-tools-v1')


if __name__ == '__main__':
    unittest.main()
