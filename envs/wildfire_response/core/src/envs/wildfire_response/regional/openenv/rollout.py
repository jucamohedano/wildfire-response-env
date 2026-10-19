"""Finish a regional episode through an external OpenEnv client without inference."""
import argparse
import json

from ..baselines import CooperativeBaseline
from .client import RegionalClient
from .models import RegionalAction


def main():
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:8001')
    parser.add_argument('--scenario-id',default='regional-train-001')
    args=parser.parse_args()
    policy=CooperativeBaseline()
    total=0
    with RegionalClient(base_url=args.url).sync() as client:
        result=client.reset(scenario_id=args.scenario_id)
        while not result.done:
            action=policy(result.observation.data)
            checked=client.step(RegionalAction(operation='check',**action))
            if not checked.observation.metadata['action_check']['valid']:
                raise ValueError('Scripted action failed its public contract check')
            result=client.step(RegionalAction(**action))
            total+=result.reward or 0
    print(json.dumps(dict(total_reward=total,done=result.done,minute=result.observation.data['minute'])))


if __name__=='__main__':
    main()
