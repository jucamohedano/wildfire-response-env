"""Fast offline fixtures for bounded display cartography, not vehicle routing."""

import copy
import gzip
import importlib.util
import json
import math
from pathlib import Path
import unittest
from unittest.mock import patch

from eval import prepare_display_roads_v2 as roads


def node(identifier, longitude, latitude, version=1):
    return dict(type='node', id=identifier, version=version, lon=longitude, lat=latitude)


def way(identifier, nodes, highway='tertiary', version=1, **tags):
    return dict(type='way', id=identifier, version=version, nodes=nodes,
                tags=dict(highway=highway, **tags))


class CachedSourceTests(unittest.TestCase):
    def test_decompressed_source_bytes_and_hash_are_both_pinned(self):
        raw = roads.canonical(dict(elements=[node(1, -5.2, 36.5)]))
        compressed = gzip.compress(raw, mtime=0)
        record = dict(bytes=len(raw), sha256=roads.digest(raw))
        self.assertEqual(roads.verified_tile(compressed, record), json.loads(raw))
        for changed in (dict(record, bytes=len(raw) + 1), dict(record, sha256='0' * 64)):
            with self.assertRaises(ValueError):
                roads.verified_tile(compressed, changed)
        with patch.object(roads, 'MAX_TILE_BYTES', 10), self.assertRaises(ValueError):
            roads.verified_tile(compressed, record)
        with patch.object(roads, 'MAX_GZIP_BYTES', 1), self.assertRaises(ValueError):
            roads.verified_tile(compressed, record)

    def test_pinned_but_incomplete_api_response_is_rejected(self):
        for document in (dict(elements=[], remark='incomplete'), dict(error='bad request'), []):
            raw = roads.canonical(document)
            with self.assertRaises(ValueError):
                roads.verified_tile(gzip.compress(raw, mtime=0), dict(bytes=len(raw), sha256=roads.digest(raw)))
        raw = roads.canonical(dict(elements=[]))
        self.assertEqual(roads.verified_tile(gzip.compress(raw, mtime=0),
                         dict(bytes=len(raw), sha256=roads.digest(raw))), dict(elements=[]))

    def test_deduplication_chooses_highest_version_independent_of_tile_order(self):
        first = dict(elements=[node(1, -5.2, 36.5), node(2, -5.19, 36.5), way(10, [1, 2])])
        second = dict(elements=[node(1, -5.21, 36.51, version=2),
                               way(10, [2, 1], highway='track', version=2)])
        before = copy.deepcopy([first, second])
        forward = roads.collect_elements([first, second])
        reverse = roads.collect_elements([second, first])
        self.assertEqual(forward, reverse)
        self.assertEqual(forward[0][1]['lon'], -5.21)
        self.assertEqual(forward[1][10]['tags']['highway'], 'track')
        self.assertEqual(forward[2], dict(node=1, way=1))
        self.assertEqual([first, second], before)

    def test_equal_version_conflicts_fail_instead_of_silently_picking_a_tile(self):
        for first, second in ((node(1, -5.2, 36.5), node(1, -5.21, 36.5)),
                              (way(10, [1, 2]), way(10, [2, 1]))):
            with self.assertRaises(ValueError):
                roads.collect_elements([dict(elements=[first]), dict(elements=[second])])

    def test_real_cached_tile_matches_frozen_acquisition(self):
        acquisition_bytes = (roads.ROOT / roads.ACQUISITION).read_bytes()
        self.assertEqual(roads.digest(acquisition_bytes), roads.ACQUISITION_SHA256)
        acquisition = json.loads(acquisition_bytes)
        self.assertEqual(len(acquisition['requests']), 21)
        record = acquisition['requests'][0]
        self.assertGreater(len(roads.verified_tile((roads.ROOT / record['raw_cache']).read_bytes(), record)['elements']), 0)


