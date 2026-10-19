import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from envs.wildfire_response.regional.benchmark import LOCK, manifest, verify_lock
from envs.wildfire_response.regional.evaluate import export_tasks


class BenchmarkTests(unittest.TestCase):
    def test_frozen_inputs_and_export_identity(self):
        self.assertEqual(json.loads(LOCK.read_text()), manifest())
        with tempfile.TemporaryDirectory() as directory:
            export_tasks(Path(directory)/'tasks')
            exported = json.loads((Path(directory)/'tasks'/'manifest.json').read_text())
            self.assertEqual(exported['benchmark'], verify_lock())

    def test_changed_reward_or_split_fails_before_export(self):
        with tempfile.TemporaryDirectory() as directory:
            changed = manifest()
            changed['tasks'][0]['split'] = 'test'
            path = Path(directory)/'changed.json'
            path.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, 'Frozen benchmark changed'):
                verify_lock(path)
            with patch('envs.wildfire_response.regional.benchmark.WEIGHTS', {'exposed_population': 1}):
                with self.assertRaises(ValueError):
                    export_tasks(Path(directory)/'should-not-exist')
            self.assertFalse((Path(directory)/'should-not-exist').exists())


if __name__ == '__main__':
    unittest.main()
