import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from envs.wildfire_response import WildfireResponseEnv
from envs.wildfire_response.baselines import DeterministicBaseline, RandomValidBaseline, RetrievalBaseline, passive
from envs.wildfire_response.core.scenarios import FAMILIES, generate_tasks
from envs.wildfire_response.core.environment import EpisodeSimulation
from simulator.engine import Simulation
from envs.wildfire_response.eval.run import dump, export_transitions, load, make_bank, replay, run_episode


def fixture(fire=False):
    task = copy.deepcopy(generate_tasks()[1])
    task['id'] = 'test-fixture'
    task['reports'] = [dict(id='r000', observed_at=0, received_at=0, source_id='citizen', source_role='resident',
                            channel='chat', location=[73, 21], text='Smoke reported at farm; verify.')]
    task['events'] = [dict(tick=6, kind='ignition', location=[73, 21])] if fire else []
    task['minimum_ticks'] = 16
    return task


def alert(observation, channel='broadcast'):
    action = passive(observation)
    action['messages'] = [dict(recipient='contact-farm' if channel == 'call' else 'farm',
                               purpose='evacuate', channel=channel, report_ids=['r000'])]
    return action


class EpisodeTests(unittest.TestCase):
    def test_cached_sensors_preserve_uncached_engine_state(self):
        env = WildfireResponseEnv()
        observation, _ = env.reset('train-006', seed=10006)
        reference = copy.deepcopy(env)
        class ReferenceSimulation(EpisodeSimulation):
            observe = Simulation.observe
        reference.sim.__class__ = ReferenceSimulation
        for _ in range(6):
            action = DeterministicBaseline()(observation)
            observation, value, done, truncated, info = env.step(action)
            compared = reference.step(action)
            self.assertEqual((observation, value, done, truncated, info), compared)
            left, right = env.sim.checkpoint(), reference.sim.checkpoint()
            left['state'].pop('_observation_key', None)
            right['state'].pop('_observation_key', None)
            self.assertEqual(left, right)

    def test_no_credentials_or_strategy_or_private_events(self):
        with patch('simulator.engine.contact_directory', side_effect=AssertionError('Private contacts accessed')):
            env = WildfireResponseEnv()
            observation, _ = env.reset(scenario=fixture(True))
            rendered = json.dumps(observation)
            for token in ('phone_number', 'chat_id', 'private_annotations', 'true_claim', 'events', 'fleet_policy', 'scenario_instructions'):
                self.assertNotIn('"'+token+'"', rendered)
            with self.assertRaises(ValueError):
                env.reports(['not-received'])
            self.assertEqual(env.reports(['r000'])[0]['received_at'], 0)

    def test_future_fire_cannot_change_current_observation_or_candidates(self):
        original, changed = fixture(True), fixture(True)
        changed['events'][0]['location'] = [40, 40]
        first, second = WildfireResponseEnv(), WildfireResponseEnv()
        self.assertEqual(first.reset(scenario=original)[0], second.reset(scenario=changed)[0])

    def test_reset_and_independent_sessions(self):
        first, second = WildfireResponseEnv(), WildfireResponseEnv()
        observation, _ = first.reset(scenario=fixture())
        second.reset(scenario=fixture())
        first.step(alert(observation))
        self.assertEqual(second.sim.tick, 0)
        self.assertEqual(second.sim.groups['farm']['status'], 'unwarned')
        self.assertEqual(first.reset(scenario=fixture())[0], observation)

    def test_delays_and_call_effect(self):
        for channel, delay in [('broadcast',2), ('call',4)]:
            env = WildfireResponseEnv(ticks_per_action=1)
            observation, _ = env.reset(scenario=fixture())
            observation, *_ = env.step(alert(observation, channel))
            self.assertEqual(env.sim.groups['farm']['status'], 'unwarned')
            for _ in range(delay-1):
                observation, *_ = env.step(passive(observation))
            self.assertEqual(env.sim.groups['farm']['status'], 'evacuating')
            self.assertEqual(observation['delivery_acknowledgements'][-1]['delivered_at'], delay)

    def test_information_does_not_evacuate(self):
        env = WildfireResponseEnv()
        observation, _ = env.reset(scenario=fixture())
        action = alert(observation)
        action['messages'][0]['purpose'] = 'inform'
        env.step(action)
        self.assertEqual(env.sim.groups['farm']['status'], 'unwarned')

    def test_invalid_package_is_atomic_and_consumes_budget(self):
        env = WildfireResponseEnv()
        task = fixture()
        task['minimum_ticks'] = 64
        observation, _ = env.reset(scenario=task)
        action = alert(observation)
        action['fleet']['extinguisher_orders'][0]['command'] = 'teleport'
        for _ in range(3):
            observation, _, _, truncated, info = env.step(action)
            self.assertIsNotNone(info['error'])
            self.assertEqual(env.sim.groups['farm']['status'], 'unwarned')
            self.assertEqual(env.sim.drone['mode'], 'hold')
        self.assertTrue(truncated)
        self.assertGreater(env.evaluator_state()['cost'], 100)
        self.assertEqual(env.sim.tick, 24)
        with self.assertRaises(RuntimeError):
            env.step(action)

    def test_report_triage_known_ids_and_support(self):
        env = WildfireResponseEnv()
        observation, _ = env.reset(scenario=fixture())
        action = passive(observation)
        action['report_dispositions'] = [dict(report_id='r000', status='investigate', supporting_report_ids=['future'])]
        *_, info = env.step(action)
        self.assertIsNotNone(info['error'])
        self.assertEqual(env.dispositions, {})

    def test_report_batches_are_bounded_and_retained(self):
        task = fixture()
        task['reports'] = [dict(task['reports'][0], id=f'r{index:03d}') for index in range(100)]
        env = WildfireResponseEnv()
        observation, _ = env.reset(scenario=task)
        self.assertEqual(len(observation['new_report_ids']), 20)
        for _ in range(4):
            observation, *_ = env.step(passive(observation))
            self.assertLessEqual(len(observation['new_report_ids']), 20)
        self.assertEqual(len(env.reports()), 100)

    def test_future_ignition_survives_empty_world(self):
        task = fixture(True)
        task['events'][0]['tick'] = 12
        env = WildfireResponseEnv()
        observation, _ = env.reset(scenario=task)
        observation, _, terminated, _, _ = env.step(passive(observation))
        self.assertFalse(terminated)
        env.step(passive(observation))
        self.assertTrue(any(env.sim.burning(cell) for row in env.sim.cells for cell in row))

    def test_evacuation_movement_is_not_frozen_after_fire_out(self):
        env = WildfireResponseEnv(max_decisions=32)
        observation, _ = env.reset(scenario=fixture())
        observation, *_ = env.step(alert(observation))
        for _ in range(8):
            if env.terminated:
                break
            observation, *_ = env.step(passive(observation))
        self.assertEqual(env.sim.groups['farm']['status'], 'safe')
        self.assertTrue(env.terminated)

    def test_early_warning_beats_waiting(self):
        early, late = WildfireResponseEnv(), WildfireResponseEnv()
        first, _ = early.reset(scenario=fixture(True))
        second, _ = late.reset(scenario=fixture(True))
        early.step(alert(first))
        late.step(passive(second))
        self.assertLess(early.evaluator_state()['cost'], late.evaluator_state()['cost'])
        self.assertGreater(late.sim.groups['farm']['burnt'], 0)

    def test_false_alarm_restraint_and_duplicates(self):
        quiet, noisy = WildfireResponseEnv(), WildfireResponseEnv()
        first, _ = quiet.reset(scenario=fixture())
        second, _ = noisy.reset(scenario=fixture())
        _, quiet_reward, *_ = quiet.step(passive(first))
        second, noisy_reward, *_ = noisy.step(alert(second))
        self.assertGreater(quiet_reward, noisy_reward)
        _, repeat_reward, *_ = noisy.step(alert(second))
        self.assertLessEqual(repeat_reward, 0)
        self.assertEqual(noisy.counters['redundant_messages'], 1)

    def test_trajectory_replay(self):
        task = generate_tasks()[0]
        trajectory, private = run_episode(task, 'passive', max_decisions=4)
        self.assertAlmostEqual(replay(json.loads(json.dumps(trajectory))), trajectory['total_reward'])
        self.assertNotIn('checkpoint', json.dumps(trajectory))
        self.assertIn('checkpoint', private)

    def test_dataset_splits_seeds_and_dry_locations(self):
        tasks = generate_tasks()
        self.assertEqual(len(tasks), 120)
        self.assertEqual(len({task['group_id'] for task in tasks}), 120)
        self.assertEqual(len({task['seed'] for task in tasks}), 120)
        self.assertEqual(tasks, generate_tasks())
        configurations = {(task['family'], tuple(task['reports'][0]['location'])) for task in tasks}
        self.assertEqual(len(configurations), 120)
        for split, count in [('train',80), ('validation',20), ('test',20)]:
            subset = [task for task in tasks if task['split'] == split]
            self.assertEqual(len(subset), count)
            self.assertEqual({task['family'] for task in subset}, set(FAMILIES))
        env = WildfireResponseEnv()
        env.reset()
        for task in tasks:
            for event in task['events']:
                if event['kind'] == 'ignition':
                    column,row = event['location']
                    self.assertGreater(env.sim.cells[row][column]['initial_fuel'], 0)

    def test_frozen_training_bank_and_streaming_export(self):
        trajectory, _ = run_episode(generate_tasks()[0], 'passive', max_decisions=1)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            dump(root/'episode.json.gz', trajectory)
            bank = root/'bank.tmp'
            make_bank(root, bank)
            policy = RetrievalBaseline(bank)
            action = policy(trajectory['initial_observation'])
            self.assertEqual(policy.retrieved_ids, [trajectory['scenario_id']])
            self.assertIn('fleet', action)
            dump(bank, [dict(split='test')])
            with self.assertRaises(ValueError):
                RetrievalBaseline(bank)
            export_transitions(root, root/'rows.gz')
            import gzip
            with gzip.open(root/'rows.gz', 'rt') as stream:
                row = json.loads(next(stream))
            self.assertEqual(row['reward'], trajectory['transitions'][0]['reward'])
            self.assertNotIn('private_annotations', row['observation'])
            trajectory['split'] = 'test'
            dump(root/'episode.json.gz', trajectory)
            with self.assertRaises(ValueError):
                make_bank(root, bank)

    def test_public_baselines_produce_accepted_actions(self):
        for task in generate_tasks()[:6]:
            for policy in (DeterministicBaseline(), RandomValidBaseline(4)):
                env = WildfireResponseEnv(max_decisions=1)
                observation, _ = env.reset(scenario=task)
                *_, info = env.step(policy(observation))
                self.assertIsNone(info['error'], (task['id'], info))


if __name__ == '__main__':
    unittest.main()
