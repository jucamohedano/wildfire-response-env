"""Fail closed when a frozen regional benchmark input changes."""
import hashlib
import json
from pathlib import Path

from .data import PACK
from .environment import REWARD_VERSION, VERSION, WEIGHTS
from .scenarios import DATASET_VERSION, generate_tasks

BENCHMARK_VERSION = 'regional-benchmark-v0.2.0'
LOCK = Path(__file__).with_name('benchmark_lock.json')


def manifest():
    root = Path(__file__).resolve().parents[3]
    inputs = [PACK, *(Path(__file__).with_name(name) for name in (
        'benchmark.py', 'environment.py', 'fire.py', 'scenarios.py', 'baselines.py', 'verifier.py')),
        root/'simulator'/'engine.py', root/'simulator'/'terrain.py']
    tasks = generate_tasks()
    return dict(benchmark_version=BENCHMARK_VERSION, dataset_version=DATASET_VERSION,
        schema_version=VERSION, reward_version=REWARD_VERSION, weights=WEIGHTS,
        max_decisions=60, ticks_per_action=2,
        policies=['passive', 'random', 'blanket_warning', 'cooperative'],
        inputs={str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs},
        tasks_sha256=hashlib.sha256(json.dumps(tasks, sort_keys=True).encode()).hexdigest(),
        tasks=[{key: task[key] for key in ('id', 'split', 'group_id', 'seed', 'configuration_sha256')} for task in tasks],
        protocol='Frozen synthetic configurations; report held-out outcomes without tuning or filtering')


def verify_lock(path=LOCK):
    frozen = json.loads(Path(path).read_text(encoding='utf-8'))
    if frozen != manifest():
        raise ValueError('Frozen benchmark changed; review and version a new benchmark before evaluation')
    digest = hashlib.sha256(json.dumps(frozen, sort_keys=True).encode()).hexdigest()
    return dict(version=frozen['benchmark_version'], sha256=digest)
