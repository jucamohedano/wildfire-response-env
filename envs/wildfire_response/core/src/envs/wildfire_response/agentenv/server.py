"""MCP adapter for the pinned wildfire benchmark used by AgentEnv."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Annotated

from pydantic import Field

from agentenv_protocol import AgentEnvEnvironment, environment_card, tool

from ..regional.benchmark_v1 import BenchmarkEnv
from ..regional.benchmark_v1.catalog import build_catalog, task_ids
from ..regional.benchmark_v1.model import public_context as compact_observation
from ..regional.ops.compact_model import action_schema as schema_for_action
from ..regional.prepared.data import load_inputs
from ..regional.topographic.data import load_domain


class WildfireAgentEnv(AgentEnvEnvironment):
    def __init__(
        self,
        *,
        prepared_inputs: str | Path | dict | None = None,
        topographic_inputs: str | Path | dict | None = None,
        catalog_lock: str | Path | dict | None = None,
        task_manifest: str | Path | dict | None = None,
        allowed_task_ids: list[str] | tuple[str, ...] | None = None,
        environment: BenchmarkEnv | None = None,
    ) -> None:
        self._environment = environment
        self._allowed_task_ids = set(allowed_task_ids or ())
        if environment is not None:
            if not self._allowed_task_ids:
                self._allowed_task_ids = set(task_ids(environment.catalog, "development"))
            return

        data_dir = Path(os.environ.get("WILDFIRE_DATA_DIR", "/app/data"))
        prepared_inputs = prepared_inputs or os.environ.get(
            "WILDFIRE_PREPARED_INPUTS", data_dir / "prepared_inputs.json"
        )
        topographic_inputs = topographic_inputs or os.environ.get(
            "WILDFIRE_TOPOGRAPHIC_INPUTS", data_dir / "topographic_inputs.json"
        )
        catalog_lock = catalog_lock or os.environ.get(
            "WILDFIRE_CATALOG_LOCK", data_dir / "catalog_lock.json"
        )
        lock = self._read_json(catalog_lock)
        prepared = load_inputs(prepared_inputs, expected_sha256=lock["prepared_inputs_sha256"])
        domain = load_domain(
            topographic_inputs,
            prepared,
            lock["topographic_inputs_sha256"],
        )
        catalog = build_catalog(prepared, domain)
        if catalog["sha256"] != lock["catalog_sha256"]:
            raise ValueError("Runtime task catalog differs from the checked-in catalog lock")
        task_manifest = task_manifest or os.environ.get(
            "WILDFIRE_TASK_MANIFEST", data_dir / "task_manifest.json"
        )
        task_manifest = self._read_json(task_manifest)
        split = task_manifest.get("split")
        if split not in {"development", "train", "validation", "test"}:
            raise ValueError("Task manifest must name one benchmark split")
        if task_manifest.get("format_version") != "wildfire-agentenv-hf-v1":
            raise ValueError("Unsupported AgentEnv task manifest version")
        if task_manifest.get("catalog_sha256") != lock["catalog_sha256"]:
            raise ValueError("Task manifest and catalog lock differ")
        self._allowed_task_ids = set(task_manifest.get("task_ids", []))
        if not self._allowed_task_ids:
            raise ValueError("Task manifest must allow at least one task")
        valid_ids = set(task_ids(catalog, split))
        if not self._allowed_task_ids <= valid_ids:
            raise ValueError("Task manifest contains IDs outside its registered split")
        self._environment = BenchmarkEnv(
            prepared,
            domain,
            expected_prepared_sha256=lock["prepared_inputs_sha256"],
            expected_topographic_sha256=lock["topographic_inputs_sha256"],
            expected_catalog_sha256=lock["catalog_sha256"],
            catalog=catalog,
            slope_enabled=True,
            allow_synthetic_conditions=True,
            profile="central",
            max_decisions=60,
            ticks_per_action=2,
        )

    @staticmethod
    def _read_json(value: str | Path | dict) -> dict:
        if isinstance(value, dict):
            return value
        with Path(value).open(encoding="utf-8") as stream:
            result = json.load(stream)
        if not isinstance(result, dict):
            raise ValueError("Expected a JSON object")
        return result

    @property
    def core(self) -> BenchmarkEnv:
        if self._environment is None:
            raise RuntimeError("Wildfire environment initialization failed")
        return self._environment

    def _require_finished(self) -> None:
        core = self.core._core
        if core.sim is None or not (core.terminated or core.truncated):
            raise RuntimeError("Finish or truncate the episode before requesting its trajectory or verification")

    @tool(description="Start a registered synthetic wildfire benchmark task. This resets the episode and returns the opening public observation.")
    def reset_episode(
        self,
        task_id: Annotated[str, Field(description="Task ID from the AgentEnv prompt.")],
    ) -> dict:
        if task_id not in self._allowed_task_ids:
            raise ValueError("Task is not in this bundle's registered split")
        observation, info = self.core.reset(task_id)
        return {"observation": compact_observation(observation), "info": info}

    @tool(description="Read the current public wildfire control-center situation without advancing simulated time.")
    def get_situation(self) -> dict:
        return compact_observation(self.core.get_situation())

    @tool(description="Read already received reports by public IDs, with a bounded result size.")
    def get_reports(
        self,
        report_ids: Annotated[list[str] | None, Field(description="Optional received report IDs.")] = None,
        limit: Annotated[int, Field(ge=1, le=100, description="Maximum number of reports.")] = 20,
    ) -> dict:
        return {"reports": self.core.get_reports(report_ids, limit=limit)}

    @tool(description="Read already delivered incident events. Cursors and IDs only address received events.")
    def get_events(
        self,
        after_cursor: Annotated[int, Field(ge=0, description="Index into the delivered-event list.")] = 0,
        limit: Annotated[int, Field(ge=1, le=100, description="Maximum number of events.")] = 20,
    ) -> dict:
        events = self.core.get_events(after_cursor=after_cursor, limit=limit)
        return {"events": events, "next_cursor": after_cursor + len(events)}

    @tool(description="Validate a complete candidate action without committing it or advancing time.")
    def check_action(
        self,
        action: Annotated[dict, Field(description="Candidate action using the observation's action schema.")],
    ) -> dict:
        return self.core.check_orders(action)

    @tool(description="Return the current legal structured-action schema and available public IDs without advancing time.")
    def get_action_schema(self) -> dict:
        schema = schema_for_action(self.core.get_situation(), tool="submit_action")
        schema["properties"].pop("explanation", None)
        return schema

    @tool(description="Commit one complete structured control-center action. A valid or invalid commit advances two simulated minutes.")
    def submit_action(
        self,
        action: Annotated[dict, Field(description="Complete action using the observation's action schema.")],
    ) -> dict:
        observation, reward, terminated, truncated, info = self.core.submit_action(action)
        return {"observation": compact_observation(observation), "reward": reward, "terminated": terminated,
                "truncated": truncated, "info": info}

    @tool(description="Commit an empty action and advance the simulator by two minutes.")
    def advance_time(self) -> dict:
        observation, reward, terminated, truncated, info = self.core.advance()
        return {"observation": compact_observation(observation), "reward": reward, "terminated": terminated,
                "truncated": truncated, "info": info}

    @tool(description="Export the full public trajectory after the episode has ended. Intended for an external collector, not routine policy turns; private evaluator state is never returned.")
    def get_trajectory(self) -> dict:
        self._require_finished()
        return self.core.trajectory()

    @tool(description="Independently replay and verify the completed public trajectory, returning its programmatic terminal score.")
    def verify_episode(self) -> dict:
        self._require_finished()
        result = self.core.verify_episode()
        outcomes = result["outcomes"]
        return {
            "passed": result["passed"],
            "reward": result["objective_score"],
            "reward_version": result["reward_version"],
            "boundary_censored": result["boundary_censored"],
            "suitable_for_training": result["suitable_for_training"],
            "safe_success": outcomes["safe_success"],
            "protected_population_fraction": outcomes["protected_population_fraction"],
            "unburned_fraction": outcomes["unburned_fraction"],
        }


@environment_card(name="wildfire-response-control-center")
class WildfireMCPEnvironment(WildfireAgentEnv):
    pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=("streamable-http", "stdio"),
                        default=os.environ.get("WILDFIRE_MCP_TRANSPORT", "streamable-http"))
    args = parser.parse_args()
    WildfireMCPEnvironment().serve(transport=args.transport)


if __name__ == "__main__":
    main()
