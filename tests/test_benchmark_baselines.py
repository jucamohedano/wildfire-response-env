import copy
import unittest

from envs.wildfire_response.regional.benchmark_v1.baselines import PublicDispatcher, communication_view
from envs.wildfire_response.regional.benchmark_v1.catalog import task_ids
from envs.wildfire_response.regional.benchmark_v1.model import BenchmarkModelPolicy
from tests.test_benchmark_core import create


class ReferencePolicyTests(unittest.TestCase):
    def test_information_pair_has_same_opening_but_different_later_protection(self):
        first,second=create(),create()
        identifiers=task_ids(first.catalog,'development')
        left,_=first.reset(identifiers[0])
        right,_=second.reset(identifiers[8])
        for observation in (left,right):
            observation.pop('task_id')
        self.assertEqual(left,right)
        left=first.step({})[0]
        right=second.step({})[0]
        actor=PublicDispatcher()
        left_action=actor(left)
        right_action=actor(right)
        self.assertNotEqual(left_action.get('protection_orders'),right_action.get('protection_orders'))
        blind=PublicDispatcher(communications=False)
        self.assertFalse(blind(left).get('protection_orders'))
        self.assertFalse(blind(right).get('protection_orders'))

    def test_projection_and_policy_do_not_mutate_observations(self):
        environment=create()
        observation,_=environment.reset(task_ids(environment.catalog,'development')[0])
        previous=copy.deepcopy(observation)
        PublicDispatcher()(observation)
        communication_view(observation,False)
        self.assertEqual(previous,observation)

    def test_freshly_refuted_false_alarm_does_not_trigger_protection(self):
        environment=create()
        identifier=next(group['cases'][0]['id'] for group in environment.catalog['groups'] if group['family']=='false_alarm')
        observation,_=environment.reset(identifier)
        observation=environment.step({})[0]
        home=observation['districts'][0]['home']
        observation['reports']=[dict(id='clear',location=home[:],observed_at=0,received_at=0)]
        observation['sensor_memory']=[dict(x=home[0],y=home[1],burning=False,observed_at=observation['minute'])]
        observation['active_incidents']=[]
        for group in observation['districts']: group['smoke_present']=False
        self.assertFalse(PublicDispatcher()(observation).get('protection_orders'))

    def test_real_benchmark_model_uses_new_observation_version(self):
        environment=create()
        observation,_=environment.reset(task_ids(environment.catalog,'development')[0])
        def transport(payload):
            return dict(choices=[dict(finish_reason='tool_calls',message=dict(tool_calls=[dict(
                id='wait',type='function',function=dict(name='advance_time',arguments='{}'))]))])
        actor=BenchmarkModelPolicy(model='test:free',transport=transport,interval=0)
        action=actor(observation)
        self.assertTrue(environment.check_orders(action)['valid'])
