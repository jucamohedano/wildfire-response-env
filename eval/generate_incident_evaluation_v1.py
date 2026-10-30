"""Compile distinct, source-grounded synthetic incident-day evaluation cases.

This is an exercise catalog, not a reconstruction of Sierra Bermeja or a
calibrated wildfire model. Pre-report duration is a synthetic warm-up after an
assumed initial front, not an observed ignition or a measured detection delay.
The locked data pack remains unmodified and requires explicit opt-in.
"""
import copy
import hashlib
import json
import math
from pathlib import Path

from eval.hub_runtime import make_environment, verify_package
from eval.incident_weather_v1 import WeatherProfile, advance, initial_state, spread_factor
from envs.wildfire_response.incident_day.real_map_scenarios import (
    PACK_PIN, NETWORK_PIN, POST_NODES, HOME_NODES, _supported_journey, build_real_map_v2_scenario,
)
from envs.wildfire_response.incident_day.routing import HazardRouter
from envs.wildfire_response.incident_day.scenarios import DayFire, grid_point
from envs.wildfire_response.regional.ops.generator import digest

VERSION = 'wildfire-incident-eval-catalog-v1'
SCENARIO_VERSION = 'wildfire-incident-evaluation-scenario-v1'
SEED_BASE = 8140000


def w(minute, wind, temperature_c, relative_humidity_pct, rain_mm_per_hour=0):
    return dict(minute=minute, wind=list(wind), temperature_c=temperature_c,
                relative_humidity_pct=relative_humidity_pct, rain_mm_per_hour=rain_mm_per_hour)


# Distinct causal exercises, not variations in phrasing. Numbers are declared
# assumptions for simulation, not observed 2021 conditions or certified limits.
TEMPLATES = (
    dict(family='hot_dry_initial_response', parent=0, center=(16, 28), radius=2, warmup=60, reserve_crew=True,
         weather=[w(0,[1,0],35,22),w(360,[1.5,0],38,16),w(1080,[0.5,-0.5],25,48)]),
    dict(family='late_large_fire', parent=5, center=(16, 26), radius=4, warmup=240,
         ground_delay=240, ground_waves=2, weather=[w(0,[1,0],34,25),w(300,[1.5,0.5],37,20),w(420,[-0.5,0],26,46)]),
    dict(family='wind_shift_access', parent=1, center=(16, 26), radius=2, warmup=60,
         weather=[w(0,[1,0],31,30),w(15,[0,2],32,27),w(600,[-1,0],28,40)]),
    dict(family='rain_front', parent=0, center=(16, 26), radius=3, warmup=120,
         weather=[w(0,[0.5,-0.5],25,48),w(240,[0,1],20,82,4),w(720,[-0.5,0],19,76,1)]),
    dict(family='ground_access_restriction', parent=2, center=(16, 28), radius=2, warmup=120,
         weather=[w(0,[-1,0],30,32),w(360,[0,-1],34,24),w(960,[0.5,0],24,55)]),
    dict(family='aircraft_window', parent=3, center=(17, 26), radius=3, warmup=60,
         start_utc='2021-09-09T09:00:00Z',
         weather=[w(0,[1,0],28,35),w(180,[2.9,0],33,24),w(480,[1,0],27,42)]),
    dict(family='occupied_destination_closure', parent=4, center=(16, 28), radius=2, warmup=60,
         weather=[w(0,[0,-1],26,46),w(360,[0.5,-1],31,30),w(900,[-1,0],23,60)]),
    dict(family='regional_shortage', parent=5, center=(16, 26), radius=3, warmup=120,
         ground_delay=180, ground_waves=2, weather=[w(0,[-1,0],33,24),w(480,[1,1],36,18),w(1020,[0,-0.5],25,48)]),
    dict(family='conflicting_incoming_calls', parent=0, center=(15, 26), radius=3, warmup=60,
         reports='conflicting', weather=[w(0,[0,-1],29,35),w(300,[1,0],34,25),w(900,[0,1],24,55)]),
    dict(family='night_to_dawn_response', parent=3, center=(17, 26), radius=3, warmup=120,
         start_utc='2021-09-09T18:00:00Z',
         weather=[w(0,[1,0],23,55),w(480,[0,-1],18,75),w(900,[1,0],30,30)]),
    dict(family='large_cooling_rain', parent=0, center=(16, 25), radius=4, warmup=120,
         weather=[w(0,[0,-1],30,35),w(180,[-1,0],22,78,2),w(600,[0.5,0],21,82,1)]),
    dict(family='staggered_limited_resources', parent=5, center=(14, 26), radius=3, warmup=180,
         ground_delay=300, ground_waves=2, low_water=True,
         weather=[w(0,[1,0],32,27),w(240,[1,1],35,20),w(420,[0,-1],24,52)]),
)


