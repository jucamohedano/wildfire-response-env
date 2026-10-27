"""Focused offline checks for the real coastal road fixture and CPU preparation."""

from collections import defaultdict
from contextlib import redirect_stdout
import gzip
import io
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from eval.prepare_coastal_roads import (
    ACTIVE_GRID, DATA, SPEEDS, build_network, canonical, clip_segment, digest,
    directions, nearest_on_line, network_digest, prepare, shortest_distances,
    simplify_line, way_reason,
)


def fixture(nodes, ways):
    return dict(bbox_wgs84=[-5.24, 36.49, -5.20, 36.53], elements=[
        dict(type='node', id=node_id, lon=position[0], lat=position[1], tags=tags)
        for node_id, position, tags in nodes
    ] + [dict(type='way', id=way_id, nodes=node_ids, tags=tags)
         for way_id, node_ids, tags in ways])


class CoastalPreparationTests(unittest.TestCase):
    def test_grade_separated_geometric_crossing_is_not_connected(self):
        snapshot = fixture([
            (1, [-5.23, 36.51], {}), (2, [-5.21, 36.51], {}),
            (3, [-5.22, 36.50], {}), (4, [-5.22, 36.52], {}),
        ], [(10, [1, 2], dict(highway='tertiary', bridge='yes', layer='1')),
            (20, [3, 4], dict(highway='tertiary'))])
        graph, _ = build_network(snapshot)
        self.assertEqual(len(graph['nodes']), 4)
        self.assertEqual(set(shortest_distances(graph, 'osm:n1')), {'osm:n1', 'osm:n2'})
        graph, statistics = build_network(snapshot, connected_only=True)
        self.assertEqual(len(graph['nodes']), 2)
        self.assertEqual(statistics['component_filter']['excluded_disconnected_components'], 1)

    def test_shared_osm_node_makes_a_real_junction(self):
        snapshot = fixture([
            (1, [-5.23, 36.51], {}), (2, [-5.22, 36.51], {}),
            (3, [-5.21, 36.51], {}), (4, [-5.22, 36.52], {}),
        ], [(10, [1, 2, 3], dict(highway='tertiary')),
            (20, [2, 4], dict(highway='secondary'))])
        graph, _ = build_network(snapshot)
        self.assertEqual(len(graph['edges']), 6)
        self.assertEqual(len(shortest_distances(graph, 'osm:n1')), 4)

    def test_oneway_and_implicit_directions(self):
        cases = [({'oneway': 'yes'}, ('f',)), ({'oneway': '-1'}, ('r',)),
                 ({'junction': 'roundabout'}, ('f',)), ({'highway': 'motorway'}, ('f',)),
                 ({'highway': 'motorway_link'}, ('f', 'r')),
                 ({'oneway': 'yes', 'oneway:motorcar': 'no'}, ('f', 'r')),
                 ({'highway': 'motorway', 'oneway': 'no'}, ('f', 'r'))]
        for tags, expected in cases:
            with self.subTest(tags=tags):
                self.assertEqual(directions(tags), expected)
        for value in ('reversible', 'alternating', 'unknown'):
            with self.assertRaises(ValueError):
                directions(dict(oneway=value))
        snapshot = fixture([(1, [-5.23, 36.51], {}), (2, [-5.21, 36.51], {})],
                           [(10, [1, 2], dict(highway='secondary', oneway='-1'))])
        graph, _ = build_network(snapshot)
        self.assertEqual(len(graph['edges']), 1)
        self.assertEqual(graph['edges'][0]['source'], 'osm:n2')
        self.assertEqual(set(shortest_distances(graph, 'osm:n1')), {'osm:n1'})

    def test_inaccessible_and_ambiguous_ways_are_excluded(self):
        rejected = [dict(highway='path', motor_vehicle='yes'),
                    dict(highway='track'), dict(highway='track', tracktype='grade3'),
                    dict(highway='track', surface='sand'), dict(highway='residential', access='private'),
                    dict(highway='service', service='parking_aisle'),
                    dict(highway='secondary', hgv='no'), dict(highway='secondary', ford='yes'),
                    dict(highway='secondary', maxweight='3.5'),
                    dict(highway='secondary', **{'motor_vehicle:conditional': 'no @ (wet)'}),
                    dict(highway='secondary', oneway='reversible')]
        for tags in rejected:
            with self.subTest(tags=tags):
                self.assertIsNotNone(way_reason(tags))
        for tags in [dict(highway='track', tracktype='grade2'),
                     dict(highway='track', surface='compacted'),
                     dict(highway='residential', access='private', motor_vehicle='yes')]:
            self.assertIsNone(way_reason(tags))

    def test_gate_is_not_silently_given_an_emergency_override(self):
        snapshot = fixture([
            (1, [-5.23, 36.51], {}), (2, [-5.22, 36.51], dict(barrier='gate')),
            (3, [-5.21, 36.51], {}),
        ], [(10, [1, 2, 3], dict(highway='secondary'))])
        graph, statistics = build_network(snapshot)
        self.assertEqual(graph['edges'], [])
        self.assertEqual(statistics['blocked_segments'], 2)
        snapshot['elements'][1]['tags']['motor_vehicle'] = 'yes'
        graph, _ = build_network(snapshot)
        self.assertIn('osm:n3', shortest_distances(graph, 'osm:n1'))

    def test_clip_handles_both_endpoints_outside_without_crossing_joins(self):
        self.assertEqual(clip_segment([-2, 0], [2, 0], [-1, -1, 1, 1]), (0.25, 0.75))
        self.assertIsNone(clip_segment([-2, 2], [2, 2], [-1, -1, 1, 1]))
        snapshot = fixture([(1, [-5.25, 36.51], {}), (2, [-5.19, 36.51], {})],
                           [(10, [1, 2], dict(highway='secondary'))])
        graph, _ = build_network(snapshot)
        self.assertTrue(all(node['id'].startswith('clip:') for node in graph['nodes']))
        self.assertEqual(graph['edges'][0]['coordinates'], list(reversed(graph['edges'][1]['coordinates'])))

    def test_missing_geometry_fails_and_zero_length_geometry_is_dropped(self):
        snapshot = fixture([(1, [-5.23, 36.51], {}), (2, [-5.23, 36.51], {})],
                           [(10, [1, 2], dict(highway='secondary'))])
        self.assertEqual(build_network(snapshot)[0]['edges'], [])
        snapshot['elements'][-1]['nodes'] = [1, 99]
        with self.assertRaises(ValueError):
            build_network(snapshot)

    def test_degree_two_compaction_preserves_oneway_reachability(self):
        snapshot = fixture([(1, [-5.40, 36.51], {}), (2, [-5.39, 36.51], {}),
                            (3, [-5.38, 36.51], {})],
                           [(10, [1, 2], dict(highway='secondary', oneway='yes')),
                            (20, [2, 3], dict(highway='secondary', oneway='yes'))])
        snapshot['bbox_wgs84'] = [-5.42, 36.49, -5.35, 36.53]
        graph, stats = build_network(snapshot)
        self.assertEqual(stats['compaction']['contracted_degree_two_nodes'], 1)
        self.assertEqual(len(graph['edges']), 1)
        self.assertIn('osm:n3', shortest_distances(graph, 'osm:n1'))
        self.assertNotIn('osm:n1', shortest_distances(graph, 'osm:n3'))
        snapshot['elements'].reverse()
        self.assertEqual(canonical(graph), canonical(build_network(snapshot)[0]))

    def test_original_source_vertex_near_interior_cell_is_kept_for_missions(self):
        from pyproj import Transformer

        inverse = Transformer.from_crs(25830, 4326, always_xy=True)
        points = [[301100, 4046600], [301170.8, 4046669.9], [301000, 4046900]]
        snapshot = fixture([(index + 1, inverse.transform(*point), {}) for index, point in enumerate(points)],
                           [(10, [1, 2, 3], dict(highway='tertiary'))])
        snapshot['bbox_wgs84'] = [-5.3, 36.50, -5.15, 36.60]
        graph, _ = build_network(snapshot)
        positions = {node['id']: node['position'] for node in graph['nodes']}
        self.assertIn('osm:n2', positions)
        self.assertLess(math.dist(positions['osm:n2'], [301250, 4046750]), 150)
        self.assertIn('osm:n2', shortest_distances(graph, 'osm:n1'))

    def test_geometry_simplification_keeps_endpoints_and_bounded_deviation(self):
        coordinates = [[0, 0], [5, 1], [10, 0], [15, 8], [20, 0]]
        simplified = simplify_line(coordinates)
        self.assertEqual(simplified[0], coordinates[0])
        self.assertEqual(simplified[-1], coordinates[-1])
        self.assertLess(len(simplified), len(coordinates))
        for point in coordinates:
            self.assertLessEqual(nearest_on_line(point, simplified)[0], 2)


class PinnedCoastalFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = json.loads((DATA / 'network.json').read_bytes())
        cls.provenance = json.loads((DATA / 'provenance.json').read_bytes())

    def test_graph_contract_hash_lengths_and_reverse_arcs(self):
        graph = self.graph
        self.assertEqual(graph['version'], 'coastal-road-network-v1')
        self.assertEqual(graph['crs'], 'EPSG:25830')
        self.assertEqual(graph['sha256'], network_digest(graph))
        self.assertEqual(graph['sha256'], '12782cb4ccb4bbfd2d59eaaa7380ad6b1a807320cf5a15b6d01e44d7ec89fc16')
        default_encoded = json.dumps({key: value for key, value in graph.items() if key != 'sha256'},
                                     sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        self.assertEqual(graph['sha256'], digest(default_encoded))
        self.assertEqual(graph['sha256'], self.provenance['outputs']['network']['sha256'])
        positions = {node['id']: node['position'] for node in graph['nodes']}
        self.assertEqual(list(positions), sorted(positions))
        self.assertEqual(len(positions), len(graph['nodes']))
        edge_ids = [edge['id'] for edge in graph['edges']]
        self.assertEqual(edge_ids, sorted(set(edge_ids)))
        pairs = defaultdict(dict)
        for edge in graph['edges']:
            self.assertEqual(set(edge), {'id', 'source', 'target', 'coordinates', 'length_m', 'speed_kph', 'road_class'})
            self.assertEqual(edge['coordinates'][0], positions[edge['source']])
            self.assertEqual(edge['coordinates'][-1], positions[edge['target']])
            self.assertGreater(edge['length_m'], 0)
            self.assertEqual(edge['speed_kph'], SPEEDS[edge['road_class']])
            self.assertTrue(all(math.isfinite(value) for point in edge['coordinates'] for value in point))
            length = sum(math.dist(start, end) for start, end in zip(edge['coordinates'], edge['coordinates'][1:]))
            self.assertAlmostEqual(edge['length_m'], length, delta=0.000501)
            identity, direction = edge['id'].rsplit(':', 1)
            pairs[identity][direction] = edge
        self.assertTrue(any(len(pair) == 1 for pair in pairs.values()))
        for pair in pairs.values():
            if len(pair) == 2:
                self.assertEqual(pair['f']['coordinates'], list(reversed(pair['r']['coordinates'])))
                self.assertEqual(pair['f']['source'], pair['r']['target'])
                self.assertEqual(pair['f']['length_m'], pair['r']['length_m'])

    def test_source_and_script_hashes_and_explicit_odbl(self):
        acquisition = self.provenance['acquisition']
        snapshot = (DATA / acquisition['snapshot']['path']).read_bytes()
        self.assertEqual(digest(snapshot), acquisition['snapshot']['sha256'])
        self.assertEqual(digest(canonical(json.loads(gzip.decompress(snapshot)))), acquisition['snapshot']['canonical_sha256'])
        script = Path(__file__).resolve().parents[1] / 'eval/prepare_coastal_roads.py'
        self.assertEqual(digest(script.read_bytes()), self.provenance['preparation']['script_sha256'])
        self.assertEqual(self.graph['source']['license'], 'ODbL-1.0')
        self.assertIn('OpenStreetMap contributors', self.graph['source']['attribution'])
        self.assertIn('failed', acquisition['official_source_attempt']['result'])

    def test_staging_and_real_interior_coverage_are_not_overclaimed(self):
        coverage = self.provenance['coverage']
        positions = {node['id']: node['position'] for node in self.graph['nodes']}
        self.assertEqual(coverage['weak_components'], 1)
        self.assertGreater(coverage['interior_eligible_count'], 0)
        for candidate in coverage['staging_candidates'][:2]:
            self.assertLess(candidate['offset_from_search_anchor_m'], 20)
            self.assertGreater(candidate['nearest_any_node']['weak_component_nodes'], 1000)
        reachable = shortest_distances(self.graph, coverage['staging_candidates'][0]['node_id'])
        for candidate in coverage['interior_cell_centres_with_reachable_node_within_150m']:
            self.assertIn(candidate['node_id'], reachable)
            self.assertLessEqual(math.dist(candidate['position'], positions[candidate['node_id']]), 150)
            self.assertEqual(candidate['position'], [ACTIVE_GRID['west'] + (candidate['col'] + .5) * 500,
                                                    ACTIVE_GRID['north'] - (candidate['row'] + .5) * 500])
        first, second, interior = coverage['target_access']
        self.assertLess(first['access_from_staging'][0]['off_network_distance_m'], 150)
        self.assertGreater(second['access_from_staging'][0]['off_network_distance_m'], 1500)
        self.assertGreater(interior['access_from_staging'][0]['off_network_distance_m'], 150)

    def test_exact_offline_regeneration(self):
        runtime = DATA / '.runtime'
        runtime.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=runtime) as directory:
            with patch('urllib.request.urlopen', side_effect=AssertionError('Preparation must stay offline')):
                with redirect_stdout(io.StringIO()):
                    prepare(Path(directory))
            for filename in ('network.json', 'provenance.json'):
                self.assertEqual((Path(directory) / filename).read_bytes(), (DATA / filename).read_bytes())

    def test_town_labels_are_named_source_points_not_population_assignments(self):
        labels = json.loads((DATA / 'town_labels.json').read_bytes())
        self.assertEqual(labels['source']['license'], 'ODbL-1.0')
        self.assertEqual({town['name'] for town in labels['towns']}, {'Estepona', 'Casares', 'Manilva', 'Marbella'})
        for town in labels['towns']:
            self.assertIn('osm_node_id', town)
            self.assertIn('position', town)
            self.assertNotIn('population', town)
            self.assertIn('not a population location', town['meaning'])


if __name__ == '__main__':
    unittest.main()
