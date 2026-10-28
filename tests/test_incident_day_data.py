import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'envs/wildfire_response/core/src'))
from envs.wildfire_response.incident_day.data import digest, load_inputs, prepare_inputs


FIXTURE = Path(__file__).resolve().parents[1] / 'data/incident_day_v2/synthetic_fixture.json'


def rehash(document):
    document['sha256'] = digest({key: value for key, value in document.items() if key != 'sha256'})


class IncidentDayDataTests(unittest.TestCase):
    def setUp(self):
        self.inputs = json.loads(FIXTURE.read_text())
        self.prepared = self.inputs['prepared']
        self.domain = self.inputs['domain']
        self.network = self.inputs['network']

    def prepare(self, **kwargs):
        return prepare_inputs(self.prepared, self.domain, self.network, **kwargs)

    def rehash_sources(self):
        rehash(self.prepared)
        self.domain['prepared_inputs_sha256'] = self.prepared['sha256']
        rehash(self.domain)

    def test_complete_synthetic_fixture_and_source_provenance(self):
        pack = self.prepare()
        self.assertEqual(pack['approval_status'], 'approved_synthetic_fixture')
        self.assertEqual(load_inputs(pack, pack['sha256']), pack)
        self.assertEqual(pack['source_pins'], dict(prepared_sha256=self.prepared['sha256'],
                                                   domain_sha256=self.domain['sha256'],
                                                   network_sha256=self.network['sha256']))
        self.assertEqual(pack['provenance']['prepared'], self.prepared['provenance'])
        self.assertEqual(pack['provenance']['domain'], self.domain['provenance'])
        self.assertEqual(pack['walk_mask'], [[1, 1], [1, 1]])

    def test_unknown_fuel_fails_closed_and_never_becomes_firebreak(self):
        self.prepared['fuel_classes'][0][0] = 0
        self.prepared['fuel_valid_mask'][0][0] = 0
        self.rehash_sources()
        pack = self.prepare()
        self.assertIsNone(pack['fuel_classes'][0][0])
        self.assertEqual(pack['fuel_known_mask'][0][0], 0)
        self.assertEqual(pack['walk_mask'][0][0], 1)
        with self.assertRaisesRegex(ValueError, 'unapproved'):
            load_inputs(pack, pack['sha256'])
        assumed = self.prepare(fuel_resolution={'kind': 'synthetic', 'class': 4})
        self.assertEqual(assumed['fuel_classes'][0][0], 4)
        self.assertEqual(assumed['fuel_known_mask'][0][0], 0)
        self.assertEqual(assumed['approval_status'], 'unapproved_source_pack')
        self.assertEqual(load_inputs(assumed, assumed['sha256'], allow_unapproved=True), assumed)

    def test_false_mask_repairs_are_rejected(self):
        self.prepared['study_mask'][0][0] = 0
        self.rehash_sources()
        with self.assertRaisesRegex(ValueError, 'masking is not repair'):
            self.prepare()
        self.prepared['study_mask'][0][0] = 1
        self.prepared['fuel_valid_mask'][0][0] = 0
        self.rehash_sources()
        with self.assertRaisesRegex(ValueError, 'fuel and validity'):
            self.prepare()
        self.prepared['fuel_classes'][0][0] = 0
        self.rehash_sources()
        pack = self.prepare()
        pack['walk_mask'][0][0] = 0
        rehash(pack)
        with self.assertRaisesRegex(ValueError, 'Masks cannot conceal'):
            load_inputs(pack, pack['sha256'], allow_unapproved=True)

    def test_source_hash_and_lineage_integrity(self):
        self.network['edges'].append({'id': 1})
        with self.assertRaisesRegex(ValueError, 'network hash'):
            self.prepare()
        self.network['edges'].clear()
        self.prepared['fuel_classes'][0][0] = 2
        with self.assertRaisesRegex(ValueError, 'prepared input hash'):
            self.prepare()
        rehash(self.prepared)
        with self.assertRaisesRegex(ValueError, 'does not pin'):
            self.prepare()

    def test_complete_heights_and_cardinalities_required(self):
        for bad_height in (None, float('inf'), True):
            with self.subTest(height=bad_height):
                source = copy.deepcopy(self.domain)
                source['elevations_metres'][0][0] = bad_height
                if bad_height == float('inf'):
                    with self.assertRaises(ValueError):
                        rehash(source)
                else:
                    rehash(source)
                    with self.assertRaisesRegex(ValueError, 'finite heights'):
                        prepare_inputs(self.prepared, source, self.network)
        self.domain['height_support_mask'][0][0] = 0
        rehash(self.domain)
        with self.assertRaisesRegex(ValueError, 'complete study and height coverage'):
            self.prepare()
        self.domain['height_support_mask'][0][0] = 1
        self.domain['elevations_metres'][0].pop()
        rehash(self.domain)
        with self.assertRaisesRegex(ValueError, 'heights dimensions'):
            self.prepare()

    def test_caller_pin_and_mask_integrity(self):
        pack = self.prepare()
        with self.assertRaisesRegex(ValueError, 'caller-pinned'):
            load_inputs(pack, '0' * 64)
        pack['fuel_known_mask'][0][0] = 0
        rehash(pack)
        with self.assertRaisesRegex(ValueError, 'Masks cannot conceal'):
            load_inputs(pack, pack['sha256'])


if __name__ == '__main__':
    unittest.main()
