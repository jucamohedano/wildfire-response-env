"""Six reproducible operational-day development exercises, not historical replays."""
import copy

from ..regional.ops.generator import digest
from ..regional.data import load_pack
from ..regional.fire import RegionalFire
from ..regional.prepared.data import FUEL_MAPPING
from ..regional.topographic.fire import EdgeFireStep


VERSION = 'wildfire-incident-day-scenarios-v2'
CASE_IDS = tuple(f'incident-day-dev-{index:03}' for index in range(6))
FAMILIES = ('quiet_handover', 'wind_access_loss', 'crew_only_access',
            'aircraft_window', 'destination_threat', 'regional_shortage')
PROFILE = dict(version='wildfire-incident-day-profile-v2', horizon_minutes=1440,
    work_minutes=240, duty_minutes=240, rest_minutes=60, ground_setup_minutes=5, aircraft_setup_minutes=2,
    ground_refill_minutes=4, aircraft_refill_minutes=6, flight_speed_kph=240,
    air_refill_minutes=6, aircraft_max_wind=2.8, engine_litres_per_minute=500,
    spread_interval_multiplier=60, burn_duration_multiplier=12,
    evidence='Declared exercise assumptions, not aviation/labour-law or fire-spread calibration')
CALIBRATED_CASE_IDS = tuple(f'incident-day-shift-v1-{index:03}' for index in range(6))
EGRESS_CASE_IDS = tuple(f'incident-day-egress-v1-{index:03}' for index in range(6))
ALL_CASE_IDS = CASE_IDS + CALIBRATED_CASE_IDS + EGRESS_CASE_IDS


class DayFire(RegionalFire, EdgeFireStep):
    def __init__(self, inputs, seed):
        super().__init__(load_pack(), seed)
        self.width, self.height = inputs['grid']['width'], inputs['grid']['height']
        self.cells = []
        resolution = inputs['provenance'].get('fuel_resolution') or {}
        estimates = {(cell['row'], cell['column']): cell for cell in resolution.get('document', {}).get('cells', [])}
        for row_index, row in enumerate(inputs['fuel_classes']):
            cells = []
            for column_index, code in enumerate(row):
                mapped = FUEL_MAPPING.get(code)
                if mapped is None:
                    raise ValueError('Day physics requires resolved fuel; unknown is not a firebreak')
                fuel = mapped['initial_fuel']
                estimate = estimates.get((row_index, column_index))
                terrain = mapped['terrain']
                if estimate is not None:
                    terrain = estimate['runtime_terrain']
                    fuel *= estimate['fuel_multiplier']
                cells.append(dict(terrain=terrain, fuel=fuel, initial_fuel=fuel,
                                  heat=0., age=0, wet=0, burned=0.))
            self.cells.append(cells)
        self.groups = {}
        self.heights = inputs['heights']
        self.slope_enabled = True
        self.boundary_contact_at = None
        self.spread_multiplier = PROFILE['spread_interval_multiplier']
        self.rules['burn_duration_multiplier'] = PROFILE['burn_duration_multiplier']


def native(inputs, point):
    transform = inputs['grid']['transform']
    return [transform[2] + (point[0] + .5) * transform[0],
            transform[5] + (point[1] + .5) * transform[4]]


