import json
from pathlib import Path
import tempfile
import unittest

from envs.wildfire_response.data.assemble import assemble, consolidate


class AssemblyTests(unittest.TestCase):
    def test_bundle_is_versioned_and_has_portable_evidence_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inventory = root / 'inventory'
            inventory.mkdir()
            (inventory / 'inventory.json').write_text(json.dumps(dict(
                cutoff='2026-10-18', activations=[], products=[], layers=[], summary=dict(activations=0))), encoding='utf-8')
            evidence = root / 'evidence.json'
            evidence.write_text(json.dumps(dict(sources={}, incidents=[])), encoding='utf-8')
            output = root / 'bundle'
            summary = assemble(output, inventory, [evidence])
            self.assertEqual(summary['reviewed_incidents'], 0)
            manifest = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
            self.assertTrue(all((output / item['bundle_path']).is_file() for item in manifest['files']))
            with self.assertRaises(ValueError):
                assemble(output, inventory, [evidence])

    def test_deduplicates_evidence_and_preserves_qualifiers(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index in range(2):
                path = Path(directory) / f'research-{index}.json'
                document = dict(sources=dict(official=dict(url='https://example.org/', published_date='2026-07-10')),
                                incidents=[dict(id='es-2026-test-fire', name='Test', facts=[dict(
                                    field='reported_area', value=20, source='official', qualifier=f'qualifier-{index}')])])
                path.write_text(json.dumps(document), encoding='utf-8')
                paths.append(path)
            sources, incidents, facts = consolidate(paths)
            self.assertEqual(len(incidents), 1)
            self.assertEqual(len(facts), 1)
            self.assertEqual(len(sources), 2)
            self.assertEqual(len(facts[0]['qualifiers']), 2)
            self.assertFalse(facts[0]['actor_input_eligible'])
            with self.assertRaises(ValueError):
                consolidate(paths, cutoff='2026-07-09')


if __name__ == '__main__':
    unittest.main()
