import contextlib
import hashlib
import io
import json
import operator
import random
import struct
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from eval import check_local_gpu_determinism as checker


class IntegerTensorFixture:
    def __init__(self, values):
        self.values = values

    def combine(self, other, operation):
        return IntegerTensorFixture([operation(first, second)
                                     for first, second in zip(self.values, other.values)])

    def __add__(self, other):
        return self.combine(other, operator.add)

    def __sub__(self, other):
        return self.combine(other, operator.sub)

    def __mul__(self, other):
        return self.combine(other, operator.mul)

    def __and__(self, mask):
        return IntegerTensorFixture([value & mask for value in self.values])

    def cpu(self):
        return self

    def tolist(self):
        return list(self.values)


class LocalGPUCheckTests(unittest.TestCase):
    def setUp(self):
        self.config = dict(seed=7, elements=4, repeats=3, device=0)

    def test_seed_is_private_and_reproducible(self):
        state = random.getstate()
        inputs = checker.make_inputs(7, 4)
        self.assertEqual(inputs, checker.make_inputs(7, 4))
        self.assertNotEqual(inputs, checker.make_inputs(8, 4))
        self.assertEqual(random.getstate(), state)

    def test_reference_and_checksum_known_fixture(self):
        expected = [65533, 16, 65511, 0]
        self.assertEqual(checker.cpu_reference([1, -4, 0, 7], [2, 0, -5, 7]), expected)
        encoded = b'local-gpu-integer-v1\0' + struct.pack('<Q4q', 4, *expected)
        self.assertEqual(checker.result_checksum(expected), hashlib.sha256(encoded).hexdigest())
        self.assertNotEqual(checker.result_checksum(expected), checker.result_checksum(expected[::-1]))
        with self.assertRaises(ValueError):
            checker.cpu_reference([1], [])

    def test_exact_comparison_detects_repeat_and_reference_mismatches(self):
        expected = [1, 2]
        passed = checker.compare_results(expected, [[1, 2], [1, 2], [1, 2]])
        self.assertEqual(passed['status'], 'pass')
        self.assertEqual(passed['runs_completed'], 3)
        drift = checker.compare_results(expected, [[1, 2], [1, 3]])
        self.assertEqual(drift['status'], 'fail')
        self.assertFalse(drift['repeatability_exact'])
        self.assertFalse(drift['cpu_reference_exact'])
        wrong = checker.compare_results(expected, [[1], [1]])
        self.assertEqual(wrong['status'], 'fail')
        self.assertTrue(wrong['repeatability_exact'])
        self.assertFalse(wrong['cpu_reference_exact'])
        for results in ([], [[1, 2]]):
            with self.assertRaises(ValueError):
                checker.compare_results(expected, results)

    def test_missing_torch_is_unavailable(self):
        for error in (ModuleNotFoundError('torch'), OSError('missing CUDA library')):
            with patch.object(checker.importlib, 'import_module', side_effect=error):
                report = checker.run_worker(self.config)
            self.assertEqual(report['status'], 'unavailable')
            self.assertIsNone(report['repeatability_exact'])
            self.assertIsNone(report['result_sha256'])

    def test_missing_cuda_is_unavailable(self):
        fake_torch = SimpleNamespace(__version__='fixture', version=SimpleNamespace(cuda=None),
                                     cuda=SimpleNamespace(is_available=lambda: False))
        with patch.object(checker.importlib, 'import_module', return_value=fake_torch):
            report = checker.run_worker(self.config)
        self.assertEqual(report['status'], 'unavailable')
        self.assertEqual(report['torch_version'], 'fixture')
        self.assertIsNone(report['cuda_version'])

    def test_tensor_data_bound_is_below_sixteen_mib(self):
        config = dict(self.config, elements=checker.MAX_ELEMENTS)
        report = checker.base_report(config)
        self.assertLess(report['conservative_tensor_data_bound_bytes'], 16 * 1024 * 1024)
        self.assertLessEqual(report['conservative_tensor_data_bound_bytes'],
                             report['tensor_data_cap_bytes'])
        self.assertIn('context/driver memory excluded', report['memory_scope'])
        self.assertIn('Not GPU simulation acceleration or model quality', report['scope'])

    def test_worker_algorithm_settings_and_identity_with_integer_fixture(self):
        from unittest.mock import Mock

        properties = SimpleNamespace(name='GPU fixture', total_memory=6 * 1024**3,
                                     major=8, minor=6)
        cuda = Mock()
        cuda.is_available.return_value = True
        cuda.device_count.return_value = 1
        cuda.get_device_properties.return_value = properties
        cuda.max_memory_allocated.return_value = 1024
        tensor = Mock(side_effect=lambda values, **kwargs: IntegerTensorFixture(values))
        deterministic = Mock()
        fake_torch = SimpleNamespace(__version__='fixture', version=SimpleNamespace(cuda='fixture'),
                                     cuda=cuda, int64='int64', tensor=tensor,
                                     use_deterministic_algorithms=deterministic)
        with patch.object(checker.importlib, 'import_module', return_value=fake_torch):
            report = checker.run_worker(self.config)
        self.assertEqual(report['status'], 'pass')
        self.assertTrue(report['repeatability_exact'])
        self.assertTrue(report['cpu_reference_exact'])
        self.assertEqual(report['runs_completed'], 3)
        self.assertEqual(report['gpu']['name'], 'GPU fixture')
        self.assertEqual(report['gpu']['total_memory_bytes'], properties.total_memory)
        self.assertEqual(report['allocator_peak_bytes'], 1024)
        deterministic.assert_called_once_with(True)
        cuda.set_per_process_memory_fraction.assert_called_once_with(
            checker.ALLOCATOR_CAP_BYTES / properties.total_memory, 0)
        self.assertEqual(cuda.synchronize.call_count, 3)
        self.assertEqual(tensor.call_count, 2)
        self.assertEqual(tensor.call_args.kwargs, dict(dtype='int64', device='cuda:0'))
        cuda.synchronize.side_effect = RuntimeError('fixture execution error')
        with patch.object(checker.importlib, 'import_module', return_value=fake_torch):
            self.assertEqual(checker.run_worker(self.config)['status'], 'error')
        cuda.get_device_properties.side_effect = RuntimeError('fixture initialization error')
        with patch.object(checker.importlib, 'import_module', return_value=fake_torch):
            self.assertEqual(checker.run_worker(self.config)['status'], 'unavailable')
        cuda.device_count.return_value = 0
        with patch.object(checker.importlib, 'import_module', return_value=fake_torch):
            self.assertEqual(checker.run_worker(self.config)['status'], 'unavailable')

    def test_worker_bounds_are_checked_before_torch_import(self):
        for config in (dict(self.config, elements=checker.MAX_ELEMENTS + 1),
                       dict(self.config, repeats=checker.MAX_REPEATS + 1),
                       dict(self.config, elements=True), {}):
            with patch.object(checker.importlib, 'import_module') as importing:
                with self.assertRaises(ValueError):
                    checker.run_worker(config)
                importing.assert_not_called()

    def test_main_only_launches_worker_and_maps_statuses(self):
        for status, code in checker.EXIT_CODES.items():
            report = dict(checker.base_report(self.config), status=status)
            completed = subprocess.CompletedProcess([], 0, json.dumps(report), '')
            with patch.object(checker.importlib, 'import_module', side_effect=AssertionError('parent import')):
                with patch.object(checker.subprocess, 'run', return_value=completed) as run:
                    with contextlib.redirect_stdout(io.StringIO()) as output:
                        self.assertEqual(checker.main(['--seed', '7', '--elements', '4']), code)
            self.assertEqual(json.loads(output.getvalue())['status'], status)
            command = run.call_args.args[0]
            self.assertEqual(command[0], sys.executable)
            self.assertEqual(command[2], '--_worker')
            self.assertEqual(json.loads(command[3]), self.config)
            self.assertEqual(run.call_args.kwargs['timeout'], 30)

    def test_worker_timeout_crash_and_invalid_output_fail_closed(self):
        with patch.object(checker.subprocess, 'run', side_effect=subprocess.TimeoutExpired('worker', 1)):
            self.assertEqual(checker.run_isolated(self.config, 1)['status'], 'error')
        for completed in (subprocess.CompletedProcess([], -9, '', 'driver error'),
                          subprocess.CompletedProcess([], 0, 'invalid json', ''),
                          subprocess.CompletedProcess([], 0, '{"status":"unknown"}', ''),
                          subprocess.CompletedProcess([], 0, '[]', '')):
            with patch.object(checker.subprocess, 'run', return_value=completed):
                self.assertEqual(checker.run_isolated(self.config, 1)['status'], 'error')

    def test_cli_rejects_unbounded_work_before_launching(self):
        for arguments in (['--elements', '0'], ['--elements', str(checker.MAX_ELEMENTS + 1)],
                          ['--repeats', '1'], ['--repeats', '9'], ['--timeout', '121'],
                          ['--seed', '-1'], ['--device', '-1']):
            with patch.object(checker.subprocess, 'run') as run:
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        checker.main(arguments)
                self.assertEqual(raised.exception.code, 2)
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
