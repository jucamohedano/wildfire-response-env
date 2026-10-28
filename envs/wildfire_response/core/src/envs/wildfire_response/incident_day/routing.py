"""Bounded incident routing over a pinned directed road graph and known hazards.

Grid coordinates are [column, row]; wind points toward spread in the same
east/south coordinate system. This is an exercise profile, not tactical safety
certification. Burning cells and their eight neighbours are excluded, plus one
additional cell downwind. Unknown conditions outside the supplied knowledge are
not inferred. Staging is searched within four cells of the requested target.
Optional excluded_cells are exact grid cells, without a fire buffer. Edge
allowed_modes, when present, is a list containing engine and/or crew; omitted
metadata leaves the pinned network's existing access assumption unchanged.
"""
import copy
from functools import lru_cache
import hashlib
import heapq
import json
import math

from wildfire_core.mobility_routing import make_journey, make_leg, point, split_line


MAX_CANDIDATES = 32
MAX_WALK_CANDIDATES = 12
MAX_WALK_GOALS = 12
MAX_ROAD_VISITS = 10000
MAX_STAGING_CELLS = 4
MAX_WALK_CELLS = 3
ORIGIN_TOLERANCE_M = 1e-6
PROFILE = ('Exercise only: one-cell fire buffer plus one cell downwind; '
           'assumed road speeds and 3 km/h walking adjusted for slope; '
           'unknown hazards, traffic and tactical safety are not verified')


