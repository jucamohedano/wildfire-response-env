from unittest import TestCase
from unittest.mock import patch


class ArchivedExportGuardTests(TestCase):
    def test_legacy_export_stops_before_reading_inputs_without_explicit_ack(self):
        from envs.wildfire_response.agentenv.export import export_dataset
        with patch('envs.wildfire_response.agentenv.export._repo_root', side_effect=AssertionError('should not read data')):
            with self.assertRaisesRegex(ValueError, 'allow-archived-catalog'):
                export_dataset('/missing', '/missing', '/tmp/not-created')
            with self.assertRaisesRegex(AssertionError, 'should not read data'):
                export_dataset('/missing', '/missing', '/tmp/not-created', allow_archived_catalog=True)
