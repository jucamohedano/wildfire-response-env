"""Evaluation-only operational configurations on the pinned development landscape.

Shared parent seeds/fire geography are deliberate. This measures transfer across
operational configurations, not independent incidents or unseen terrain.
"""
import copy
import random
from .scenarios import REAL_MAP_DEVELOPMENT_CASE_IDS, EVALUATION_CASE_IDS
from .real_map_scenarios import build_real_map_development_scenario, PACK_PIN, NETWORK_PIN
from ..regional.ops.generator import digest

VERSION = 'wildfire-incident-day-evaluation-v1'
GENERATION_SEED = 20261102
PROFILES = (
    dict(name='limited_water', population=[180, 140], water_fraction=.5, facility_fraction=.4,
         mobilization_extra=0, setup_extra=3, reserve_delay=0),
    dict(name='high_population', population=[380, 300], water_fraction=.8, facility_fraction=.8,
         mobilization_extra=2, setup_extra=0, reserve_delay=0),
    dict(name='slow_mobilization', population=[260, 220], water_fraction=.9, facility_fraction=.7,
         mobilization_extra=8, setup_extra=6, reserve_delay=0),
    dict(name='late_crew_reserve', population=[300, 250], water_fraction=.7, facility_fraction=.6,
         mobilization_extra=3, setup_extra=2, reserve_delay=180),
)


def build_evaluation_scenario(inputs, network, identifier, seed=None):
    if identifier not in EVALUATION_CASE_IDS:
        raise ValueError('Unknown evaluation scenario')
    index = EVALUATION_CASE_IDS.index(identifier)
    family, variant = divmod(index, 4)
    # Fixed ordering is independent of any model output or baseline score.
    order = list(range(4))
    random.Random(GENERATION_SEED + family).shuffle(order)
    config = copy.deepcopy(PROFILES[order[variant]])
    parent_id = REAL_MAP_DEVELOPMENT_CASE_IDS[family * 2 + variant % 2]
    parent = build_real_map_development_scenario(inputs, network, parent_id, seed)
    scenario = copy.deepcopy(parent)
    scenario.update(id=identifier, version=VERSION, parent_scenario_sha256=parent['sha256'])
    scenario['evaluation'] = dict(split='evaluation', generation_seed=GENERATION_SEED,
        parent_id=parent_id, configuration_group=f'evaluation-v1-family-{family}',
        configuration=config, shared_terrain=True, shared_parent_fire_seed=True,
        historical_replay=False, training_eligible=False)
    for group, count in zip(scenario['groups'], config['population']):
        group['count'] = count
        # Do not invent inherited exposure when demand is changed.
        if group.get('exposed', 0):
            raise ValueError('Evaluation demand changes require unexposed inherited cohorts')
    for destination in scenario['destinations']:
        destination['capacity'] = sum(config['population'])
    for unit in scenario['resources']:
        if unit['capacity_litres']:
            unit['water_litres'] = int(unit['water_litres'] * config['water_fraction'])
        if unit['id'] == 'crew-1' and config['reserve_delay']:
            if unit['state'] not in ('available', 'unavailable'):
                raise ValueError('Reserve delay cannot rewrite an inherited active mission')
            unit.update(available_at=max(unit['available_at'], config['reserve_delay']), state='unavailable')
    profile = scenario['profile']
    for facility in profile['facilities'].values():
        facility['water_litres'] = int(facility['water_litres'] * config['facility_fraction'])
    # Keep the inherited version/capability flags: these are configurations of
    # the same verified runtime, not a new physics or reward implementation.
    profile['mobilization_minutes'] = profile.get('mobilization_minutes', 1) + config['mobilization_extra']
    profile['ground_setup_minutes'] += config['setup_extra']
    scenario['handover_notice'] += (' EXERCISE. Evaluation configuration: '+config['name']+'. '
        'Resource inventories and population demand are synthetic exercise values.')
    scenario['sha256'] = digest({k: v for k, v in scenario.items() if k != 'sha256'})
    return scenario
