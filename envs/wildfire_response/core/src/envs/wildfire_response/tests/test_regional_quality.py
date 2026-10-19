import copy
import json
from pathlib import Path
import tempfile
import unittest

from envs.wildfire_response.eval.run import load
from envs.wildfire_response.regional import RegionalResponseEnv
from envs.wildfire_response.regional.evaluate import export_tasks
from envs.wildfire_response.regional.run import replay, run
from envs.wildfire_response.regional.scenarios import FAMILIES, generate_tasks, get_scenario
from envs.wildfire_response.regional.verifier import verify


class RegionalQualityTests(unittest.TestCase):
    def test_grouped_dataset_is_reproducible_and_excludes_occupied_ignitions(self):
        tasks = generate_tasks()
        self.assertEqual(tasks, generate_tasks())
        self.assertEqual(len(tasks), 36)
        for split, count in [('train',24),('validation',6),('test',6)]:
            selected = [task for task in tasks if task['split']==split]
            self.assertEqual(len(selected),count)
            self.assertEqual({task['family'] for task in selected},set(FAMILIES))
        for field in ('seed','group_id','configuration_sha256'):
            self.assertEqual(len({task[field] for task in tasks}),36)
        env = RegionalResponseEnv()
        env.reset()
        homes = {tuple(group['home']) for group in env.sim.groups.values()}
        for task in tasks:
            self.assertEqual(get_scenario(task['id']),task)
            for event in task['events']:
                if event['kind']=='ignition':
                    self.assertNotIn(tuple(event['location']), homes)

    def test_task_export_separates_public_metadata_and_private_events(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/'tasks'
            export_tasks(root)
            metadata = json.loads((root/'train.jsonl').read_text().splitlines()[0])
            self.assertNotIn('events',metadata)
            self.assertNotIn('family',metadata)
            self.assertEqual(len(load(root/'private'/'train.json')),24)

    def test_action_checker_is_bounded_nonmutating_and_not_a_future_oracle(self):
        env = RegionalResponseEnv()
        observation,_ = env.reset('regional-003')
        other = RegionalResponseEnv()
        changed = copy.deepcopy(env.task)
        changed['events'][1]['location'] = [1,1]
        other.reset(scenario=changed)
        before = copy.deepcopy(env.evaluator_state())
        action = dict(requests=[dict(resource_id='engine-valverde',sector_id='report:report-0',mission='suppress')])
        self.assertEqual(env.check_action(action), other.check_action(action))
        self.assertEqual(env.observe(),observation)
        self.assertEqual(env.evaluator_state(),before)
        env.check_action({})
        env.check_action({})
        with self.assertRaises(ValueError):
            env.check_action({})
        env.step({})
        self.assertEqual(env.check_action({})['remaining_checks'],2)

    def test_report_private_annotations_and_locationless_noise_are_safe(self):
        env = RegionalResponseEnv()
        task = get_scenario('regional-train-012')
        task['reports'][0].update(true_claim=True, private_label='secret', phone_number='not-for-actor')
        observation,_=env.reset(scenario=task)
        text=json.dumps(observation)
        for field in ('true_claim','private_label','phone_number','private_annotations'):
            self.assertNotIn('"'+field+'"',text)
        for _ in range(2):
            observation,*_=env.step({})
        self.assertTrue(any(report['location'] is None for report in observation['reports']))
        self.assertNotIn('report:irrelevant-0',{sector['id'] for sector in observation['sectors']})

    def test_hostile_actions_do_not_mutate_resource_allocations(self):
        nested=[]
        for _ in range(1100):
            nested=[nested]
        payloads=[dict(requests=True),dict(authorizations=[[]]),dict(explanation=float('nan')),
                  dict(requests=[dict(resource_id=nested,sector_id='report:report-0',mission='suppress')]),
                  dict(messages=[dict(recipient='settlement-west',purpose='teleport')]),
                  dict(requests=[dict(resource_id='engine-valverde',sector_id='future',mission='suppress')])]
        for payload in payloads:
            env=RegionalResponseEnv()
            env.reset()
            self.assertFalse(env.check_action(payload)['valid'])
            *_,info=env.step(payload)
            self.assertIsNotNone(info['error'])
            self.assertTrue(all(unit['state']=='available' for unit in env.resources.values()))

    def test_independent_checker_rejects_counter_and_terrain_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'run'
            run(root,'regional-002','cooperative')
            trajectory,evaluator=load(root/'trajectory.json.gz'),load(root/'evaluator.json.gz')
            self.assertTrue(verify(trajectory,evaluator)['passed'])
            modified=copy.deepcopy(evaluator)
            modified['components']['committed_unit_minutes']+=1
            with self.assertRaises(ValueError):
                verify(trajectory,modified)
            modified=copy.deepcopy(evaluator)
            modified['audit_minutes'][0]['resource_water']['engine-valverde']=999999
            with self.assertRaises(ValueError):
                verify(trajectory,modified)
            modified=copy.deepcopy(evaluator)
            modified['fire_checkpoint']['state']['cells'][0][0]['burned']=float('nan')
            with self.assertRaises(ValueError):
                verify(trajectory,modified)

    def test_weather_only_false_alarm_still_accounts_for_unnecessary_evacuation(self):
        env=RegionalResponseEnv(max_decisions=3)
        task=get_scenario('regional-002')
        task['events']=[dict(tick=1,kind='wind',wind=[0,1])]
        env.reset(scenario=task)
        env.step(dict(messages=[dict(recipient='settlement-west',purpose='evacuate')]))
        env.step({})
        self.assertGreater(env.evaluator_state()['components']['unnecessary_evacuation'],0)

    def test_blanket_warning_cannot_outscore_resolving_an_active_incident(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            warning=run(root/'warning','regional-001','blanket_warning')
            resolved=run(root/'resolved','regional-001','cooperative')
            self.assertEqual(warning['components']['unresolved_incident'],1)
            self.assertEqual(resolved['components']['unresolved_incident'],0)
            self.assertGreater(resolved['total_reward'],warning['total_reward'])
            old=load(root/'warning'/'trajectory.json.gz')
            old['reward_version']='regional-outcome-cost-v1'
            with self.assertRaises(ValueError):
                replay(old)

    def test_independent_checker_rejects_duplicate_aircraft_drop(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'run'
            run(root,'regional-000','cooperative')
            trajectory,evaluator=load(root/'trajectory.json.gz'),load(root/'evaluator.json.gz')
            drop=next(item for item in evaluator['communications'] if item['kind']=='water_drop')
            evaluator['communications'].append(copy.deepcopy(drop))
            with self.assertRaises(ValueError):
                verify(trajectory,evaluator)

    def test_observed_fire_edges_become_targets_without_revealing_hidden_fire(self):
        env=RegionalResponseEnv()
        env.reset()
        env.sim.add_fire(4,4)
        self.assertNotIn('observed:4:4',{sector['id'] for sector in env.observe()['sectors']})
        env._sense_at([4,4],1,'test-lookout')
        self.assertIn('observed:4:4',{sector['id'] for sector in env.observe()['sectors']})
        env.sim.cells[4][4]['heat']=0
        env._sense_at([4,4],1,'test-lookout')
        self.assertIn('observed:4:4',{sector['id'] for sector in env.observe()['sectors']})
        self.assertTrue(env.check_action(dict(requests=[dict(resource_id='engine-valverde',
            sector_id='observed:4:4',mission='recon')]))['valid'])


if __name__=='__main__':
    unittest.main()
