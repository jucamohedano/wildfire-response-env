import json
from pathlib import Path
import tempfile
import hashlib
from unittest.mock import patch
from unittest import TestCase
from eval.build_spain_hub_dataset import no_private_fields, write_json


class SpainPayloadPrivacyTests(TestCase):
    def test_nested_private_runtime_fields_are_rejected_before_file_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'payload.json'
            with self.assertRaisesRegex(ValueError,'Private'):
                write_json(path, {'records':[{'future_events':[{'minute':10}]}]})
            self.assertFalse(path.exists())

    def test_static_fuel_estimate_provenance_is_not_an_episode_label(self):
        value={'confidence':'low','source_known_mask':[[0]],'support_neighbours':[{'row':0,'column':1}],
               'description':'No future_events or private audits are included'}
        no_private_fields(value)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'source.json';write_json(path,value)
            self.assertEqual(json.loads(path.read_text()),value)
            self.assertEqual(sorted(p.name for p in Path(directory).iterdir()),['source.json'])


class HistoricalSnapshotPinTests(TestCase):
    def fixture(self, root):
        from eval import build_spain_hub_dataset as build
        rows={'incident_claims.jsonl':b'{"claim":"source"}\n',
              'municipal_population_2021.csv':b'population\n7\n','source_registry.json':b'{}\n'}
        outputs=[{'path':'data/'+name,'sha256':hashlib.sha256(raw).hexdigest()} for name,raw in rows.items()]
        rows['provenance.json']=json.dumps({'outputs':outputs}).encode()
        for name,raw in rows.items():(root/name).write_bytes(raw)
        return build,{name:hashlib.sha256(raw).hexdigest() for name,raw in rows.items()}

    def test_reviewed_snapshot_passes_and_self_signed_changes_do_not(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);build,pins=self.fixture(root)
            with patch.object(build,'HISTORICAL_EVIDENCE_PINS',pins):
                build.validate_historical_evidence(root)
                (root/'incident_claims.jsonl').write_text('{"claim":"rewritten"}\n')
                value=json.loads((root/'provenance.json').read_text())
                value['outputs'][0]['sha256']=hashlib.sha256((root/'incident_claims.jsonl').read_bytes()).hexdigest()
                (root/'provenance.json').write_text(json.dumps(value))
                with self.assertRaisesRegex(ValueError,'immutable snapshot'):
                    build.validate_historical_evidence(root)

    def test_all_required_output_entries_are_mandatory(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);build,pins=self.fixture(root)
            value=json.loads((root/'provenance.json').read_text());value['outputs'].pop()
            raw=json.dumps(value).encode();(root/'provenance.json').write_bytes(raw)
            pins['provenance.json']=hashlib.sha256(raw).hexdigest()
            with patch.object(build,'HISTORICAL_EVIDENCE_PINS',pins):
                with self.assertRaisesRegex(ValueError,'required output'):
                    build.validate_historical_evidence(root)
