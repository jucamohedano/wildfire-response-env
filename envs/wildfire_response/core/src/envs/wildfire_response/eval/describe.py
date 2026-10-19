"""Print a compact public trajectory for human inspection."""
import argparse

from .run import load


def describe(trajectory, limit=32):
    lines = [f"# {trajectory['scenario_id']} / {trajectory['policy']}",
             f"Seed: {trajectory['seed']}. Total reward: {trajectory['total_reward']:.6f} (higher is better)."]
    for transition in trajectory['transitions'][:limit]:
        observation, action = transition['observation'], transition['action']
        lines.append(f"\nTick {observation['tick']}: reward {transition['reward']:.6f}; "
                     f"{len(observation['new_report_ids'])} newly available reports.")
        for key in ('scout_orders','extinguisher_orders','truck_orders'):
            for order in action.get('fleet', {}).get(key, []):
                ident = order.get('drone_id') or order.get('truck_id')
                target = order.get('district_id') or order.get('waypoints') or [order.get('target_x'),order.get('target_y')]
                lines.append(f"- {ident}: {order['command']} {target}; {order.get('reason','')}")
        for message in action.get('messages', []):
            lines.append(f"- {message['channel']} to {message['recipient']}: {message['purpose']}")
        if transition['info'].get('error'):
            lines.append(f"- Rejected: {transition['info']['error']}")
        lines.append('Population: '+', '.join(f"{group['id']}={group['status']}" for group in observation['districts']))
    final = trajectory['transitions'][-1]
    lines.append(f"\nTerminated: {final['terminated']}; truncated: {final['truncated']}.")
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('trajectory')
    parser.add_argument('--limit', type=int, default=32)
    args = parser.parse_args()
    print(describe(load(args.trajectory), args.limit))


if __name__ == '__main__':
    main()
