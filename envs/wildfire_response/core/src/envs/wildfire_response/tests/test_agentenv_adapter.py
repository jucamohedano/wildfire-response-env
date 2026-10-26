import json
import unittest


try:
    from agentenv_protocol import AgentEnvEnvironment
    from envs.wildfire_response.agentenv.server import WildfireMCPEnvironment
    from envs.wildfire_response.regional.benchmark_v1 import BenchmarkEnv
    from envs.wildfire_response.regional.benchmark_v1.catalog import build_catalog
    from envs.wildfire_response.regional.ops.generator import digest
    from envs.wildfire_response.tests.test_prepared_development import fixture_inputs
    from envs.wildfire_response.tests.test_topographic_development import fixture_domain
    AVAILABLE = True
except ModuleNotFoundError as error:
    if error.name not in {"agentenv_protocol", "mcp"}:
        raise
    AVAILABLE = False


@unittest.skipUnless(AVAILABLE, "Install the optional AgentEnv adapter extra")
class AgentEnvAdapterTests(unittest.TestCase):
    def setUp(self):
        prepared = fixture_inputs()
        domain = fixture_domain(prepared)
        catalog = build_catalog(prepared, domain)
        case = next(entry for entry in catalog["development"] if entry["factors"]["family"] == "false_alarm")
        self.task_id = case["id"]
        self.environment = BenchmarkEnv(
            prepared,
            domain,
            expected_prepared_sha256=prepared["sha256"],
            expected_topographic_sha256=domain["sha256"],
            expected_catalog_sha256=catalog["sha256"],
            catalog=catalog,
            slope_enabled=True,
            allow_synthetic_conditions=True,
        )
        self.adapter = WildfireMCPEnvironment(
            environment=self.environment,
            allowed_task_ids=[self.task_id],
        )

    def test_registered_tools_are_mounted_on_agentenv_fastmcp(self):
        self.assertIsInstance(self.adapter, AgentEnvEnvironment)
        app = self.adapter.create_app()
        self.assertEqual(app.name, "wildfire-response-control-center")
        advertised = {tool.name for tool in app._tool_manager.list_tools()}
        self.assertEqual(advertised, {
            "reset_episode", "get_situation", "get_reports", "get_events", "get_action_schema", "check_action",
            "submit_action", "advance_time", "get_trajectory", "verify_episode",
        })

    def test_public_tools_check_without_advancing_and_verify_terminal_reward(self):
        reset = self.adapter.reset_episode(self.task_id)
        observation = reset["observation"]
        self.assertEqual(observation["minute"], 0)
        self.assertNotIn("landscape", observation)
        self.assertIn("terrain_at_sectors", observation)
        public = json.dumps(observation)
        for forbidden in ("private_audit", "evaluator_state", "future_events"):
            self.assertNotIn(forbidden, public)
        self.assertTrue(self.adapter.check_action({})["valid"])
        self.assertEqual(self.adapter.get_situation()["minute"], 0)
        schema = self.adapter.get_action_schema()
        self.assertNotIn("explanation", schema["properties"])
        self.assertIn("protection_orders", schema["properties"])
        self.assertEqual(self.adapter.get_reports(limit=20)["reports"], observation["reports"])
        for _ in range(60):
            result = self.adapter.advance_time()
        self.assertTrue(result["terminated"])
        self.assertFalse(result["truncated"])
        trajectory = self.adapter.get_trajectory()
        self.assertEqual(len(trajectory["transitions"]), 60)
        verified = self.adapter.verify_episode()
        self.assertTrue(verified["passed"])
        self.assertTrue(verified["suitable_for_training"])
        self.assertEqual(verified["reward"], sum(
            transition["objective_reward"] for transition in trajectory["transitions"]))

    def test_cannot_reset_to_a_task_outside_the_bundle_split(self):
        with self.assertRaises(ValueError):
            self.adapter.reset_episode("bench-test-not-in-development-bundle")

    def test_manifest_cannot_mix_splits(self):
        prepared = fixture_inputs()
        domain = fixture_domain(prepared)
        catalog = build_catalog(prepared, domain)
        train_task = next(case["id"] for group in catalog["groups"] if group["split"] == "train"
                          for case in group["cases"])
        with self.assertRaisesRegex(ValueError, "outside its registered split"):
            WildfireMCPEnvironment(
                prepared_inputs=prepared,
                topographic_inputs=domain,
                catalog_lock={
                    "prepared_inputs_sha256": prepared["sha256"],
                    "topographic_inputs_sha256": domain["sha256"],
                    "catalog_sha256": catalog["sha256"],
                },
                task_manifest={
                    "format_version": "wildfire-agentenv-hf-v1",
                    "split": "development",
                    "catalog_sha256": catalog["sha256"],
                    "task_ids": [train_task],
                },
            )


if __name__ == "__main__":
    unittest.main()
