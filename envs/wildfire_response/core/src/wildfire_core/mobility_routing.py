"""Deterministic metric routing over a caller-pinned, directed road snapshot."""
import copy
from functools import lru_cache
import heapq
import json
import math
from pathlib import Path

from envs.wildfire_response.regional.ops.generator import digest


NETWORK_VERSION = 'coastal-road-network-v1'
MAX_NETWORK_BYTES = 32_000_000


def point(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or any(type(number) not in (float, int) or not math.isfinite(number)
                   for number in value)):
        raise ValueError('Expected a finite metric coordinate pair')
    return list(value)


def line_length(coordinates):
    return sum(math.dist(first, second) for first, second in zip(coordinates, coordinates[1:]))


def split_line(coordinates, fraction):
    remaining = line_length(coordinates) * max(0, min(1, fraction))
    prefix = [coordinates[0][:]]
    for index, (first, second) in enumerate(zip(coordinates, coordinates[1:])):
        length = math.dist(first, second)
        if remaining <= length and length:
            ratio = remaining / length
            position = [first[axis] + ratio * (second[axis] - first[axis]) for axis in range(2)]
            return position, prefix + [position], [position, *copy.deepcopy(coordinates[index + 1:])]
        remaining -= length
        prefix.append(second[:])
    return coordinates[-1][:], prefix, [coordinates[-1][:]]


def make_leg(mode, coordinates, speed_kph, edge_id=None, destination_node=None):
    coordinates = [point(coordinate) for coordinate in coordinates]
    length = line_length(coordinates)
    if not math.isfinite(length) or not 0 < speed_kph <= 500:
        raise ValueError('Invalid segment length or speed')
    return dict(mode=mode, coordinates=coordinates, distance_m=length,
                duration_minutes=length / (speed_kph * 1000 / 60),
                edge_id=edge_id, destination_node=destination_node)


def make_journey(legs, origin, destination, basis):
    legs = copy.deepcopy([leg for leg in legs if leg['distance_m'] > 0.001])
    raw_minutes = sum(leg['duration_minutes'] for leg in legs)
    duration = max(1, math.ceil(raw_minutes))
    if legs:
        scale = duration / raw_minutes
        for leg in legs:
            leg['duration_minutes'] *= scale
    return dict(origin=point(origin), destination=point(destination), legs=legs,
                distance_m=sum(leg['distance_m'] for leg in legs),
                duration_minutes=duration, basis=basis)


def journey_position(journey, minute):
    elapsed = max(0, minute - journey['departed_at'])
    for leg in journey['legs']:
        if elapsed < leg['duration_minutes']:
            fraction = elapsed / leg['duration_minutes']
            position, prefix, suffix = split_line(leg['coordinates'], fraction)
            return position, leg, prefix, suffix
        elapsed -= leg['duration_minutes']
    return journey['destination'][:], None, [], []


