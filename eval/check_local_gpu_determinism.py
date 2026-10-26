"""Optional local integer CUDA smoke check; no training or simulation dependency.

Run: python eval/check_local_gpu_determinism.py
Exit codes: 0 pass, 1 mismatch, 2 unavailable, 3 error/timeout.
Only the disposable worker imports torch or changes its settings. Inputs are
seeded with a private standard-library RNG on the CPU and copied to CUDA.
"""

import argparse
import hashlib
import importlib
import json
from pathlib import Path
import random
import struct
import subprocess
import sys


MAX_ELEMENTS = 65536
MAX_REPEATS = 8
TENSOR_DATA_CAP_BYTES = 8 * 1024 * 1024
ALLOCATOR_CAP_BYTES = 64 * 1024 * 1024
EXIT_CODES = {'pass': 0, 'fail': 1, 'unavailable': 2, 'error': 3}
SCOPE = (
    'Exact integer elementwise CUDA repeatability and independent Python CPU '
    'reference agreement for these seeded inputs only. No floating-point, '
    'cross-hardware, GPU RNG, or general determinism claim. '
    'Not GPU simulation acceleration or model quality; no model or training.'
)


def bounded_integer(minimum, maximum):
    def parse(value):
        number = int(value)
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(f'Expected {minimum}..{maximum}')
        return number
    return parse


def validate_config(config):
    limits = dict(seed=(0, 2**32 - 1), elements=(1, MAX_ELEMENTS),
                  repeats=(2, MAX_REPEATS), device=(0, 255))
    if set(config) != set(limits):
        raise ValueError('Invalid worker configuration keys')
    for name, (minimum, maximum) in limits.items():
        value = config[name]
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f'Invalid worker configuration: {name}')
    if config['elements'] * 8 * 12 > TENSOR_DATA_CAP_BYTES:
        raise ValueError('Tensor data exceeds the cap')


def make_inputs(seed, elements):
    generator = random.Random(seed)
    left = [generator.randrange(-1024, 1024) for _ in range(elements)]
    right = [generator.randrange(-1024, 1024) for _ in range(elements)]
    return left, right


def cpu_reference(left, right):
    if len(left) != len(right):
        raise ValueError('Input lengths differ')
    return [((first + second) * (first - second)) & 65535
            for first, second in zip(left, right)]


def result_checksum(values):
    digest = hashlib.sha256(b'local-gpu-integer-v1\0')
    digest.update(struct.pack('<Q', len(values)))
    for value in values:
        digest.update(struct.pack('<q', value))
    return digest.hexdigest()


def compare_results(expected, results):
    baseline = None
    repeatability = True
    reference_match = True
    runs = 0
    for result in results:
        runs += 1
        if baseline is None:
            baseline = list(result)
        repeatability = repeatability and result == baseline
        reference_match = reference_match and result == expected
    if runs < 2:
        raise ValueError('At least two GPU results are required')
    return dict(status='pass' if repeatability and reference_match else 'fail',
                repeatability_exact=repeatability, cpu_reference_exact=reference_match,
                runs_completed=runs, result_sha256=result_checksum(baseline),
                checksum_encoding='local-gpu-integer-v1 NUL, uint64 length, int64 values; little-endian')


def base_report(config):
    return dict(version='local-gpu-determinism-v1', scope=SCOPE, config=config,
                torch_version=None, cuda_version=None, gpu=None,
                tensor_data_cap_bytes=TENSOR_DATA_CAP_BYTES,
                conservative_tensor_data_bound_bytes=config['elements'] * 8 * 12,
                allocator_cap_bytes=ALLOCATOR_CAP_BYTES,
                memory_scope='Tensor data bound and torch allocator cap; CUDA context/driver memory excluded',
                repeatability_exact=None, cpu_reference_exact=None, result_sha256=None)