def _burning(fire):
    return {(column, row) for row, cells in enumerate(fire.cells)
            for column, cell in enumerate(cells) if fire.burning(cell)}


def _preflight(inputs, network, scenario):
    """Validate initial terrain, uncertain cells, boundary and occupied posts."""
    schedule = scenario['weather_schedule']
    # The warm-up uses the handover weather throughout; changes after handover
    # are not allowed to influence the earlier period.
    initial = dict(schedule[0]); initial.pop('received_at', None)
    profile = WeatherProfile.from_records([initial])
    state = initial_state(profile)
    fire = DayFire(inputs, scenario['seed'])
    fire.set_wind(x=scenario['wind'][0], y=scenario['wind'][1])
    unresolved = {(c['column'], c['row']) for c in
        inputs['provenance']['fuel_resolution']['document']['cells'] if c['land_kind'] == 'unresolved'}
    for x, y in scenario['initial_fire']:
        if not inputs['fuel_known_mask'][y][x]:
            raise ValueError('Ignition must use source-known fuel')
        fire.add_fire(x, y)
    for minute in range(scenario['warmup_minutes']):
        active = _burning(fire)
        if any(max(abs(x-a),abs(y-b)) <= 1 for x,y in unresolved for a,b in active):
            raise ValueError('Warm-up reaches unresolved land or water')
        state = advance(profile, state, minute + 1)
        fire.rules['spread_factor'] = spread_factor(state, base_factor=.5)
        fire.step()
        if fire.boundary_contact_at is not None:
            raise ValueError('Warm-up reaches domain boundary')
    active = _burning(fire)
    if any(max(abs(x-a),abs(y-b)) <= 1 for x,y in unresolved for a,b in active):
        raise ValueError('Handover reaches unresolved land or water')
    footprint = sum(c['burned'] > 0 or fire.burning(c) for row in fire.cells for c in row)
    if footprint * inputs['grid']['cell_km'] ** 2 * 100 <= 500:
        raise ValueError('Handover does not meet the existing large-fire contract')
    allowed = frozenset(scenario['profile']['allowed_road_edge_ids'])
    router = HazardRouter(network, inputs, inputs['heights'], walk_mask=inputs['walk_mask'],
                          allowed_edge_ids=allowed)
    for node in POST_NODES:
        if not router.safe_position(network.nodes[node], active, scenario['wind'], mode='crew'):
            raise ValueError('Occupied post is unsafe at handover')
    for node in HOME_NODES:
        if not router.safe_position(network.nodes[node], active, scenario['wind'], mode='crew'):
            raise ValueError('Synthetic home is unsafe at handover')
    # A route alone is not a work opportunity: the staging point must be near
    # observed fire and admit a mapped, safe return under the initial hazards.
    useful, ready_at = {}, {}
    hazards = router._hazards(active, scenario['wind'])
    for kind in ('engine', 'crew'):
        unit = next(unit for unit in scenario['resources'] if unit['id'] == f'{kind}-0')
        options = router.options(unit['position'], scenario['sector_grid'], mode=kind,
                                 burning=active, wind=scenario['wind'], limit=32)['options']
        count = 0
        for option in options:
            if not any(math.dist(cell, scenario['sector_grid']) <= 3
                       and math.dist(cell, option['approach_grid']) <= 3 for cell in active):
                continue
            back = router.options(option['approach_point'], grid_point(inputs, unit['position']),
                                  mode=kind, burning=active, wind=scenario['wind'],
                                  target_metric=unit['position'], limit=1)['options']
            if back and _supported_journey(router, option['journey'], hazards, allowed) and \
                    _supported_journey(router, back[0]['journey'], hazards, allowed):
                count += 1
        if not count:
            raise ValueError(f'No supported useful {kind} approach and return at handover')
        useful[kind] = count
        ready_at[kind] = unit['available_at']
    wind_check = None
    if scenario['family'] == 'wind_shift_access':
        unit = next(unit for unit in scenario['resources'] if unit['id'] == 'engine-0')
        changed = scenario['weather_schedule'][1]['wind']
        old = router.options(unit['position'], scenario['sector_grid'], mode='engine',
                             burning=active, wind=scenario['wind'], limit=32)['options']
        new = router.options(unit['position'], scenario['sector_grid'], mode='engine',
                             burning=active, wind=changed, limit=32)['options']
        old_points = {tuple(option['approach_grid']) for option in old}
        new_points = {tuple(option['approach_grid']) for option in new}
        if not old_points - new_points or not new_points:
            raise ValueError('Wind change must remove an approach while retaining an alternative')
        wind_check = dict(lost_approach=True, alternative_remains=True)
    return dict(initial_cells=len(scenario['initial_fire']), handover_footprint_cells=footprint,
                handover_active_cells=len(active), warmup_minutes=scenario['warmup_minutes'],
                geometric_work_approaches=useful, first_unit_available_at=ready_at,
                initial_wind_counterfactual=wind_check)