class GeometryTests(unittest.TestCase):
    def test_clips_crossing_lines_with_both_endpoints_outside(self):
        extent = [0, 0, 10, 10]
        self.assertEqual(roads.clip_segment([-5, 5], [15, 5], extent), (.25, .75))
        self.assertEqual(roads.clip_line([[-5, 5], [15, 5]], extent), [[[0.0, 5.0], [10.0, 5.0]]])
        self.assertIsNone(roads.clip_segment([-5, 11], [15, 11], extent))

    def test_exits_and_reentries_do_not_gain_a_fabricated_boundary_connector(self):
        coordinates = [[-2, 5], [5, 5], [12, 5], [12, 8], [5, 8], [-2, 8]]
        before = copy.deepcopy(coordinates)
        parts = roads.clip_line(coordinates, [0, 0, 10, 10])
        self.assertEqual(parts, [[[0.0, 5.0], [5.0, 5.0], [10.0, 5.0]],
                                 [[10.0, 8.0], [5.0, 8.0], [0.0, 8.0]]])
        self.assertEqual(coordinates, before)

    def test_duplicate_vertices_and_point_contacts_do_not_become_zero_length_lines(self):
        self.assertEqual(roads.clip_line([[1, 1], [1, 1]], [0, 0, 10, 10]), [])
        self.assertEqual(roads.clip_line([[-1, 1], [1, -1]], [0, 0, 10, 10]), [])
        self.assertEqual(roads.clip_line([[1, 1], [1, 1], [2, 2]], [0, 0, 10, 10]), [[[1.0, 1.0], [2.0, 2.0]]])

    def test_simplification_preserves_endpoints_and_bounds_geometric_deviation(self):
        coordinates = [[0, 0], [5, 1], [10, 0], [15, 8], [20, 0]]
        before = copy.deepcopy(coordinates)
        simplified = roads.simplify_line(coordinates)
        self.assertEqual((simplified[0], simplified[-1]), (coordinates[0], coordinates[-1]))
        self.assertLess(len(simplified), len(coordinates))
        for point in coordinates:
            self.assertLessEqual(min(roads.point_segment_distance(point, start, end)
                                    for start, end in zip(simplified, simplified[1:])), roads.SIMPLIFY_METRES)
        self.assertEqual(coordinates, before)

    def test_small_real_closed_loops_are_not_collapsed_into_a_backtracking_line(self):
        coordinates = [[2, 2], [3, 2], [3, 3], [2, 3], [2, 2]]
        self.assertEqual(roads.simplify_line(coordinates), coordinates)

    def test_access_flags_never_claim_positive_drivability(self):
        for tags in ({}, dict(access='yes'), dict(access='private', motor_vehicle='yes')):
            self.assertEqual(roads.access_category(tags), 'unknown')
        for tags in (dict(access='private'), dict(motor_vehicle='no'), dict(access='destination'),
                     dict(maxweight='3.5'), {'vehicle:conditional': 'no @ (wet)'}):
            self.assertEqual(roads.access_category(tags), 'restricted')