def grid_point(inputs, position):
    transform = inputs['grid']['transform']
    return [int((position[0] - transform[2]) // transform[0]),
            int((position[1] - transform[5]) // transform[4])]


def build_scenario(inputs, network, identifier, seed=None):
    if identifier in EGRESS_CASE_IDS:
        from .calibrated_scenarios import build_egress_scenario
        return build_egress_scenario(inputs, network, identifier, seed)
    if identifier in CALIBRATED_CASE_IDS:
        from .calibrated_scenarios import build_calibrated_scenario
        return build_calibrated_scenario(inputs, network, identifier, seed)
    if identifier not in CASE_IDS:
        raise ValueError('Unknown incident-day development scenario')
    index = CASE_IDS.index(identifier)
    fixed_seed = 2844000 + index
    if seed is not None and seed != fixed_seed:
        raise ValueError('Development scenario seeds are pinned')
    width, height = inputs['grid']['width'], inputs['grid']['height']
    if width < 24 or height < 24:
        raise ValueError('Large-fire day needs a supported domain at least 24 cells wide/high')
    center = [width // 2, height // 2]
    initial = [[center[0] + offset_x, center[1] + offset_y]
               for offset_y in range(-2, 3) for offset_x in range(-2, 3)]
    bases = [network.nearest(native(inputs, [4, height - 5]), connected=True),
             network.nearest(native(inputs, [width - 5, height - 5]), connected=True)]
    resources = []
    for base_index, base in enumerate(bases):
        for kind in ('engine', 'crew'):
            resources.append(dict(id=f'{kind}-{base_index}', kind=kind, home_base=f'center-{base_index}',
                position=network.nodes[base][:], base_position=network.nodes[base][:],
                capacity_litres=3500 if kind == 'engine' else 0,
                water_litres=2500 if kind == 'engine' else 0, state='available',
                available_at=0, work_remaining=180 if base_index == 0 else 240, rest_minutes=60))
    staging = network.nearest(native(inputs, [center[0] - 6, center[1]]), connected=True)
    resources[0].update(state='on_scene', position=network.nodes[staging][:],
                        sector_id='incident', target_grid=center[:], due_at=0)
    airfield = [366040.059, 4059866.949]
    resources.append(dict(id='plane-0', kind='aircraft', home_base='regional', position=airfield[:],
        base_position=airfield[:], capacity_litres=3100, water_litres=3100, state='available',
        available_at=0, work_remaining=240, rest_minutes=60))
    resources.append(dict(id='relief-crew-0', kind='crew', home_base='regional',
        position=network.nodes[bases[1]][:], base_position=network.nodes[bases[1]][:],
        capacity_litres=0, water_litres=0, state='unavailable', available_at=480,
        work_remaining=240, rest_minutes=60))
    for resource in resources:
        resource.update(duty_end_at=1440 if resource['id'].startswith('relief') else 1320,
                        total_work_remaining=480 if resource['id'].startswith('relief') else 720,
                        facility_id='landload-base' if resource['kind'] == 'aircraft' else resource['home_base'])
        if resource['kind'] == 'aircraft':
            resource.update(fuel_minutes=180, fuel_capacity_minutes=180)
    facilities = {f'center-{index}': dict(position=network.nodes[base][:],
        allowed_kinds=['engine', 'crew'], water_litres=100000) for index, base in enumerate(bases)}
    facilities['landload-base'] = dict(position=airfield[:], allowed_kinds=['aircraft'], water_litres=100000, refuel=True)
    facilities['regional'] = dict(position=network.nodes[bases[1]][:], allowed_kinds=['crew'], water_litres=0)
    homes = [[5, 5], [width - 6, 5]]
    if index in (1, 4):
        homes[1] = [center[0] + 6, center[1]]
    groups = [dict(id=f'population-{group_index}', count=count,
        home=network.nodes[network.nearest(native(inputs, home), connected=True)][:],
        position=network.nodes[network.nearest(native(inputs, home), connected=True)][:],
        status='unwarned', exposed=0, destination_id=None)
        for group_index, (home, count) in enumerate(zip(homes, (200, 150)))]
    if index == 4:
        threatened_home = native(inputs, [center[0] + 5, center[1]])
        groups[1].update(home=threatened_home[:], position=threatened_home[:])
    destinations = [dict(id=f'destination-{base_index}', name=('West reception centre', 'East reception centre')[base_index],
        position=network.nodes[base][:], capacity=400, status='open') for base_index, base in enumerate(bases)]
    events = [dict(minute=240, kind='wind', wind=[0., 1.]),
              dict(minute=600, kind='wind', wind=[-1., 0.]),
              dict(minute=1020, kind='wind', wind=[0., -1.])]
    if index == 1:
        events.append(dict(minute=180, kind='wind', wind=[0., -2.]))
        north_staging = network.nearest(native(inputs, [center[0], center[1] - 4]), connected=True)
        resources[1].update(state='on_scene', position=network.nodes[north_staging][:],
                            sector_id='incident', target_grid=center[:], due_at=0, work_remaining=240)
    if index == 2:
        events.append(dict(minute=90, kind='access_restriction', resource_kind='engine'))
    if index == 3:
        events.append(dict(minute=300, kind='aircraft_unavailable', until=480))
    if index == 4:
        events.append(dict(minute=480, kind='destination_closed', destination_id='destination-0'))
    if index == 5:
        resources[2]['available_at'] = 360
        resources[2]['state'] = 'unavailable'
        events.append(dict(minute=720, kind='center_unavailable', center_id='center-1', until=840))
    scenario = dict(version=VERSION, id=identifier, seed=fixed_seed, family=FAMILIES[index],
        start_utc='2021-09-09T04:00:00Z', timezone='Europe/Madrid', horizon_minutes=1440,
        initial_fire=initial, warmup_minutes=60, wind=[1., 0.],
        resources=resources, groups=groups, destinations=destinations,
        centers=['regional', 'center-0', 'center-1'], events=sorted(events, key=lambda item: item['minute']),
        profile=dict(copy.deepcopy(PROFILE), facilities=facilities), synthetic=True,
        historical_replay=False, prehistory_role='Synthetic aged-fire handover, never expert action labels')
    scenario['sha256'] = digest(scenario)
    return scenario


def fixture_inputs(size=32):
    if not 24 <= size <= 64:
        raise ValueError('Fixture grid must be bounded')
    transform = [500, 0, 297000, 0, -500, 4054500]
    value = dict(version='incident-day-test-inputs-v2', grid=dict(width=size, height=size,
        transform=transform, cell_km=.5, crs='EPSG:25830'),
        heights=[[300. + column * 2 + row for column in range(size)] for row in range(size)],
        fuel_classes=[[4] * size for row in range(size)], study_mask=[[1] * size for row in range(size)],
        walk_mask=[[1] * size for row in range(size)], fuel_known_mask=[[1] * size for row in range(size)],
        approval=dict(approved=True, scope='synthetic_test_fixture_only', real_data_approved=False),
        provenance=dict(role='Synthetic topology/physics test fixture, not real geographic evidence'))
    nodes, edges = [], []
    for row in range(2, size - 2, 2):
        for column in range(2, size - 2, 2):
            identifier = f'{column}:{row}'
            nodes.append(dict(id=identifier, position=native(value, [column, row])))
            for next_column, next_row in ((column + 2, row), (column, row + 2)):
                if next_column >= size - 2 or next_row >= size - 2:
                    continue
                for origin, target in (([column, row], [next_column, next_row]), ([next_column, next_row], [column, row])):
                    edges.append(dict(id=f'{origin[0]}:{origin[1]}-{target[0]}:{target[1]}',
                        source=f'{origin[0]}:{origin[1]}', target=f'{target[0]}:{target[1]}',
                        coordinates=[native(value, origin), native(value, target)],
                        length_m=1000., speed_kph=30., road_class='fixture'))
    network = dict(version='coastal-road-network-v1', crs='EPSG:25830', nodes=nodes, edges=edges,
                   source=dict(name='Synthetic integration topology', license='test fixture', url=''))
    network['sha256'] = digest(network)
    from .data import prepare_inputs
    prepared = dict(role='synthetic_fixture', grid=copy.deepcopy(value['grid']),
        fuel_classes=value['fuel_classes'], fuel_valid_mask=value['fuel_known_mask'],
        study_mask=value['study_mask'], provenance=value['provenance'])
    prepared['sha256'] = digest(prepared)
    domain = dict(role='synthetic_fixture', prepared_inputs_sha256=prepared['sha256'],
        grid=copy.deepcopy(value['grid']), crop=dict(left=0, top=0, width=size, height=size),
        elevations_metres=value['heights'], height_support_mask=value['study_mask'],
        provenance=value['provenance'])
    domain['sha256'] = digest(domain)
    return prepare_inputs(prepared, domain, network), network
