import json
import unittest


try:
    from agent_env.task_step.task_steps.deploy_agent import DeployAgentTaskStep
    from agent_env.task_step.task_steps.deploy_env import DeployEnvTaskStep
    from agent_env.task_step.task_steps.prompt_agent import PromptAgentTaskStep
    from agent_env.task_step.task_steps.verifiers.env_outcome_verifier import EnvOutcomeVerifierTaskStep
    from envs.wildfire_response.agentenv.export import _contains_private_key, _task_steps
    AVAILABLE = True
except ModuleNotFoundError as error:
    if error.name not in {"agent_env", "agentenv_hf", "pyarrow"}:
        raise
    AVAILABLE = False


@unittest.skipUnless(AVAILABLE, "Install the optional AgentEnv HF export extra")
class AgentEnvExportTests(unittest.TestCase):
    def test_task_dag_uses_agentenv_steps_and_only_the_registered_task_id(self):
        task_id = "bench-dev-71f777cb5ff8c9f2"
        steps = _task_steps(task_id)
        constructors = {
            "deploy_env": DeployEnvTaskStep,
            "deploy_agent": DeployAgentTaskStep,
            "prompt_agent": PromptAgentTaskStep,
            "env_outcome_verifier": EnvOutcomeVerifierTaskStep,
        }
        parsed = [constructors[step["type"]].from_dict(step) for step in steps]
        self.assertEqual([step.id for step in parsed], ["deploy", "agent", "control", "verify"])
        self.assertEqual(steps[-1]["score_aggregator"], "weighted_average")
        prompt_text = json.dumps(steps, ensure_ascii=False)
        self.assertIn(task_id, prompt_text)
        for private_field in ("future_events", "scenario_factors", "ground_truth", "evaluator_state"):
            self.assertNotIn(private_field, prompt_text)

    def test_recursive_trace_scan_rejects_private_nested_fields(self):
        self.assertFalse(_contains_private_key({"observation": {"reports": [{"id": "public"}]}}))
        self.assertTrue(_contains_private_key({"observation": {"scenario": {"future_events": []}}}))


if __name__ == "__main__":
    unittest.main()
