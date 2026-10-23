import copy
import json
import unittest

from wildfire_core import OpsControlCenterEnv, verify_ops_trajectory
from wildfire_core.ops import ControlBaseline
from envs.wildfire_response.regional.ops.evidence import EvidenceLedger
from envs.wildfire_response.regional.ops.objectives import objective_step, validate_objectives
from envs.wildfire_response.regional.ops.run import run_episode
from envs.wildfire_response.regional.ops.tasks import get_ops_task, list_ops_tasks


def public_evidence():
    return dict(minute=10, resources=[], reports=[dict(id='report-0', observed_at=1,
        received_at=2, location=[3, 4])], sensor_memory=[dict(x=3, y=4, burning=False, observed_at=9)],
        districts=[dict(id='near', home=[5, 4], exposed=0, status='unwarned'),
                   dict(id='far', home=[30, 30], exposed=0, status='unwarned')],
        report_assessments=[dict(report_id='report-0', assessment='no_fire_observed')])


def evaluator_evidence(**overrides):
    evidence = dict(schedule_complete=True, active_fire_cells=0,
                    redundant_notices=0, unsupported_evacuations=0)
    evidence.update(overrides)
    return evidence


def rollout(policy, task_id='control-002'):
    env = OpsControlCenterEnv()
    observation, _ = env.reset(task_id)
    while True:
        observation, _, terminated, truncated, _ = env.submit_action(policy(observation))
        if terminated or truncated:
            break
    return env, env.verify_episode()


class BroaderObjectiveTests(unittest.TestCase):
    def setUp(self):
        self.objectives = get_ops_task('control-000')['objectives']

    def test_control_task_manifest_covers_six_families_without_prescribed_targets(self):
        tasks = list_ops_tasks('control')
        self.assertEqual(len(tasks), 6)
        self.assertEqual(len(list_ops_tasks()), 12)
        self.assertEqual(len({task['group_id'] for task in tasks}), 6)
        for task in tasks:
            self.assertEqual(task['split'], 'development')
            self.assertEqual(task['task_set'], 'control')
            self.assertTrue(task['scenario_id'].startswith('regional-train-'))
            self.assertEqual({objective['kind'] for objective in task['objectives']},
                {'evidence_triage', 'population_safety', 'incident_resolved', 'communication_discipline'})
            for objective in task['objectives']:
                self.assertEqual(set(objective), {'id', 'kind', 'deadline_minute'})
        with self.assertRaisesRegex(ValueError, 'Choose dispatch or control'):
            list_ops_tasks('test')

    def test_terminal_outcomes_have_no_early_or_duplicate_credit(self):
        observation = public_evidence()
        evidence = evaluator_evidence()
        completed, reward = objective_step(observation, self.objectives, [], evidence=evidence)
        self.assertEqual((completed, reward), ([], 0))
        completed, reward = objective_step(observation, self.objectives, [], terminal=True, evidence=evidence)
        self.assertEqual(len(completed), 4)
        self.assertEqual(reward, 1)
        self.assertEqual(objective_step(observation, self.objectives, completed,
            terminal=True, evidence=evidence), ([], 0))

    def test_unresolved_fire_prevents_full_credit(self):
        completed, reward = objective_step(public_evidence(), self.objectives, [],
            terminal=True, evidence=evaluator_evidence(active_fire_cells=1))
        self.assertNotIn('incident_resolved', completed)
        self.assertEqual(reward, .75)

    def test_population_exposure_and_unresolved_movement_fail_safety(self):
        for field, value in (('exposed', 1), ('status', 'blocked'), ('status', 'evacuating')):
            observation = public_evidence()
            observation['districts'][0][field] = value
            completed, reward = objective_step(observation, self.objectives, [], terminal=True,
                evidence=evaluator_evidence())
            self.assertNotIn('population_safety', completed)
            self.assertLess(reward, 1)

    def test_pending_schedule_or_invalid_abort_cannot_collect_quiet_outcome_credit(self):
        self.assertEqual(objective_step(public_evidence(), self.objectives, [], terminal=True,
            evidence=evaluator_evidence(schedule_complete=False)), ([], 0))
        env = OpsControlCenterEnv()
        env.reset('control-002')
        for _ in range(3):
            env.submit_action({'unknown': True})
        self.assertEqual(env.verify_episode()['objective_score'], 0)

    def test_no_report_evidence_means_no_triage_credit(self):
        observation = public_evidence()
        observation['report_assessments'] = []
        completed, _ = objective_step(observation, self.objectives, [], terminal=True,
                                      evidence=evaluator_evidence())
        self.assertNotIn('evidence_triage', completed)

    def test_duplicate_terminal_predicates_and_extra_private_fields_are_rejected(self):
        duplicate = dict(self.objectives[0], id='same-again')
        with self.assertRaisesRegex(ValueError, 'duplicates an existing objective'):
            validate_objectives([self.objectives[0], duplicate])
        with self.assertRaisesRegex(ValueError, 'unknown field'):
            validate_objectives([dict(self.objectives[0], private_truth=True)])