@unittest.skipUnless(importlib.util.find_spec('pyproj'), 'Install pyproj for offline projection tests')
class DisplayCompilerTests(unittest.TestCase):
    def elements(self):
        return roads.collect_elements([dict(elements=[node(1, -5.22, 36.54), node(2, -5.20, 36.54),
            node(3, -5.21, 36.53), node(4, -5.21, 36.55),
            way(10, [1, 2]), way(20, [3, 4], highway='path', access='private')])])[:2]

    def test_real_projection_axis_order_display_categories_and_no_crossing_noding(self):
        from pyproj import Transformer, network

        nodes, ways = self.elements()
        before = copy.deepcopy((nodes, ways))
        asset, statistics = roads.build_asset(nodes, ways)
        self.assertEqual(asset['role'], 'display_only_not_routing')
        self.assertEqual(asset['crs'], 'EPSG:25830')
        self.assertEqual(asset['extent'], list(roads.EXTENT))
        self.assertEqual(statistics['lines'], 2)
        self.assertEqual([line['kind'] for line in asset['lines']], ['major', 'path'])
        self.assertEqual(asset['lines'][1]['access'], 'restricted')
        expected = [round(value, 1) for value in Transformer.from_crs(4326, 25830, always_xy=True).transform(-5.22, 36.54)]
        self.assertEqual(asset['lines'][0]['coordinates'][0], expected)
        self.assertTrue(297000 < expected[0] < 315500 and 4036500 < expected[1] < 4054500)
        self.assertTrue(all(len(line['coordinates']) == 2 for line in asset['lines']))
        self.assertEqual((nodes, ways), before)
        self.assertFalse(network.is_network_enabled())

    def test_deterministic_immutable_output_without_network_or_routing_fields(self):
        nodes, ways = self.elements()
        with patch('socket.create_connection', side_effect=AssertionError('Offline compiler only')):
            first, _ = roads.build_asset(nodes, ways)
            second, _ = roads.build_asset(dict(reversed(list(nodes.items()))), dict(reversed(list(ways.items()))))
        self.assertEqual(roads.canonical(first), roads.canonical(second))
        self.assertEqual(first['sha256'], roads.asset_digest(first))
        self.assertNotIn('edges', first)
        self.assertTrue(all('speed_kph' not in line and 'source' not in line and 'target' not in line for line in first['lines']))
        first['source']['name'] = 'changed output only'
        self.assertNotEqual(first['source']['name'], roads.SOURCE['name'])

    def test_newer_nonroad_version_does_not_leave_behind_an_old_road(self):
        nodes, ways = self.elements()
        newer = way(10, [1, 2], highway='construction', version=2)
        _, updates, _ = roads.collect_elements([dict(elements=[newer])])
        ways.update(updates)
        asset, _ = roads.build_asset(nodes, ways)
        self.assertEqual([line['id'] for line in asset['lines']], ['osm:w20:0000'])

    def test_missing_source_geometry_and_excessive_outputs_fail_closed(self):
        nodes, ways = self.elements()
        del nodes[1]
        with self.assertRaises(ValueError):
            roads.build_asset(nodes, ways)
        nodes, ways = self.elements()
        for cap in ('MAX_VERTICES', 'MAX_ASSET_BYTES'):
            with patch.object(roads, cap, 1), self.assertRaises(ValueError):
                roads.build_asset(nodes, ways)


class PinnedDisplayAssetTests(unittest.TestCase):
    def test_asset_hash_extent_full_domain_coverage_caps_and_license(self):
        encoded = (roads.ROOT / roads.ASSET).read_bytes()
        asset = json.loads(encoded)
        provenance = json.loads((roads.ROOT / roads.PROVENANCE).read_bytes())
        self.assertEqual(asset['version'], roads.VERSION)
        self.assertEqual(asset['sha256'], roads.asset_digest(asset))
        self.assertEqual(provenance['output']['sha256'], asset['sha256'])
        self.assertEqual(provenance['output']['file_sha256'], roads.digest(encoded))
        self.assertEqual(provenance['preparation']['script_sha256'], roads.digest(Path(roads.__file__).read_bytes()))
        self.assertEqual(asset['source']['license'], 'ODbL-1.0')
        self.assertIn('OpenStreetMap contributors', asset['source']['attribution'])
        self.assertEqual(asset['role'], 'display_only_not_routing')
        self.assertEqual(asset['extent'], [297000, 4036500, 315500, 4054500])
        self.assertLess(len(encoded), roads.MAX_ASSET_BYTES)
        identifiers = [line['id'] for line in asset['lines']]
        self.assertEqual(identifiers, sorted(set(identifiers)))
        points = [point for line in asset['lines'] for point in line['coordinates']]
        self.assertLessEqual(len(points), roads.MAX_VERTICES)
        self.assertGreater(max(point[0] for point in points), 314000)
        self.assertEqual({line['kind'] for line in asset['lines']}, {'major', 'local', 'track', 'path'})
        self.assertEqual({line['access'] for line in asset['lines']}, {'unknown', 'restricted'})
        for line in asset['lines']:
            self.assertGreater(len(line['coordinates']), 1)
            self.assertTrue(any(first != second for first, second in zip(line['coordinates'], line['coordinates'][1:])))
            for east, north in line['coordinates']:
                self.assertTrue(math.isfinite(east) and math.isfinite(north))
                self.assertTrue(297000 <= east <= 315500 and 4036500 <= north <= 4054500)


if __name__ == '__main__':
    unittest.main()
