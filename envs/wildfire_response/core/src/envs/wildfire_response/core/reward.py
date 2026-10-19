"""Outcome losses, independently reconstructable from evaluator snapshots."""

REWARD_VERSION = 'wildfire-response-cost-v1'
WEIGHTS = dict(exposed_population=100.0, burned_terrain=1.0,
               unnecessary_evacuation=0.1, redundant_messages=0.002, invalid_actions=0.05,
               abandoned_episode=102.0)


def components(sim, counters):
    population = sum(group['count'] for group in sim.groups.values())
    return dict(exposed_population=sum(group['burnt'] for group in sim.groups.values()) / population,
                burned_terrain=sum(cell.get('burned', 0) for row in sim.cells for cell in row) / (sim.width*sim.height),
                unnecessary_evacuation=counters['unnecessary_person_ticks'] / (population*256),
                redundant_messages=counters['redundant_messages'], invalid_actions=counters['invalid_actions'],
                abandoned_episode=counters['abandoned_episode'])


def cost(values):
    return sum(WEIGHTS[key]*values[key] for key in WEIGHTS)
