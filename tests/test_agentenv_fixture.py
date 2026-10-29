import asyncio
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
try:
    import httpx
    from mcp.client.streamable_http import streamable_http_client
    from envs.wildfire_response.agentenv.server import WildfireMCPEnvironment
    from envs.wildfire_response.regional.benchmark_v1 import BenchmarkEnv, verify_benchmark_trajectory
    from envs.wildfire_response.regional.benchmark_v1.catalog import build_catalog, task_ids
    from envs.wildfire_response.tests.test_topographic_development import fixture_domain, fixture_inputs
except ImportError:
    httpx = None


@unittest.skipUnless(httpx, 'AgentEnv optional dependencies are unavailable')
class AgentEnvFixtureTests(unittest.TestCase):
    def test_driver_completes_synthetic_mcp_task(self):
        spec = importlib.util.spec_from_file_location('fixture_driver', ROOT / 'eval/agentenv_fixture_driver.py')
        driver = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(driver)
        prepared = fixture_inputs()
        domain = fixture_domain(prepared)
        catalog = build_catalog(prepared, domain)
        task = task_ids(catalog, 'development')[0]
        core = BenchmarkEnv(prepared, domain, expected_prepared_sha256=prepared['sha256'],
                            expected_topographic_sha256=domain['sha256'], catalog=catalog,
                            expected_catalog_sha256=catalog['sha256'], slope_enabled=True,
                            allow_synthetic_conditions=True, profile='central', max_decisions=60, ticks_per_action=2)
        server = WildfireMCPEnvironment(environment=core, allowed_task_ids=[task])
        app = server.create_app()
        http_app = app.streamable_http_app()

        async def exercise():
            async with app.session_manager.run():
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=http_app), base_url='http://local') as client:
                    driver.streamable_http_client = lambda url: streamable_http_client(url, http_client=client)
                    return await driver._run({'env': {'mcp_url': 'http://local/mcp'}, 'args': {'task_id': task}})
        result = asyncio.run(exercise())
        self.assertTrue(result['replay_passed'])
        self.assertFalse(result['model_used'])
        self.assertTrue(result['terminated'] or result['truncated'])
        self.assertEqual(result['decisions'], 60)
        self.assertEqual(result['end_minute'], 120)
        self.assertTrue(result['terminated'])
        self.assertFalse(result['truncated'])
        self.assertEqual(driver.canonical_hash(result['trajectory']), result['source_trajectory_sha256'])
        header = {key: value for key, value in result['trajectory'].items() if key != 'transitions'}
        self.assertEqual(driver.canonical_hash(header), result['source_header_sha256'])
        native_spec = importlib.util.spec_from_file_location('native_header', ROOT / 'eval/check_agentenv_native_fixture.py')
        native = importlib.util.module_from_spec(native_spec)
        native_spec.loader.exec_module(native)
        comparison = native.header_diagnostics(result['trajectory'], prepared, domain, catalog)
        self.assertEqual(comparison['source_header_sha256'], result['source_header_sha256'])
        self.assertEqual(comparison['differing_header_fields'], [])
        changed = dict(result['trajectory'])
        changed['implementation_sha256'] = '0' * 64
        comparison = native.header_diagnostics(changed, prepared, domain, catalog)
        self.assertEqual(comparison['differing_header_fields'], ['implementation_sha256'])
        try:
            verify_benchmark_trajectory(changed, prepared, domain, catalog=catalog)
        except ValueError as exc:
            self.assertEqual(native.safe_verifier_error(exc), 'Benchmark header or implementation pins differ')
        else:
            self.fail('changed implementation fingerprint must fail replay')
        import os
        import subprocess
        import sys
        program = r"""
import json, subprocess, sys
from pathlib import Path
from agentenv_hf import runs, dataset
from agentenv_hf.card import card
from agent_env.store.routing import namespace_routing
from agent_env.store.document_store import Filter, UpdateSpec
from agent_env.task.store import get_task_instance_store
root = Path(sys.argv[1])
repo = Path(sys.argv[2])
bundle_path = root / 'bundle'
subprocess.run([sys.executable, str(repo / 'eval/prepare_agentenv_fixture.py'), '--output', str(bundle_path)],
               cwd=repo, check=True, capture_output=True)
result = json.loads((root / 'driver-result.json').read_text())
bundle = runs.locate(str(bundle_path))
task = runs.tasks(bundle)[0]
with namespace_routing():
    store = get_task_instance_store()
    instance = store.create_instance(task.id, 1, 3)
    verification = {'wildfire_reward': {'score': result['verification']['reward']}}
    store._doc_store.update('task_instances', Filter.of(instance_id=instance.instance_id),
        UpdateSpec(set={'status':'completed', 'context': {'metadata': {'script_results': {'drive':result},
                        'verifications': verification}}, 'completed_steps': [
                        {'step_id':step, 'status':'success'} for step in ('deploy','drive','verify')]}))
    built = dataset.build(bundle, name='wildfire-ci', split='development', which='latest')
out = root / 'dataset'
for name, content in built.files.items():
    path = out / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
(out / 'README.md').write_text(card(None, name='wildfire-ci', split='development', repo=None,
                                   description='Synthetic fixture test', license=None))
sys.path.insert(0, str(repo / 'eval'))
from check_agentenv_native_fixture import export_and_check
report = export_and_check(out, bundle_path, debug_path=root / 'debug.json')
debug = json.loads((root / 'debug.json').read_text())
assert debug['public_trajectory'] == result['trajectory']
assert debug['driver_trace_sha256'] == debug['framework_trace_sha256']
assert debug['host_expected_header']['task_id'] == result['task_id']
assert 'critical_file_sha256' not in debug
assert report['status'] == 'passed' and report['decisions'] == 60
assert report['native_reward'] == result['verification']['reward']
assert report['independent_replay_after_dataset_load']
"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / 'driver-result.json').write_text(json.dumps(result))
            env = os.environ.copy()
            env.update(XDG_STATE_HOME=str(tmp_path / 'state'), XDG_CACHE_HOME=str(tmp_path / 'cache'),
                       HF_HOME=str(tmp_path / 'hf'), HF_HUB_OFFLINE='1', HF_DATASETS_OFFLINE='1')
            execution = subprocess.run([sys.executable, '-c', program, tmp, str(ROOT)], cwd=ROOT, env=env,
                                       capture_output=True, text=True, timeout=60)
            self.assertEqual(execution.returncode, 0, execution.stderr[-4000:])


if __name__ == '__main__':
    unittest.main()
