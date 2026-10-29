"""Run a passive synthetic day against an already-running local REST server."""
import argparse
import json

from .incident_day_client import IncidentDayClient


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8005')
    parser.add_argument('--scenario-id', default='incident-day-dev-000')
    parser.add_argument('--max-steps', type=int, default=1440)
    args = parser.parse_args(argv)
    if not 1 <= args.max_steps <= 1440:
        parser.error('max-steps must be between 1 and 1440')
    client = IncidentDayClient(args.base_url)
    result = client.reset(args.scenario_id)
    for _ in range(args.max_steps):
        result = client.step({'advance_minutes': 60})
        if result['done']:
            break
    print(json.dumps({'minute': result['data']['minute'], 'reward': result['reward'],
                      'terminated': result['terminated'], 'truncated': result['truncated']}))
    return 0 if result['terminated'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