class RoadNetwork:
    def __init__(self, source, expected_sha256):
        if isinstance(source, dict):
            value = copy.deepcopy(source)
            raw = json.dumps(value, allow_nan=False).encode()
        else:
            path = Path(source)
            if path.is_symlink():
                raise ValueError('Road snapshot must not be a symlink')
            with path.open('rb') as stream:
                raw = stream.read(MAX_NETWORK_BYTES + 1)
            value = json.loads(raw)
        if (len(raw) > MAX_NETWORK_BYTES or not isinstance(value, dict)
                or value.get('version') != NETWORK_VERSION or value.get('crs') != 'EPSG:25830'
                or not isinstance(expected_sha256, str) or len(expected_sha256) != 64
                or value.get('sha256') != expected_sha256
                or digest({key: item for key, item in value.items() if key != 'sha256'}) != expected_sha256):
            raise ValueError('Road contract or caller SHA256 pin mismatch')
        nodes, edges = value.get('nodes'), value.get('edges')
        if (not isinstance(nodes, list) or not 1 <= len(nodes) <= 50_000
                or not isinstance(edges, list) or not 1 <= len(edges) <= 100_000):
            raise ValueError('Road snapshot exceeds supported graph bounds')
        self.nodes, self.edges, self.adjacency = {}, {}, {}
        for node in nodes:
            identifier = node['id']
            if not isinstance(identifier, str) or not identifier or identifier in self.nodes:
                raise ValueError('Road node IDs must be unique strings')
            self.nodes[identifier] = point(node['position'])
            self.adjacency[identifier] = []
        total_coordinates = 0
        for edge in edges:
            identifier, start, end = edge['id'], edge['source'], edge['target']
            coordinates = edge['coordinates']
            if (not isinstance(identifier, str) or identifier in self.edges
                    or start not in self.nodes or end not in self.nodes
                    or not isinstance(coordinates, list) or len(coordinates) < 2):
                raise ValueError('Invalid directed edge or disconnected endpoint')
            coordinates = [point(coordinate) for coordinate in coordinates]
            total_coordinates += len(coordinates)
            length, speed = edge['length_m'], edge['speed_kph']
            if (coordinates[0] != self.nodes[start] or coordinates[-1] != self.nodes[end]
                    or type(length) not in (int, float) or not math.isfinite(length) or length <= 0
                    or abs(line_length(coordinates) - length) > max(.05, length * 1e-5)
                    or type(speed) not in (int, float) or not math.isfinite(speed) or not 0 < speed <= 100
                    or total_coordinates > 500_000):
                raise ValueError('Edge geometry, metric length or assumed speed is invalid')
            self.edges[identifier] = copy.deepcopy(edge)
            self.adjacency[start].append(identifier)
        for arcs in self.adjacency.values():
            arcs.sort()
        self.document = value
        self.sha256 = expected_sha256
        self.path = lru_cache(maxsize=256)(self._path)
        reverse = {identifier: [] for identifier in self.nodes}
        for edge in self.edges.values():
            reverse[edge['target']].append(edge['source'])
        visited, order = set(), []
        for origin in sorted(self.nodes):
            stack = [(origin, False)]
            while stack:
                node, exiting = stack.pop()
                if exiting:
                    order.append(node)
                elif node not in visited:
                    visited.add(node)
                    stack.append((node, True))
                    stack.extend((self.edges[arc]['target'], False) for arc in self.adjacency[node]
                                 if self.edges[arc]['target'] not in visited)
        visited, components = set(), []
        for origin in reversed(order):
            if origin in visited:
                continue
            component, stack = set(), [origin]
            while stack:
                node = stack.pop()
                if node in visited:
                    continue
                visited.add(node)
                component.add(node)
                stack.extend(reverse[node])
            components.append(component)
        self.main_component = max(components, key=lambda component: (len(component), min(component)))

    def nearest(self, position, *, connected=False):
        position = point(position)
        nodes = self.main_component if connected else self.nodes
        return min(nodes, key=lambda identifier: (math.dist(position, self.nodes[identifier]), identifier))

    def _path(self, origin, destination):
        if origin not in self.nodes or destination not in self.nodes:
            raise ValueError('Unknown road endpoint')
        queue, costs, parents = [(0, origin)], {origin: 0}, {}
        while queue:
            cost, node = heapq.heappop(queue)
            if cost != costs[node]:
                continue
            if node == destination:
                result = []
                while node != origin:
                    edge = parents[node]
                    result.append(edge)
                    node = self.edges[edge]['source']
                return tuple(reversed(result))
            for identifier in self.adjacency[node]:
                edge = self.edges[identifier]
                next_cost = cost + edge['length_m'] / edge['speed_kph']
                if next_cost < costs.get(edge['target'], math.inf):
                    costs[edge['target']] = next_cost
                    parents[edge['target']] = identifier
                    heapq.heappush(queue, (next_cost, edge['target']))
        raise ValueError('No directed road route between these endpoints')

    def legs(self, origin, destination):
        return [make_leg('road', self.edges[identifier]['coordinates'],
                         self.edges[identifier]['speed_kph'], identifier,
                         self.edges[identifier]['target']) for identifier in self.path(origin, destination)]

    def approach(self, origin, target, *, walk=False, max_access_m=150):
        candidates = sorted((math.dist(position, target), identifier)
                            for identifier, position in self.nodes.items()
                            if math.dist(position, target) <= max_access_m
                            and (origin not in self.main_component or identifier in self.main_component))
        for gap, destination in candidates:
            try:
                legs = self.legs(origin, destination)
                self.path(destination, origin)
            except ValueError:
                continue
            access = self.nodes[destination]
            if walk and gap > .001:
                legs.append(make_leg('walk', [access, target], 3, destination_node=destination))
            journey = make_journey(legs, self.nodes[origin], target if walk else access,
                                   'Pinned directed roads; assumed class speeds; optional 3 km/h walking leg')
            journey.update(access_node=destination, access_point=access[:], access_distance_m=gap)
            return journey
        raise ValueError('No reachable road access within the declared mission limit')

    def return_journey(self, previous, minute, base):
        position, leg, prefix, suffix = journey_position(previous, minute)
        if leg and leg['mode'] == 'walk':
            access = previous['access_node']
            legs = [make_leg('walk', list(reversed(prefix)), 3), *self.legs(access, base)]
        elif leg:
            edge = self.edges[leg['edge_id']]
            legs = [make_leg('road', suffix, edge['speed_kph'], edge['id'], edge['target']),
                    *self.legs(edge['target'], base)]
        else:
            access = previous.get('access_node', base)
            legs = []
            if math.dist(position, self.nodes[access]) > .001:
                legs.append(make_leg('walk', [position, self.nodes[access]], 3))
            legs.extend(self.legs(access, base))
        result = make_journey(legs, position, self.nodes[base],
                              'Legal forward road traversal on return; no reverse through one-way links')
        result.update(access_node=base, access_point=self.nodes[base][:], access_distance_m=0)
        return result