class EvidenceTriageTests(unittest.TestCase):
    def test_assessment_requires_fresh_matching_observation_not_a_hidden_truth_label(self):
        ledger = EvidenceLedger()
        observation = public_evidence()
        action = dict(triage=[dict(report_ids=['report-0'], assessment='no_fire_observed')])
        records = ledger.validate(action, observation)
        self.assertEqual(records[0]['evidence_observed_at'], 9)
        self.assertEqual(ledger.public_state()['report_assessments'], [])
        ledger.apply(action, observation, records)
        self.assertEqual(ledger.public_state()['report_assessments'][0]['assessment'], 'no_fire_observed')
        observation['sensor_memory'][0]['burning'] = True
        with self.assertRaisesRegex(ValueError, 'match the observed sensor state'):
            ledger.validate(action, observation)

    def test_stale_and_pre_report_observations_are_rejected(self):
        action = dict(triage=[dict(report_ids=['report-0'], assessment='no_fire_observed')])
        for report_time, sample_time in ((1, 2), (10, 9)):
            observation = public_evidence()
            observation['reports'][0]['observed_at'] = report_time
            observation['sensor_memory'][0]['observed_at'] = sample_time
            with self.assertRaisesRegex(ValueError, 'fresh sensor evidence'):
                EvidenceLedger().validate(action, observation)

    def test_deferral_needs_no_sensor_but_does_not_resolve_the_report(self):
        observation = public_evidence()
        observation['sensor_memory'] = []
        action = dict(triage=[dict(report_ids=['report-0'], assessment='defer')])
        ledger = EvidenceLedger()
        records = ledger.validate(action, observation)
        ledger.apply(action, observation, records)
        self.assertEqual(ledger.public_state()['report_assessments'][0]['assessment'], 'defer')

    def test_only_structurally_matching_received_claims_can_be_linked_as_duplicates(self):
        observation = public_evidence()
        observation['reports'].append(dict(observation['reports'][0], id='duplicate-0', received_at=8))
        action = dict(triage=[dict(report_ids=['report-0', 'duplicate-0'], assessment='no_fire_observed')])
        records = EvidenceLedger().validate(action, observation)
        self.assertEqual(records[1]['duplicate_of'], 'report-0')
        observation['reports'][1]['location'] = [4, 4]
        with self.assertRaisesRegex(ValueError, 'share a location and observation time'):
            EvidenceLedger().validate(action, observation)
        with self.assertRaisesRegex(ValueError, 'received report IDs'):
            EvidenceLedger().validate(dict(triage=[dict(report_ids=['future-report'], assessment='defer')]),
                                      observation)

    def test_bad_combined_action_cannot_partially_record_assessments(self):
        env = OpsControlCenterEnv()
        env.reset('ops-002')
        observation, *_ = env.request_resources([dict(resource_id='engine-valverde',
            sector_id='report:report-0', mission='recon')])
        env.submit_action(dict(authorizations=[observation['offers'][0]['id']],
            readback_confirmations=[observation['readbacks'][0]['id']]))
        for _ in range(30):
            if env.get_situation()['resources'][0]['state'] == 'on_scene':
                break
            env.advance()
        result = env.submit_action(dict(triage=[dict(report_ids=['report-0'], assessment='no_fire_observed')],
                                        messages=[dict(recipient='unknown', purpose='evacuate')]))
        self.assertIsNotNone(result[4]['error'])
        self.assertEqual(result[0]['report_assessments'], [])
        env.reset('ops-002')
        self.assertEqual(env.get_situation()['issued_notices'], [])
        self.assertEqual(env.get_situation()['report_assessments'], [])

    def test_warning_is_judged_using_information_available_when_issued(self):
        observation = public_evidence()
        observation['sensor_memory'] = []
        ledger = EvidenceLedger()
        warning = dict(messages=[dict(recipient='near', purpose='evacuate')])
        ledger.apply(warning, observation, [])
        self.assertEqual(ledger.unsupported_evacuations, 0)
        observation['sensor_memory'] = [dict(x=3, y=4, burning=False, observed_at=10)]
        self.assertEqual(ledger.unsupported_evacuations, 0)
        ledger.apply(dict(messages=[dict(recipient='far', purpose='evacuate')]), observation, [])
        self.assertEqual(ledger.unsupported_evacuations, 1)
        ledger.apply(warning, observation, [])
        self.assertEqual(ledger.redundant_notices, 1)
        self.assertEqual(ledger.unsupported_evacuations, 2)