class HazardRouter:
    def __init__(self, network, inputs, heights, *, walk_mask=None):
        self.network = network
        self.inputs = copy.deepcopy(inputs)
        self.heights = copy.deepcopy(heights)
        self.walk_mask = copy.deepcopy(walk_mask)
        resolution = self.inputs.get('provenance', {}).get('fuel_resolution') or {}
        self._estimated_land = {(cell['column'], cell['row'])
            for cell in resolution.get('document', {}).get('cells', [])
            if cell['land_kind'] in ('vegetation', 'urban_mixed')}
        grid = self.inputs['grid']
        self.width, self.height = grid['width'], grid['height']
        self.transform = grid['transform'][:]
        if (type(self.width) is not int or type(self.height) is not int
                or self.width <= 0 or self.height <= 0 or len(self.transform) != 6
                or any(type(value) not in (int, float) or not math.isfinite(value)
                       for value in self.transform)
                or self.transform[1] != 0 or self.transform[3] != 0
                or self.transform[0] <= 0 or self.transform[4] >= 0
                or grid.get('crs', 'EPSG:25830') != 'EPSG:25830'):
            raise ValueError('Expected an axis-aligned EPSG:25830 grid')
        if self.heights is None:
            raise ValueError('A height raster is required')
        for matrix in (self.heights, self.walk_mask, self.inputs.get('fuel_valid_mask'),
                       self.inputs.get('fuel_known_mask'),
                       self.inputs.get('fuel_classes'), self.inputs.get('study_mask')):
            if matrix is not None and (len(matrix) != self.height
                    or any(len(row) != self.width for row in matrix)):
                raise ValueError('Raster dimensions must match the grid')
        for edge in self.network.edges.values():
            modes = edge.get('allowed_modes')
            if 'allowed_modes' in edge and (not isinstance(modes, list)
                                            or any(mode not in ('engine', 'crew') for mode in modes)):
                raise ValueError('allowed_modes must be a list of engine and/or crew')
        self._cached_options = lru_cache(maxsize=128)(self._plan)

    def _cell(self, position):
        return (math.floor((position[0] - self.transform[2]) / self.transform[0]),
                math.floor((position[1] - self.transform[5]) / self.transform[4]))

    def _center(self, cell):
        return [self.transform[2] + (cell[0] + .5) * self.transform[0],
                self.transform[5] + (cell[1] + .5) * self.transform[4]]

    def _inside(self, cell):
        return 0 <= cell[0] < self.width and 0 <= cell[1] < self.height

    def _walkable(self, cell):
        if not self._inside(cell):
            return False
        column, row = cell
        elevation = self.heights[row][column]
        if type(elevation) not in (int, float) or not math.isfinite(elevation):
            return False
        for field in ('study_mask', 'fuel_valid_mask', 'fuel_known_mask'):
            mask = self.inputs.get(field)
            if mask is not None and mask[row][column] != 1:
                if field == 'study_mask' or (column, row) not in self._estimated_land:
                    return False
        fuel = self.inputs.get('fuel_classes')
        if fuel is not None and (type(fuel[row][column]) is not int or not 1 <= fuel[row][column] <= 10):
            return False
        if self.walk_mask is not None:
            return self.walk_mask[row][column] == 1
        return fuel is not None and (self.inputs.get('fuel_valid_mask') is not None
                                     or self.inputs.get('fuel_known_mask') is not None)

    def _hazards(self, burning, wind):
        wind = point(wind)
        cells = set()
        direction = tuple(0 if abs(value) < 1e-12 else (1 if value > 0 else -1)
                          for value in wind)
        for cell in burning:
            if (not isinstance(cell, (tuple, list)) or len(cell) != 2
                    or any(type(value) is not int for value in cell)):
                raise ValueError('Burning cells must be integer (column, row) pairs')
            for delta_x in range(-1, 2):
                for delta_y in range(-1, 2):
                    base = (cell[0] + delta_x, cell[1] + delta_y)
                    cells.add(base)
                    cells.add((base[0] + direction[0], base[1] + direction[1]))
        return frozenset(cells)

    def _safe_cell(self, cell, hazards, mode):
        return self._inside(cell) and cell not in hazards and (mode != 'crew' or self._walkable(cell))

    def _excluded(self, excluded_cells):
        cells = set()
        for cell in excluded_cells:
            if (not isinstance(cell, (tuple, list)) or len(cell) != 2
                    or any(type(value) is not int for value in cell)):
                raise ValueError('Excluded cells must be integer (column, row) pairs')
            cells.add(tuple(cell))
        return frozenset(cells)

    def _edge_allowed(self, edge, mode):
        return 'allowed_modes' not in edge or mode in edge['allowed_modes']

    def safe_position(self, position_metric, burning, wind, *, mode='engine', excluded_cells=()):
        if mode not in ('engine', 'crew'):
            raise ValueError('Mode must be engine or crew')
        hazards = self._hazards(burning, wind) | self._excluded(excluded_cells)
        return self._safe_cell(self._cell(point(position_metric)), hazards, mode)

    def _segment_cells(self, first, second):
        start = ((first[0] - self.transform[2]) / self.transform[0],
                 (first[1] - self.transform[5]) / self.transform[4])
        end = ((second[0] - self.transform[2]) / self.transform[0],
               (second[1] - self.transform[5]) / self.transform[4])
        times = {0.0, 1.0}
        for axis in range(2):
            change = end[axis] - start[axis]
            if change:
                for boundary in range(math.floor(min(start[axis], end[axis])) + 1,
                                      math.ceil(max(start[axis], end[axis]))):
                    times.add((boundary - start[axis]) / change)
        times = sorted(times)
        samples = times + [(first_time + second_time) / 2
                           for first_time, second_time in zip(times, times[1:])]
        cells = set()
        for time in samples:
            coordinate = [start[axis] + time * (end[axis] - start[axis]) for axis in range(2)]
            axes = []
            for value in coordinate:
                nearest = round(value)
                axes.append((nearest - 1, nearest) if abs(value - nearest) < 1e-10
                            else (math.floor(value),))
            cells.update((column, row) for column in axes[0] for row in axes[1])
        return cells

    def _safe_line(self, coordinates, hazards, mode):
        return (all(self._safe_cell(self._cell(position), hazards, mode) for position in coordinates)
                and all(self._safe_cell(cell, hazards, mode)
                   for first, second in zip(coordinates, coordinates[1:])
                   for cell in self._segment_cells(first, second)))

    def safe_journey_segment(self, journey, minute, burning, wind, *, excluded_cells=(), mode=None):
        hazards = self._hazards(burning, wind) | self._excluded(excluded_cells)
        mode = mode if mode is not None else journey.get('resource_mode')
        if mode is not None and mode not in ('engine', 'crew'):
            raise ValueError('Mode must be engine or crew')
        elapsed = max(0, minute - journey.get('departed_at', 0))
        remaining = 1.0
        for leg in journey['legs']:
            duration = leg['duration_minutes']
            if elapsed >= duration:
                elapsed -= duration
                continue
            if (leg['mode'] == 'road' and mode is not None
                    and (leg.get('edge_id') not in self.network.edges
                         or not self._edge_allowed(self.network.edges[leg['edge_id']], mode))):
                return False
            start_fraction = elapsed / duration
            end_fraction = min(1, (elapsed + remaining) / duration)
            _, _, suffix = split_line(leg['coordinates'], start_fraction)
            _, segment, _ = split_line(suffix, (end_fraction - start_fraction) / (1 - start_fraction))
            if not self._safe_line(segment, hazards, 'crew' if leg['mode'] == 'walk' else 'engine'):
                return False
            remaining -= min(remaining, duration - elapsed)
            elapsed = 0
            if remaining <= 0:
                return True
        return self._safe_cell(self._cell(point(journey['destination'])), hazards,
                               'crew' if journey['legs'] and journey['legs'][-1]['mode'] == 'walk' else 'engine')

    def _origins(self, origin, mode=None):
        nodes = sorted(identifier for identifier, position in self.network.nodes.items()
                       if math.dist(origin, position) <= ORIGIN_TOLERANCE_M)
        if nodes:
            return [(nodes[0], [])] if len(nodes) == 1 else []
        matches = []
        for identifier, edge in self.network.edges.items():
            if mode is not None and not self._edge_allowed(edge, mode):
                continue
            coordinates = edge['coordinates']
            for index, (first, second) in enumerate(zip(coordinates, coordinates[1:])):
                vector = [second[axis] - first[axis] for axis in range(2)]
                squared = sum(value * value for value in vector)
                if not squared:
                    continue
                fraction = max(0, min(1, sum((origin[axis] - first[axis]) * vector[axis]
                                              for axis in range(2)) / squared))
                projection = [first[axis] + fraction * vector[axis] for axis in range(2)]
                gap = math.dist(origin, projection)
                if gap <= ORIGIN_TOLERANCE_M:
                    suffix = [origin[:], *copy.deepcopy(coordinates[index + 1:])]
                    matches.append((gap, identifier, edge, suffix))
        if not matches:
            return []
        gap, identifier, edge, suffix = min(matches, key=lambda match: (match[0], match[1]))
        geometry = edge['coordinates']
        equivalents = [match for match in matches
                       if match[2]['coordinates'] == geometry
                       or match[2]['coordinates'] == list(reversed(geometry))]
        return [(item['target'], [make_leg('road', remainder, item['speed_kph'], name, item['target'])])
                for _, name, item, remainder in equivalents]

    def _roads(self, origins, hazards, targets, mode):
        queue, costs, paths = [], {}, {}
        for node, legs in origins:
            if any(not self._safe_line(leg['coordinates'], hazards,
                                       'crew' if leg['mode'] == 'walk' else 'engine') for leg in legs):
                continue
            cost = sum(leg['duration_minutes'] for leg in legs)
            if cost < costs.get(node, math.inf):
                costs[node], paths[node] = cost, legs
                heapq.heappush(queue, (cost, node))
        settled = set()
        while queue and len(settled) < MAX_ROAD_VISITS:
            cost, node = heapq.heappop(queue)
            if cost != costs[node] or node in settled:
                continue
            settled.add(node)
            if targets <= settled:
                break
            for identifier in self.network.adjacency[node]:
                edge = self.network.edges[identifier]
                if not self._edge_allowed(edge, mode) or not self._safe_line(edge['coordinates'], hazards, 'engine'):
                    continue
                next_cost = cost + edge['length_m'] / (edge['speed_kph'] * 1000 / 60)
                if next_cost < costs.get(edge['target'], math.inf):
                    costs[edge['target']] = next_cost
                    paths[edge['target']] = paths[node] + [make_leg(
                        'road', edge['coordinates'], edge['speed_kph'], identifier, edge['target'])]
                    heapq.heappush(queue, (next_cost, edge['target']))
        return {node: paths[node] for node in settled}, not queue or targets <= settled

    def _graph_reachable(self, origins, targets, mode=None):
        pending = [node for node, _ in origins]
        visited = set()
        while pending and len(visited) < MAX_ROAD_VISITS:
            node = pending.pop()
            if node in visited:
                continue
            visited.add(node)
            if node in targets:
                return True, True
            pending.extend(self.network.edges[edge]['target'] for edge in self.network.adjacency[node]
                           if mode is None or self._edge_allowed(self.network.edges[edge], mode))
        return False, not pending

    def _walk(self, access, goal, hazards, destination=None):
        start = self._cell(access)
        if not self._safe_cell(start, hazards, 'crew') or not self._safe_cell(goal, hazards, 'crew'):
            return None
        queue, costs, parents = [(0, 0, start)], {start: 0}, {}
        while queue:
            _, cost, cell = heapq.heappop(queue)
            if cost != costs[cell]:
                continue
            if cell == goal:
                route = [cell]
                while cell != start:
                    cell = parents[cell]
                    route.append(cell)
                route.reverse()
                coordinates = [access[:]] + [self._center(item) for item in route]
                if destination is not None:
                    coordinates.append(destination[:])
                if not self._safe_line(coordinates, hazards, 'crew'):
                    return None
                legs = []
                cells = [start] + route + ([goal] if destination is not None else [])
                for first, second, left, right in zip(coordinates, coordinates[1:], cells, cells[1:]):
                    distance = math.dist(first, second)
                    if distance <= .001:
                        continue
                    grade = abs(self.heights[right[1]][right[0]] - self.heights[left[1]][left[0]]) / distance
                    legs.append(make_leg('walk', [first, second], 3 / (1 + 5 * grade)))
                return legs
            for delta_x, delta_y in ((0, -1), (-1, 0), (1, 0), (0, 1)):
                neighbour = (cell[0] + delta_x, cell[1] + delta_y)
                if (not self._safe_cell(neighbour, hazards, 'crew')
                        or abs(neighbour[0] - start[0]) + abs(neighbour[1] - start[1]) > MAX_WALK_CELLS):
                    continue
                distance = math.dist(self._center(cell), self._center(neighbour))
                rise = abs(self.heights[cell[1]][cell[0]] - self.heights[neighbour[1]][neighbour[0]])
                next_cost = cost + distance + 5 * rise
                if next_cost < costs.get(neighbour, math.inf):
                    costs[neighbour], parents[neighbour] = next_cost, cell
                    heuristic = math.dist(self._center(neighbour), self._center(goal))
                    heapq.heappush(queue, (next_cost + heuristic, next_cost, neighbour))
        return None

    def _walking_origins(self, origin, hazards):
        start = self._cell(origin)
        nearby = sorted((math.dist(origin, position), node)
                        for node, position in self.network.nodes.items()
                        if self._inside(self._cell(position))
                        and sum(abs(a - b) for a, b in zip(start, self._cell(position))) <= MAX_WALK_CELLS)
        positions = {}
        for _, node in nearby:
            position = tuple(self.network.nodes[node])
            positions[position] = positions.get(position, 0) + 1
        result = []
        for _, node in nearby[:MAX_WALK_CANDIDATES]:
            position = self.network.nodes[node]
            if positions[tuple(position)] != 1:
                continue
            legs = self._walk(origin, self._cell(position), hazards, destination=position)
            if legs is not None:
                result.append((node, legs))
        return result

    def options(self, origin_metric, target_grid, *, mode='engine', burning, wind, revision=0, limit=3,
                excluded_cells=(), target_metric=None):
        origin = point(origin_metric)
        if (mode not in ('engine', 'crew') or not isinstance(target_grid, (tuple, list))
                or len(target_grid) != 2 or any(type(value) is not int for value in target_grid)
                or type(revision) is not int or type(limit) is not int or not 1 <= limit <= 32):
            raise ValueError('Invalid mode, target, revision or option limit')
        wind = point(wind)
        if target_metric is not None:
            target_metric = point(target_metric)
            if self._cell(target_metric) != tuple(target_grid):
                raise ValueError('Exact target must lie in the target grid cell')
        excluded = self._excluded(excluded_cells)
        hazards = self._hazards(burning, wind) | excluded
        key_burning = tuple(sorted(tuple(cell) for cell in burning))
        return copy.deepcopy(self._cached_options(tuple(origin), tuple(target_grid), mode,
            key_burning, tuple(wind), revision, limit, hazards, tuple(sorted(excluded)),
            tuple(target_metric) if target_metric is not None else None))

    def _plan(self, origin, target, mode, burning, wind, revision, limit, hazards, excluded, target_metric):
        if not self._inside(target):
            return dict(status='unmapped', options=[])
        if not self._inside(self._cell(origin)):
            return dict(status='unmapped', options=[])
        if self._cell(origin) in hazards or (target_metric is not None and target in hazards):
            return dict(status='blocked', options=[])
        if target_metric is not None and mode == 'crew' and not self._walkable(target):
            return dict(status='unmapped', options=[])
        origins = self._origins(list(origin), mode)
        unrestricted_origins = self._origins(list(origin))
        if not origins and mode == 'crew' and not unrestricted_origins:
            origins = self._walking_origins(list(origin), hazards)
            if not origins and self._walking_origins(list(origin), frozenset()):
                return dict(status='blocked', options=[])
        if not origins:
            return dict(status='blocked' if unrestricted_origins else 'unmapped', options=[])
        target_point = self._center(target)
        cell_size = max(self.transform[0], -self.transform[4])
        candidates = sorted((math.dist(position, target_point), identifier)
                            for identifier, position in self.network.nodes.items()
                            if math.dist(position, target_point) <= MAX_STAGING_CELLS * cell_size
                            and self._inside(self._cell(position)))
        if not candidates:
            return dict(status='unmapped', options=[])
        candidates = [(distance, node) for distance, node in candidates
                      if self._safe_cell(self._cell(self.network.nodes[node]), hazards, 'engine')]
        if not candidates:
            return dict(status='blocked', options=[])
        if mode == 'crew':
            candidates = [(distance, node) for distance, node in candidates
                          if self._walkable(self._cell(self.network.nodes[node]))]
            if not candidates:
                return dict(status='unmapped', options=[])
        truncated = len(candidates) > (MAX_WALK_CANDIDATES if mode == 'crew' else MAX_CANDIDATES)
        candidates = candidates[:MAX_WALK_CANDIDATES if mode == 'crew' else MAX_CANDIDATES]
        targets = {node for _, node in candidates}
        paths, complete = self._roads(origins, hazards, targets, mode)
        safe_goals = sorted((math.dist((column, row), target), column, row)
                            for row in range(max(0, target[1] - MAX_STAGING_CELLS),
                                             min(self.height, target[1] + MAX_STAGING_CELLS + 1))
                            for column in range(max(0, target[0] - MAX_STAGING_CELLS),
                                                min(self.width, target[0] + MAX_STAGING_CELLS + 1))
                            if math.dist((column, row), target) <= MAX_STAGING_CELLS
                            and self._safe_cell((column, row), hazards, 'crew'))[:MAX_WALK_GOALS]
        options = []
        for _, node in candidates:
            if node not in paths or not self._safe_cell(self._cell(self.network.nodes[node]), hazards, 'engine'):
                continue
            access = self.network.nodes[node]
            if mode == 'engine':
                destinations = ([(self._cell(access), access[:], [])]
                                if target_metric is None or access == list(target_metric) else [])
            else:
                destinations = []
                goals = [(0, *target)] if target_metric is not None else safe_goals
                for _, column, row in goals:
                    goal = (column, row)
                    legs = self._walk(access, goal, hazards,
                                      destination=list(target_metric) if target_metric is not None else None)
                    if legs is not None:
                        destinations.append((goal, list(target_metric) if target_metric is not None
                                             else self._center(goal), legs))
                        break
                if (target_metric is None and self._cell(access) == target
                        and math.dist(access, self._center(target)) > .001):
                    destinations.append((target, access[:], []))
            for cell, destination, walk_legs in destinations:
                journey = make_journey(paths[node] + walk_legs, list(origin), destination, PROFILE)
                for leg in journey['legs']:
                    leg['speed_kph'] = leg['distance_m'] / leg['duration_minutes'] * .06
                journey.update(access_node=node, access_point=access[:],
                               access_distance_m=math.dist(access, destination), departed_at=0, resource_mode=mode)
                identity = dict(network=self.network.sha256, origin=origin, target=target, mode=mode,
                                burning=burning, wind=wind, excluded=excluded, revision=revision, node=node,
                                cell=cell, destination=destination,
                                edges=[leg['edge_id'] for leg in journey['legs']])
                identifier = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:20]
                options.append(dict(id=identifier, approach_grid=list(cell), approach_point=destination[:],
                                    journey=journey, distance_m=journey['distance_m'],
                                    travel_minutes=journey['duration_minutes'], knowledge_revision=revision,
                                    uncertainty=PROFILE))
        options.sort(key=lambda option: (math.dist(option['approach_grid'], target),
                                         option['travel_minutes'], option['id']))
        if options:
            return dict(status='reachable', options=options[:limit])
        unrestricted, unrestricted_complete = self._roads(origins, frozenset(), targets, mode)
        graph_reachable = any(node in unrestricted for _, node in candidates)
        if graph_reachable and (not paths or any(node in unrestricted and node not in paths for _, node in candidates)
                                or any(self._cell(self.network.nodes[node]) in hazards for _, node in candidates)):
            return dict(status='blocked', options=[])
        if truncated or not complete or not unrestricted_complete:
            return dict(status='unmapped', options=[])
        connected, graph_complete = self._graph_reachable(origins, targets, mode)
        if connected or not graph_complete:
            return dict(status='unmapped', options=[])
        connected_any, any_complete = self._graph_reachable(origins, targets)
        if connected_any:
            return dict(status='blocked', options=[])
        if not any_complete:
            return dict(status='unmapped', options=[])
        return dict(status='unreachable', options=[])
