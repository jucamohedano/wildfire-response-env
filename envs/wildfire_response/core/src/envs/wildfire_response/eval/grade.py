"""Optional bounded hindsight search, kept outside the training step loop."""
import argparse
import copy
import itertools
import math
import time

from simulator import oracle
from ..core.environment import WildfireResponseEnv
from ..core.reward import REWARD_VERSION
from .run import dump, load


def grade(trajectory, budget=1.0):
    if budget <= 0:
        raise ValueError('budget must be positive')
    env = WildfireResponseEnv(max_decisions=trajectory['max_decisions'])
    env.reset(trajectory['scenario_id'], trajectory['seed'])
    actual = trajectory['transitions'][0]['action']
    started = time.monotonic()
    def score(action):
        clone = copy.deepcopy(env)
        *_, info = clone.step(action)
        return None if info['error'] else clone.evaluator_state()['cost']
    actual_cost = score(actual)
    options = oracle.vehicle_options(env.sim)
    total = math.prod(len(items) for _, items in options)
    best, best_cost = actual, actual_cost
    checked = 0
    for parts in itertools.islice(itertools.product(*(items for _, items in options)), 8):
        if time.monotonic()-started >= budget:
            break
        assembled = oracle.assemble(zip((key for key, _ in options), parts))
        action = copy.deepcopy(actual)
        action['fleet'] = {key: assembled[key] for key in ('scout_orders','extinguisher_orders','truck_orders')}
        action['fleet'].update(mission='Hindsight fleet alternative', reason='Bounded counterfactual search')
        value = score(action)
        checked += 1
        if value is not None and (best_cost is None or value < best_cost):
            best, best_cost = action, value
    truncated = checked < total
    return dict(reference='best_found', objective=REWARD_VERSION, truncated=truncated,
                eligible_teacher=bool(actual_cost is not None and not truncated),
                actual_cost=actual_cost, best_cost=best_cost, best_action=best,
                regret=actual_cost-best_cost if actual_cost is not None and best_cost is not None else None,
                checked_alternatives=checked, candidate_combinations=total,
                limitation='Fleet alternatives only; outgoing messages held fixed; first interval only')


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('trajectory')
    parser.add_argument('--output', required=True)
    parser.add_argument('--budget', type=float, default=1.0)
    args = parser.parse_args()
    dump(args.output, grade(load(args.trajectory), args.budget))


if __name__ == '__main__':
    main()