class BroaderControlRolloutTests(unittest.TestCase):
    def test_control_resolves_fire_but_arrival_only_policy_cannot_get_full_credit(self):
        _, good, _ = run_episode('control-000', 'control')
        _, arrivals, _ = run_episode('control-000', 'dispatch')
        self.assertTrue(good['all_objectives_met'])
        self.assertTrue(good['objective_components']['incident_resolved'])
        self.assertFalse(arrivals['objective_components']['incident_resolved'])
        self.assertLess(arrivals['objective_score'], good['objective_score'])

    def test_false_alarm_restraint_beats_indiscriminate_warnings(self):
        _, restrained, _ = run_episode('control-002', 'control')
        _, blanket, _ = run_episode('control-002', 'blanket_warning')
        self.assertEqual(restrained['diagnostics']['unnecessary_evacuation'], 0)
        self.assertTrue(restrained['objective_components']['communication_discipline'])
        self.assertFalse(blanket['objective_components']['communication_discipline'])
        self.assertLess(blanket['objective_score'], restrained['objective_score'])

    def test_timely_population_warning_beats_waiting_in_urgent_case(self):
        def early_warning(observation):
            if observation['minute'] == 0:
                return dict(messages=[dict(recipient='settlement-west', purpose='evacuate')])
            return {}

        _, early = rollout(early_warning, 'control-001')
        _, waiting = rollout(lambda observation: {}, 'control-001')
        self.assertTrue(early['objective_result']['by_kind']['population_safety'])
        self.assertFalse(waiting['objective_result']['by_kind']['population_safety'])
        self.assertLess(early['core_accounting']['components']['exposed_population'],
                        waiting['core_accounting']['components']['exposed_population'])

    def test_duplicate_notices_cannot_improve_control_score(self):
        policy = ControlBaseline()

        def repeated_notices(observation):
            action = policy(observation)
            action['messages'].extend([dict(recipient='valverde', purpose='status')] * 2)
            return action

        _, repeated = rollout(repeated_notices)
        _, normal, _ = run_episode('control-002', 'control')
        self.assertFalse(repeated['objective_result']['by_kind']['communication_discipline'])
        self.assertLess(repeated['objective_score'], normal['objective_score'])

    def test_public_trace_replay_rejects_fabricated_evidence_and_reward(self):
        trace, summary, _ = run_episode('control-002', 'control')
        self.assertEqual(verify_ops_trajectory(json.loads(json.dumps(trace)))['total_score'], 1)
        self.assertEqual(summary['invalid_actions'], 0)
        before_end = trace['transitions'][:-1]
        self.assertTrue(all(step['objective_reward'] == 0 for step in before_end))
        self.assertNotIn('active_fire_cells', json.dumps(trace['initial_observation']))
        self.assertNotIn('schedule_complete', json.dumps(trace['initial_observation']))
        tampered = copy.deepcopy(trace)
        assessed = next(step for step in tampered['transitions'] if step['observation']['report_assessments'])
        assessed['observation']['report_assessments'][0]['evidence_observed_at'] = -1
        with self.assertRaisesRegex(ValueError, 'does not replay'):
            verify_ops_trajectory(tampered)
        trace['transitions'][-1]['objective_reward'] = 0
        with self.assertRaisesRegex(ValueError, 'objective_reward mismatch'):
            verify_ops_trajectory(trace)


if __name__ == '__main__':
    unittest.main()
