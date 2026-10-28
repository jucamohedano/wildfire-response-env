"""Offline tests for bounded OSM display-road source snapshots."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError

from pyproj import Transformer
from eval import display_road_snapshot as snapshot
from eval import prepare_display_roads_v2 as roads


def example():
    reverse = Transformer.from_crs('EPSG:25830', 'EPSG:4326', always_xy=True)
    first_point = reverse.transform(296900, 4040000)
    second_point = reverse.transform(300000, 4040000)
    third_point = reverse.transform(316000, 4040000)
    elements = [dict(type='node', id=index, version=1, lon=point[0], lat=point[1])
                for index, point in enumerate((first_point, second_point, third_point), 1)]
    elements += [dict(type='way', id=10, version=1, nodes=[1, 2, 3],
                      tags={'highway': 'track', 'access': 'private'}, timestamp='2026-01-01T00:00:00Z')]
    return dict(elements=elements)


class SnapshotTests(unittest.TestCase):
    def test_outward_bounds_cover_densified_extent_edges(self):
        west, south, east, north = snapshot.outward_bounds()
        transformer = Transformer.from_crs('EPSG:25830', 'EPSG:4326', always_xy=True)
        west_extent, south_extent, east_extent, north_extent = snapshot.EXTENT
        for index in range(101):
            fraction = index / 100
            for east_coordinate, north_coordinate in (
                (west_extent + (east_extent - west_extent) * fraction, south_extent),
                (west_extent + (east_extent - west_extent) * fraction, north_extent),
                (west_extent, south_extent + (north_extent - south_extent) * fraction),
                (east_extent, south_extent + (north_extent - south_extent) * fraction),
            ):
                lon, lat = transformer.transform(east_coordinate, north_coordinate)
                self.assertTrue(west < lon < east and south < lat < north)

    def test_snapshot_reproduces_compiler_geometry_and_rejects_tampering(self):
        doc = example()
        extra = dict(elements=[dict(type='node', id=2, version=2, lon=doc['elements'][1]['lon'], lat=doc['elements'][1]['lat']),
                               dict(type='way', id=10, version=2, nodes=[1, 2, 3], tags={'highway':'track','access':'private'}, timestamp='2026-02-01T00:00:00Z')])
        full_nodes, full_ways, _ = roads.collect_elements([doc, extra])
        expected, _ = roads.build_asset(full_nodes, full_ways)
        compressed, manifest = snapshot.prepare([doc, extra], [], {}, snapshot.outward_bounds())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / 'manifest.json'
            bundle_path = root / manifest['bundle']['path']
            encoded = json.dumps(manifest, sort_keys=True).encode()
            manifest_path.write_bytes(encoded)
            bundle_path.write_bytes(compressed)
            got, nodes, ways = snapshot.load_snapshot(manifest_path, roads.digest(encoded))
            actual, _ = roads.build_asset(nodes, ways)
            self.assertEqual(expected, actual)
            self.assertEqual(ways[10]['version'], 2)
            self.assertEqual(ways[10]['nodes'], [1, 2, 3])
            self.assertEqual(ways[10]['tags']['access'], 'private')
            self.assertEqual(ways[10]['timestamp'], '2026-02-01T00:00:00Z')
            self.assertIn('not a 2021', got['source']['temporal_scope'])
            with self.assertRaises(ValueError):
                snapshot.load_snapshot(manifest_path, '0'*64)
            bundle_path.write_bytes(compressed[:-1] + bytes([compressed[-1] ^ 1]))
            with self.assertRaises(ValueError):
                snapshot.load_snapshot(manifest_path, roads.digest(encoded))

    def test_symlinks_and_oversized_manifest_are_rejected(self):
        from unittest.mock import patch
        compressed, manifest = snapshot.prepare([example()], [], {}, snapshot.outward_bounds())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / 'manifest.json'
            bundle_path = root / manifest['bundle']['path']
            encoded = json.dumps(manifest, sort_keys=True).encode()
            manifest_path.write_bytes(encoded)
            bundle_path.write_bytes(compressed)
            manifest_link = root / 'manifest-link.json'
            manifest_link.symlink_to(manifest_path)
            with self.assertRaises(ValueError):
                snapshot.load_snapshot(manifest_link, roads.digest(encoded))
            with patch.object(snapshot, 'MAX_MANIFEST', 10):
                with self.assertRaises(ValueError):
                    snapshot.load_snapshot(manifest_path, roads.digest(encoded))
            bundle_path.unlink()
            bundle_path.symlink_to(manifest_path)
            with self.assertRaises(ValueError):
                snapshot.load_snapshot(manifest_path, roads.digest(encoded))

    def test_pinned_portable_snapshot_builds_offline(self):
        manifest, nodes, ways = snapshot.load_snapshot(
            snapshot.OUTPUT_DIR / 'display-roads-manifest.json',
            'b21852b130cf02edebb15eab03c40d4b3ae4d8c805d11ee317233ccbbe9107d5')
        asset, _ = roads.build_asset(nodes, ways)
        self.assertEqual(len(manifest['requests']), 16)
        self.assertEqual(len(ways), 2931)
        self.assertEqual(roads.digest(roads.canonical(asset['lines'])),
                         '132a66edd3786e6f098944545fcd2ed7ae0e86d2720def1ebd2f30a5700384d2')

    def test_missing_nodes_and_equal_version_conflicts_fail(self):
        doc = example(); doc['elements'][3]['nodes'].append(999)
        with self.assertRaisesRegex(ValueError, 'Missing source way node'): snapshot.normalize([doc])
        one = example(); two = example(); two['elements'][0]['lon'] += .01
        with self.assertRaisesRegex(ValueError, 'Conflicting'): snapshot.normalize([one, two])

    def test_node_cap_subdivision_and_full_coverage(self):
        raw = json.dumps(example()).encode()
        calls = []
        class Response(io.BytesIO):
            headers = {'Date': 'sample'}
        def opener(request, timeout):
            calls.append(request.full_url)
            if len(calls) == 1:
                raise HTTPError(request.full_url, 400, 'node cap', {}, io.BytesIO(b'too many nodes'))
            return Response(raw)
        docs, records, totals = snapshot.acquire([0, 0, 1, 1], opener=opener, sleeper=lambda delay: None, monotonic=lambda: 0)
        self.assertEqual(len(calls), 18)
        self.assertEqual(len(docs), 17)
        self.assertEqual(len(records), 17)
        self.assertEqual(totals['raw_bytes'], 17 * len(raw) + len(b'too many nodes'))
        self.assertTrue(all(record['sha256'] == roads.digest(raw) for record in records))
        self.assertEqual(records[0]['element_count'], len(example()['elements']))

    def test_raw_budget_fails_instead_of_returning_partial_coverage(self):
        from unittest.mock import patch
        class Response(io.BytesIO): headers = {}
        with patch.object(snapshot, 'MAX_TOTAL_RAW', 5):
            with self.assertRaisesRegex(ValueError, 'budget'):
                snapshot.acquire([0,0,1,1], opener=lambda *arguments, **keyword_arguments: Response(b'123456'), sleeper=lambda delay: None)


if __name__ == '__main__': unittest.main()
