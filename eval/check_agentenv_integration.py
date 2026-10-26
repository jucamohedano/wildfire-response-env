"""Replay one public benchmark trace through the AgentEnv MCP adapter."""

from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
import logging
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from envs.wildfire_response.agentenv.export import _json, _repo_root
from envs.wildfire_response.agentenv.server import WildfireMCPEnvironment
from envs.wildfire_response.regional.benchmark_v1 import verify_benchmark_trajectory
from envs.wildfire_response.regional.benchmark_v1.catalog import build_catalog
from envs.wildfire_response.regional.benchmark_v1.model import public_context as compact_observation
from envs.wildfire_response.regional.ops.generator import digest
from envs.wildfire_response.regional.prepared.data import load_inputs
from envs.wildfire_response.regional.topographic.data import load_domain


async def _call(session: ClientSession, name: str, arguments: dict | None = None) -> dict:
    response = await session.call_tool(name, arguments or {})
    text = "".join(getattr(part, "text", "") for part in response.content)
    if response.isError:
        raise RuntimeError(f"MCP tool {name} failed: {text[:500]}")
    result = json.loads(text)
    if not isinstance(result, dict):
        raise ValueError(f"MCP tool {name} did not return a JSON object")
    return result


async def check(prepared_path: Path, domain_path: Path, trajectory_path: Path) -> dict:
    logging.getLogger("mcp").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    repo = _repo_root()
    lock_path = repo / "data/benchmark_v1/catalog_lock.json"
    lock = _json(lock_path)
    prepared = load_inputs(prepared_path, expected_sha256=lock["prepared_inputs_sha256"])
    domain = load_domain(domain_path, prepared, lock["topographic_inputs_sha256"])
    catalog = build_catalog(prepared, domain)
    if catalog["sha256"] != lock["catalog_sha256"]:
        raise ValueError("Prepared inputs, terrain, and catalog do not match the frozen release lock")
    with gzip.open(trajectory_path, "rt", encoding="utf-8") as stream:
        expected = json.load(stream)
    replay = verify_benchmark_trajectory(expected, prepared, domain, catalog=catalog)
    if not replay["passed"]:
        raise ValueError("Source trajectory failed the independent replay check")
    task_id = expected.get("task_id")
    if not isinstance(task_id, str) or not task_id.startswith("bench-dev-"):
        raise ValueError("The integration smoke accepts development traces only")

    task_manifest = {
        "format_version": "wildfire-agentenv-hf-v1",
        "split": "development",
        "catalog_sha256": lock["catalog_sha256"],
        "task_ids": [task_id],
    }
    server = WildfireMCPEnvironment(
        prepared_inputs=prepared,
        topographic_inputs=domain,
        catalog_lock=lock,
        task_manifest=task_manifest,
    )
    fastmcp = server.create_app()
    http_app = fastmcp.streamable_http_app()
    async with fastmcp.session_manager.run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=http_app),
            base_url="http://wildfire-agentenv.local",
        ) as client:
            async with streamable_http_client(
                "http://wildfire-agentenv.local/mcp", http_client=client
            ) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = {tool.name for tool in (await session.list_tools()).tools}
                    required = {"reset_episode", "get_situation", "get_reports", "get_events", "get_action_schema",
                                "check_action", "submit_action", "advance_time", "get_trajectory", "verify_episode"}
                    if not required <= tools:
                        raise AssertionError(f"MCP tools missing: {sorted(required - tools)}")
                    reset = await _call(session, "reset_episode", {"task_id": task_id})
                    current = reset["observation"]
                    if digest(current) != digest(compact_observation(expected["initial_observation"])):
                        raise AssertionError("MCP reset did not produce the expected opening observation")
                    serialized = json.dumps(current, ensure_ascii=False)
                    if any(marker in serialized for marker in ("private_audit", "evaluator_state", "future_events")):
                        raise AssertionError("MCP reset leaked private or future state")
                    check_result = await _call(session, "check_action", {"action": {}})
                    if not check_result.get("valid") or (await _call(session, "get_situation"))["minute"] != 0:
                        raise AssertionError("Action check advanced time or rejected the empty action")
                    schema = await _call(session, "get_action_schema")
                    if "explanation" in schema.get("properties", {}):
                        raise AssertionError("Action schema advertises an unsupported explanation field")
                    for index, transition in enumerate(expected["transitions"]):
                        result = await _call(session, "submit_action", {"action": transition["action"]})
                        if digest(result["observation"]) != digest(compact_observation(transition["observation"])):
                            raise AssertionError(f"MCP observation differs from recorded transition {index}")
                        if result["reward"] != transition["objective_reward"]:
                            raise AssertionError(f"MCP reward differs from recorded transition {index}")
                        if (result["terminated"], result["truncated"]) != (
                                transition["terminated"], transition["truncated"]):
                            raise AssertionError(f"MCP terminal flags differ from recorded transition {index}")
                    actual = await _call(session, "get_trajectory")
                    if digest(actual) != digest(expected):
                        raise AssertionError("MCP trajectory differs from the source trace")
                    verification = await _call(session, "verify_episode")
                    if not verification.get("passed") or not verification.get("suitable_for_training"):
                        raise AssertionError("MCP terminal outcome did not pass exact replay verification")
                    if verification["reward"] != replay["objective_score"]:
                        raise AssertionError("MCP verifier reward differs from source replay")
    return {
        "passed": True,
        "task_id": task_id,
        "source_sha256": hashlib.sha256(trajectory_path.read_bytes()).hexdigest(),
        "transitions": len(expected["transitions"]),
        "replayed_by_mcp": True,
        "terminal_reward": verification["reward"],
        "suitable_for_training": verification["suitable_for_training"],
        "scope": "Local MCP adapter parity; not an AgentEnv Docker deployment or hosted run.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared-inputs", required=True, type=Path)
    parser.add_argument("--topographic-inputs", required=True, type=Path)
    parser.add_argument("--trajectory", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    result = asyncio.run(check(args.prepared_inputs, args.topographic_inputs, args.trajectory))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
