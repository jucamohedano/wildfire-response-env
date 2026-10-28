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
    from envs.wildfire_response.regional.benchmark_v1 import BenchmarkEnv
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


if __name__ == '__main__':
    unittest.main()
