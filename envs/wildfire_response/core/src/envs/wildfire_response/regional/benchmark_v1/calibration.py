import copy
import math


VERSION = 'wildfire-benchmark-calibration-v1'
PROFILES = {
    'central': dict(travel_multiplier=1.0, suppression_multiplier=1.0),
    'faster_stronger': dict(travel_multiplier=.75, suppression_multiplier=1.5),
    'slower_weaker': dict(travel_multiplier=1.5, suppression_multiplier=.5),
}
REGISTRY = {
    'travel': dict(unit='simulation_minutes', evidence='assumed_decomposition',
        source='data/malaga_2021/synthetic_runtime_inputs_v1.json#transit',
        limitation='Aggregate response time does not separately identify mobilization and road transit'),
    'suppression': dict(unit='probability_per_attempt', evidence='synthetic',
        source='regional/pack assumptions', limitation='No empirical firefighter productivity calibration'),
    'footprint': dict(unit='hectares', evidence='grid_derived',
        source='pinned prepared grid', limitation='Whole grid-cell union, not a surveyed fire perimeter'),
    'population_exposure': dict(unit='persons', evidence='synthetic',
        source='regional/protection/world.py', limitation='Abstract dose/direct-fire exposure, not mortality'),
    'communication': dict(unit='simulation_minutes', evidence='synthetic',
        source='regional/incident/events.py', limitation='Template timing is not historical call-center evidence'),
}


def public_profile(profile='central'):
    if profile not in PROFILES:
        raise ValueError('Unknown frozen calibration profile')
    return dict(version=VERSION, name=profile, **PROFILES[profile],
                evidence='Declared sensitivity assumptions; not empirically fitted', registry=copy.deepcopy(REGISTRY))


def apply_profile(scenario, profile='central'):
    result = copy.deepcopy(scenario)
    settings = public_profile(profile)
    result['grounded']['ground_transit_minutes'] = max(1, math.ceil(
        result['grounded']['ground_transit_minutes'] * settings['travel_multiplier']))
    result['benchmark_calibration'] = settings
    return result


def apply_runtime_profile(core, profile='central'):
    settings = public_profile(profile)
    probabilities = core.pack['assumptions']['suppression_probability']
    for kind, probability in list(probabilities.items()):
        probabilities[kind] = min(1., probability * settings['suppression_multiplier'])