def run_worker(config):
    validate_config(config)
    report = base_report(config)
    try:
        torch = importlib.import_module('torch')
    except (ImportError, OSError) as error:
        return dict(report, status='unavailable', reason=f'torch unavailable: {error}')
    report.update(torch_version=str(torch.__version__), cuda_version=torch.version.cuda)
    try:
        if not torch.cuda.is_available():
            return dict(report, status='unavailable', reason='CUDA is unavailable to torch')
        if config['device'] >= torch.cuda.device_count():
            return dict(report, status='unavailable', reason='Requested CUDA device is unavailable')
        properties = torch.cuda.get_device_properties(config['device'])
        report['gpu'] = dict(index=config['device'], name=properties.name,
                             total_memory_bytes=properties.total_memory,
                             compute_capability=[properties.major, properties.minor])
        torch.cuda.set_device(config['device'])
    except Exception as error:
        return dict(report, status='unavailable', reason=f'CUDA initialization unavailable: {error}')
    try:
        cap = min(ALLOCATOR_CAP_BYTES, properties.total_memory)
        report['allocator_cap_bytes'] = cap
        torch.cuda.set_per_process_memory_fraction(cap / properties.total_memory,
                                                  config['device'])
        torch.use_deterministic_algorithms(True)
        report['deterministic_algorithms'] = True
        left, right = make_inputs(config['seed'], config['elements'])
        expected = cpu_reference(left, right)
        device = f"cuda:{config['device']}"
        left_tensor = torch.tensor(left, dtype=torch.int64, device=device)
        right_tensor = torch.tensor(right, dtype=torch.int64, device=device)

        def results():
            for _ in range(config['repeats']):
                output = ((left_tensor + right_tensor) * (left_tensor - right_tensor)) & 65535
                torch.cuda.synchronize(config['device'])
                yield output.cpu().tolist()
                del output

        report.update(compare_results(expected, results()))
        report['allocator_peak_bytes'] = torch.cuda.max_memory_allocated(config['device'])
        return report
    except Exception as error:
        return dict(report, status='error', reason=f'CUDA smoke check failed: {error}')


def run_isolated(config, timeout):
    validate_config(config)
    report = base_report(config)
    command = [sys.executable, str(Path(__file__).resolve()), '--_worker', json.dumps(config)]
    try:
        completed = subprocess.run(command, capture_output=True, text=True,
                                   timeout=timeout, check=False)
        if completed.returncode != 0:
            return dict(report, status='error', reason='Worker exited abnormally',
                        worker_returncode=completed.returncode,
                        worker_stderr=completed.stderr[-2000:])
        result = json.loads(completed.stdout)
        if not isinstance(result, dict) or result.get('status') not in EXIT_CODES:
            raise ValueError('Invalid worker status')
        return result
    except subprocess.TimeoutExpired:
        return dict(report, status='error', reason=f'Worker timed out after {timeout} seconds')
    except (OSError, ValueError) as error:
        return dict(report, status='error', reason=f'Worker failed: {error}')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, epilog=SCOPE)
    parser.add_argument('--seed', type=bounded_integer(0, 2**32 - 1), default=2026)
    parser.add_argument('--elements', type=bounded_integer(1, MAX_ELEMENTS), default=4096)
    parser.add_argument('--repeats', type=bounded_integer(2, MAX_REPEATS), default=3)
    parser.add_argument('--device', type=bounded_integer(0, 255), default=0)
    parser.add_argument('--timeout', type=bounded_integer(1, 120), default=30,
                        help='Worker wall-clock limit in seconds (default: 30)')
    args = parser.parse_args(argv)
    config = dict(seed=args.seed, elements=args.elements, repeats=args.repeats, device=args.device)
    result = run_isolated(config, args.timeout)
    print(json.dumps(result, indent=2, sort_keys=True))
    return EXIT_CODES[result['status']]


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--_worker':
        print(json.dumps(run_worker(json.loads(sys.argv[2]))))
    else:
        raise SystemExit(main())
