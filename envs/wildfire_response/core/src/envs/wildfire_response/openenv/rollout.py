"""External Python client: complete one episode without any model API."""
import argparse
import json

from ..baselines import DeterministicBaseline
from .client import WildfireClient
from .models import WildfireAction


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    parser.add_argument('--scenario-id', default='train-000')
    args = parser.parse_args()
    policy = DeterministicBaseline()
    total = 0.0
    with WildfireClient(base_url=args.url).sync() as client:
        result = client.reset(scenario_id=args.scenario_id)
        while not result.done:
            action = WildfireAction(**policy(result.observation.data))
            result = client.step(action)
            total += result.reward or 0.0
            print(json.dumps(dict(tick=result.observation.data['tick'], reward=result.reward, done=result.done)), flush=True)
    print(json.dumps(dict(total_reward=total)))


if __name__ == '__main__':
    main()