def build_catalog(inputs, network):
    if inputs['sha256'] != PACK_PIN or network.sha256 != NETWORK_PIN:
        raise ValueError('Evaluation catalog requires the pinned source-map inputs and network')
    cases, evidence = [], []
    for index, spec in enumerate(TEMPLATES):
        parent = build_real_map_v2_scenario(inputs, network,
            f"incident-day-realmap-v2-{spec['parent']:03}")
        scenario = copy.deepcopy(parent)
        center, radius = spec['center'], spec['radius']
        cells = [[x,y] for y in range(center[1]-radius,center[1]+radius+1)
                 for x in range(center[0]-radius,center[0]+radius+1)
                 if 0 < y < inputs['grid']['height']-1 and 0 < x < inputs['grid']['width']-1
                 and inputs['fuel_known_mask'][y][x]]
        schedule = copy.deepcopy(spec['weather'])
        WeatherProfile.from_records(schedule)
        scenario.update(id=f'incident-eval-v1-{index:03}', version=SCENARIO_VERSION,
            parent_scenario_sha256=parent['sha256'], seed=SEED_BASE+index,
            family=spec['family'], initial_fire=cells, sector_grid=[16,28], warmup_minutes=spec['warmup'],
            wind=schedule[0]['wind'][:], weather_schedule=schedule,
            synthetic=True, historical_replay=False)
        scenario['profile']['version'] = 'wildfire-incident-evaluation-profile-v1'
        scenario['profile']['realmap_v2_wind_coverage'] = False
        scenario['profile']['causal_coverage'] = False
        scenario['start_utc'] = spec.get('start_utc',scenario['start_utc'])
        scenario['events'] = [copy.deepcopy(event) for event in parent['events'] if event['kind'] != 'wind']
        if spec['family'] in ('late_large_fire', 'staggered_limited_resources'):
            # Place the shortage event while these high-growth exercises still
            # have a plausible modeled opportunity before the terrain guard.
            for event in scenario['events']:
                if event['kind'] == 'center_unavailable':
                    event.update(minute=360, until=480)
        scenario['events'].extend(dict(minute=item['minute'], kind='wind', wind=item['wind'][:])
                                  for item in schedule[1:])
        scenario['events'].sort(key=lambda item: item['minute'])
        if spec.get('ground_waves') == 2:
            scenario['resources'] = [unit for unit in scenario['resources']
                                     if not (unit['kind'] in ('crew','engine') and unit['id'].endswith('-shift-3'))]
        if spec.get('reserve_crew'):
            reserve = copy.deepcopy(next(unit for unit in scenario['resources'] if unit['id'] == 'crew-0'))
            reserve.update(id='crew-0-reserve', state='unavailable', available_at=120,
                           duty_end_at=480, total_work_remaining=360)
            scenario['resources'].append(reserve)
        if 'ground_delay' in spec:
            for unit in scenario['resources']:
                if unit['kind'] in ('crew','engine') and unit['available_at'] == 0 and unit['id'] != 'engine-0':
                    unit.update(state='unavailable', available_at=spec['ground_delay'])
        if spec.get('low_water'):
            for unit in scenario['resources']:
                if unit['kind'] == 'engine' and unit['available_at'] == 0:
                    unit['water_litres'] = min(unit['water_litres'], 1000)
        if spec.get('reports') == 'conflicting':
            x,y = center
            scenario['incoming_reports'] = [
                dict(minute=20,source='synthetic-caller-1',role='resident',channel='phone',
                     location=[x,y],text='EXERCISE. I can see flames and smoke at this location.',delay=4),
                dict(minute=20,source='synthetic-caller-1',role='resident',channel='phone',
                     location=[x,y],text='EXERCISE. I can see flames and smoke at this location.',delay=12),
                dict(minute=22,source='synthetic-caller-2',role='resident',channel='phone',
                     location=[x,y],text='EXERCISE. I see no flames here; smoke appears farther northeast.',delay=8),
            ]
        scenario['handover_notice'] = ('EXERCISE. Synthetic initial fire and resource situation on source geography; '
            'the pre-handover period and weather are assumed, not a historical dispatch record.')
        if spec['parent'] == 4:
            scenario['handover_notice'] += ' EXERCISE. A synthetic cohort is already at the reception centre.'
        scenario['sha256'] = digest({k:v for k,v in scenario.items() if k != 'sha256'})
        gate = _preflight(inputs, network, scenario)
        gate['crew_slots'] = sum(unit['kind'] == 'crew' for unit in scenario['resources'])
        gate['engine_slots'] = sum(unit['kind'] == 'engine' for unit in scenario['resources'])
        cases.append(scenario)
        evidence.append(dict(id=scenario['id'], family=spec['family'], **gate))
    payload = dict(version=VERSION, inputs_sha256=inputs['sha256'], network_sha256=network.sha256,
                   cases=cases)
    payload['sha256'] = digest(payload)
    return payload, evidence


