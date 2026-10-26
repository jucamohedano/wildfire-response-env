import copy
import json
import unittest

from envs.wildfire_response.regional.benchmark_v1.model import BenchmarkModelPolicy, public_context, query
from envs.wildfire_response.tests.test_prepared_development import create


class BenchmarkModelTests(unittest.TestCase):
    def setUp(self):
        self.environment = create()
        self.observation, _ = self.environment.reset('prepared-dev-000')
        self.observation['observation_version'] = 'wildfire-benchmark-delivered-evidence-v1'
        initial = self.observation['reports'][0]
        self.observation['reports'] = [dict(initial, id=f'claim-{index}') for index in range(25)]

    def test_bounded_history_query_and_advance_share_public_state_without_time_advancing(self):
        replies = [('get_reports', {'ids':['claim-0']}), ('advance_time', {})]
        payloads=[]
        def transport(payload):
            payloads.append(payload)
            name, arguments = replies.pop(0)
            return dict(choices=[dict(finish_reason='tool_calls',message=dict(tool_calls=[dict(
                id='call',type='function',function=dict(name=name,arguments=json.dumps(arguments)))]))])
        actor=BenchmarkModelPolicy(model='test:free',transport=transport,interval=0)
        action=actor(self.observation)
        self.assertTrue(all(value==[] for value in action.values()))
        self.assertEqual(self.environment.get_situation()['minute'],0)
        opening=json.loads(payloads[0]['messages'][1]['content'])
        self.assertEqual(len(opening['reports']),20)
        self.assertEqual(opening['reports_count'],25)
        self.assertNotIn('claim-0',{report['id'] for report in opening['reports']})
        self.assertEqual(actor.records[0]['query_result']['records'][0]['id'],'claim-0')
        schema=next(tool['function']['parameters'] for tool in payloads[1]['tools']
                    if tool['function']['name']=='assess_reports')
        self.assertIn('claim-0',schema['properties']['triage']['items']['properties']['report_ids']['items']['enum'])

    def test_queries_reject_unreceived_ids_duplicate_fields_and_unbounded_pages(self):
        for raw in ('{"ids":["future"]}', '{"limit":21}', '{"offset":-1}', '{"limit":1,"limit":2}'):
            with self.assertRaises(ValueError):
                query(raw,self.observation['reports'])

    def test_future_history_and_nested_private_data_do_not_enter_context(self):
        observation=copy.deepcopy(self.observation)
        observation['reports'].append(dict(observation['reports'][0],id='future',received_at=100))
        observation['districts'][0]['private_label']='PRIVATE-SENTINEL'
        context=public_context(observation)
        self.assertNotIn('PRIVATE-SENTINEL',json.dumps(context))
        self.assertNotIn('future',{entry['id'] for entry in context['reports']})
        self.assertEqual(context['reward_weights'],dict(population_protection=.8,unburned_footprint=.2))