def main():
    import argparse
    from eval.incident_weather_runtime_v1 import IncidentWeatherRuntimeV1
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    verify_package(args.data_dir)
    release = json.loads((Path(__file__).resolve().parents[1]/'data/hub_runtime_v1/release.json').read_text())
    manifest_sha = hashlib.sha256((Path(args.data_dir)/'manifest.json').read_bytes()).hexdigest()
    if manifest_sha != release['manifest_sha256']:
        raise ValueError('Dataset manifest differs from the trusted release lock')
    env, _ = make_environment(args.data_dir, 'es-ma-2021', allow_unapproved_data=True)
    catalog, evidence = build_catalog(env.inputs, env.network)
    # Record trusted expected pins before evaluation begins. The source/data
    # catalog and future events stay in an owner-only directory, never prompts.
    runtime = IncidentWeatherRuntimeV1(catalog, expected_pins={})
    lock = dict(version='wildfire-incident-eval-lock-v1',
                generator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                dataset_manifest_sha256=manifest_sha,
                policy_sha256=hashlib.sha256(Path(__file__).with_name('incident_day_policies.py').read_bytes()).hexdigest(),
                facade_sha256=hashlib.sha256(Path(__file__).with_name('validate_incident_day_runtime.py').read_bytes()).hexdigest(),
                runtime_pins=runtime.pins, case_ids=[case['id'] for case in catalog['cases']])
    provenance = dict(version='wildfire-incident-eval-provenance-v1',
        split='evaluation_development', independent_historical_incidents=1,
        source_context='Sierra Bermeja, Malaga, 2021; context, not historical replay',
        source_dataset=release['dataset_repo'], source_revision=release['revision'],
        dataset_manifest_sha256=manifest_sha, inputs_sha256=env.inputs['sha256'],
        network_sha256=env.network.sha256, generator_sha256=lock['generator_sha256'],
        catalog_sha256=catalog['sha256'], seeds=[case['seed'] for case in catalog['cases']],
        observed_context=['source terrain and road geography', 'retrospective weather and incident context'],
        synthetic=['initial fronts', 'pre-handover warm-up', 'weather', 'crew and resource states',
                   'population', 'calls', 'destinations and communications'],
        limitations=['Source-pack fuel estimates and land/water uncertainty remain unapproved.',
                     'Weather and its moisture/spread proxy are synthetic and uncalibrated.',
                     'Warm-up begins from an assumed front, not an observed ignition or detection time.',
                     'Open development cases are not a secret or geographically independent test set.'],
        weather_rationale=['https://research.fs.usda.gov/treesearch/47360',
                           'https://research.fs.usda.gov/treesearch/49034'])
    output = Path(args.output)
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name, value in (('catalog.json',catalog),('preflight.json',evidence),('lock.json',lock),
                        ('provenance.json',provenance)):
        path=output/name
        path.write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')
        path.chmod(0o600)
    print(json.dumps(dict(status='compiled_candidates', cases=len(catalog['cases']), sha256=catalog['sha256'])))


if __name__ == '__main__':
    main()
