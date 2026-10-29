"""Build local AgentEnv bundles and Hugging Face tables from the frozen benchmark."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import tempfile
import tomllib
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from agent_env.bundle.parse import BundleKind, parse_bundle
from agentenv_hf.card import card as dataset_card
from agentenv_hf.records import Rewrite
from agentenv_hf.scan import known_values, scan
from agentenv_hf import tables as agentenv_tables

from ..regional.benchmark_v1.catalog import build_catalog, task_ids
from ..regional.benchmark_v1.environment import verify_benchmark_trajectory
from ..regional.ops.generator import digest
from ..regional.prepared.data import load_inputs
from ..regional.topographic.data import load_domain
from .policy_prompt import POLICY_PROMPT


SPLITS = ("development", "train", "validation", "test")
FORMAT_VERSION = "wildfire-agentenv-hf-v1"
PRIVATE_TRACE_KEYS = frozenset({
    "private_audit", "evaluator_state", "future_events", "ground_truth", "scenario_config",
    "scenario_factors", "truth_labels", "generation_annotations",
})
TRAJECTORY_EPISODES = pa.schema([
    ("episode_id", pa.string()), ("task_id", pa.string()), ("split", pa.string()),
    ("run_id", pa.string()), ("model", pa.string()), ("trajectory_sha256", pa.string()),
    ("decisions", pa.int64()), ("end_minute", pa.int64()), ("terminated", pa.bool_()),
    ("truncated", pa.bool_()), ("reward_version", pa.string()), ("final_reward", pa.float64()),
    ("replay_verified", pa.bool_()), ("training_eligible", pa.bool_()),
])
TRAJECTORY_TRANSITIONS = pa.schema([
    ("episode_id", pa.string()), ("task_id", pa.string()), ("split", pa.string()),
    ("step", pa.int64()), ("minute", pa.int64()), ("next_minute", pa.int64()),
    ("observation", pa.json_()), ("action", pa.json_()), ("next_observation", pa.json_()),
    ("reward", pa.float64()), ("action_valid", pa.bool_()),
    ("terminated", pa.bool_()), ("truncated", pa.bool_()),
])


def _repo_root() -> Path:
    explicit = os.environ.get("WILDFIRE_SOURCE_ROOT")
    candidates = [Path(explicit).resolve()] if explicit else []
    candidates.extend(Path(__file__).resolve().parents)
    for candidate in candidates:
        if ((candidate / "data/benchmark_v1/catalog_lock.json").is_file()
                and (candidate / "envs/wildfire_response/core/src").is_dir()):
            return candidate
    raise ValueError("Cannot locate the wildfire repository; set WILDFIRE_SOURCE_ROOT")


def _json(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        result = json.load(stream)
    if not isinstance(result, dict):
        raise ValueError(f"Expected a JSON object in {path.name}")
    return result


def _write_parquet(path: Path, rows: list[dict], schema: pa.Schema) -> None:
    storage = pa.schema([
        pa.field(field.name, field.type.storage_type if isinstance(field.type, pa.BaseExtensionType) else field.type)
        for field in schema
    ])
    encoded = []
    for row in rows:
        converted = {}
        for field in schema:
            value = row.get(field.name)
            if value is not None and isinstance(field.type, pa.BaseExtensionType):
                value = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
            converted[field.name] = value
        encoded.append(converted)
    table = pa.Table.from_pylist(encoded, schema=storage).cast(schema)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path, compression="zstd")


def _step_dependencies(step_id: str) -> list[dict]:
    return [{"task_step_id": step_id}]


def _task_steps(task_id: str) -> list[dict]:
    return [
        {"id": "deploy", "type": "deploy_env", "env_id": "wildfire", "sandbox_type": "local"},
        {"id": "agent", "type": "deploy_agent", "a2a_agent_id": "claude-code",
         "agent_name": "dispatcher", "env_ids": ["wildfire"], "depends_on": _step_dependencies("deploy")},
        {"id": "control", "type": "prompt_agent", "prompt_id": "control", "agent_name": "dispatcher",
         "system_prompt": POLICY_PROMPT,
         "prompt": (f"Run the wildfire benchmark task {task_id}. Start by calling reset_episode with exactly "
                    f"task_id={task_id!r}. Follow the public observations and continue until the episode ends."),
         "max_turns": 240, "timeout_seconds": 1800, "depends_on": _step_dependencies("agent")},
        {"id": "verify", "type": "env_outcome_verifier", "env_id": "wildfire",
         "file_artifact_id": "verify_wildfire", "verifier_id": "wildfire_reward",
         "score_aggregator": "weighted_average", "depends_on": _step_dependencies("control")},
    ]


def _write_bundle(bundle_root: Path, split: str, identifiers: list[str], prepared: dict,
                  domain: dict, lock: dict, repo: Path) -> None:
    bundle_root.mkdir(parents=True)
    for name in ("tasks", "evals", "envs/wildfire", "artifacts/verify_wildfire"):
        (bundle_root / name).mkdir(parents=True, exist_ok=True)
    for identifier in identifiers:
        task_path = bundle_root / "tasks" / f"{identifier}.json"
        task_path.write_text(json.dumps(_task_steps(identifier), ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    (bundle_root / "evals" / f"{split}.toml").write_text(
        "type = \"eval\"\n"
        f"tasks = {json.dumps(identifiers, ensure_ascii=False)}\n",
        encoding="utf-8",
    )
    public_manifest = {
        "format_version": FORMAT_VERSION,
            "catalog_status": "archived",
            "archival_use_acknowledged": True,
            "active_incident_day_training_corpus": False,
        "split": split,
        "catalog_sha256": lock["catalog_sha256"],
        "task_ids": identifiers,
    }
    data_dir = bundle_root / "envs/wildfire/data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "prepared_inputs.json").write_text(
        json.dumps(prepared, ensure_ascii=False, sort_keys=True, allow_nan=False), encoding="utf-8")
    (data_dir / "topographic_inputs.json").write_text(
        json.dumps(domain, ensure_ascii=False, sort_keys=True, allow_nan=False), encoding="utf-8")
    (data_dir / "catalog_lock.json").write_text(
        json.dumps(lock, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (data_dir / "task_manifest.json").write_text(
        json.dumps(public_manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    app_root = bundle_root / "envs/wildfire"
    (app_root / "server.py").write_text(
        "from envs.wildfire_response.agentenv.server import main\n\nif __name__ == \"__main__\":\n    main()\n",
        encoding="utf-8",
    )
    (app_root / "requirements.txt").write_text(
        "agentenv-framework-protocol[agent]==0.1.373\n",
        encoding="utf-8",
    )
    (app_root / "Dockerfile").write_text(
        "FROM python:3.12-slim\n"
        "WORKDIR /app\n"
        "COPY requirements.txt .\n"
        "RUN pip install --no-cache-dir -r requirements.txt\n"
        "COPY src/ ./src/\n"
        "COPY data/ ./data/\n"
        "COPY server.py ./server.py\n"
        "ENV PYTHONPATH=/app/src\n"
        "CMD [\"python\", \"server.py\"]\n",
        encoding="utf-8",
    )
    source = repo / "envs/wildfire_response/core/src"
    shutil.copytree(source, app_root / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info"))
    (bundle_root / "artifacts/verify_wildfire/verify.py").write_text(
        """import json
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def verify(mcp_url):
    async with streamable_http_client(mcp_url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            response = await session.call_tool("verify_episode")
    body = "".join(getattr(part, "text", "") for part in response.content)
    if response.isError:
        return [{"id": "wildfire-replay", "result": False, "description": body[:300]}]
    result = json.loads(body)
    passed = result.get("passed") is True and result.get("suitable_for_training") is True
    return [{"id": "wildfire-reward", "result": passed,
             "score": float(result.get("reward", 0.0)) if passed else 0.0,
             "description": "Exact public-trace replay; boundary-censored or unverified runs receive zero."}]
""",
        encoding="utf-8",
    )
    (bundle_root / "README.md").write_text(
        f"# Wildfire AgentEnv bundle: {split}\n\n"
        f"Contains {len(identifiers)} registered {split} tasks for the synthetic wildfire control-center benchmark. "
        "Task files include IDs and instructions only; the simulator resolves scenario configuration privately. "
        "The bundle includes pinned runtime map inputs for local evaluation. Redistribution rights are not cleared; "
        "do not publish this generated bundle until the repository license and input-data terms are resolved.\n",
        encoding="utf-8",
    )


def _bundle_files(root: Path):
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if (path.is_file() and not any(part.startswith(".") or part == "__pycache__" for part in relative.parts)
                and path.suffix != ".pyc"):
            yield relative.as_posix(), path.read_bytes()


def _task_rows(bundle_root: Path, name: str) -> list[dict]:
    bundle = parse_bundle(bundle_root)
    tasks = [entry for entry in bundle.entries if entry.kind is BundleKind.TASK]
    by_id = {entry.id: entry.name for entry in tasks}
    evals: dict[str, list[str]] = {}
    for entry in bundle.entries:
        if entry.kind is not BundleKind.EVAL:
            continue
        config = entry.config or tomllib.loads(entry.path.read_text(encoding="utf-8"))
        for reference in config.get("tasks", []):
            task_name = reference if reference in {item.name for item in tasks} else by_id.get(reference)
            if task_name:
                evals.setdefault(task_name, []).append(entry.name)
    rewrite = Rewrite(bundle.id_root, name)
    return [rewrite(agentenv_tables.task_row(entry.name, entry.config, entry.id, evals.get(entry.name, [])))
            for entry in tasks]


def _contains_private_key(value) -> bool:
    if isinstance(value, dict):
        return bool(PRIVATE_TRACE_KEYS & value.keys()) or any(_contains_private_key(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_private_key(item) for item in value)
    return False


def _trace_exports(roots: list[Path], task_splits: dict[str, str], prepared: dict, domain: dict,
                   catalog: dict) -> tuple[list[dict], list[dict], list[dict]]:
    episodes, transitions, raw_records = [], [], []
    seen: set[tuple[str, str, str]] = set()
    for root in roots:
        if root.is_symlink() or not root.is_dir():
            raise ValueError(f"Trajectory root must be a real directory: {root}")
        actor_manifest_path = root / "actor_manifest.json"
        actor_manifest = _json(actor_manifest_path) if actor_manifest_path.is_file() else {}
        model = actor_manifest.get("requested_model")
        for path in sorted(root.rglob("model/trajectory.json.gz")):
            if path.is_symlink():
                raise ValueError("Trajectory files must not be symlinks")
            with gzip.open(path, "rt", encoding="utf-8") as stream:
                trajectory = json.load(stream)
            task_id = trajectory.get("task_id")
            if task_splits.get(task_id) != "development":
                raise ValueError("Only development traces may be imported; training/validation/test stay held out")
            verification = verify_benchmark_trajectory(trajectory, prepared, domain, catalog=catalog)
            if not verification["passed"]:
                raise ValueError(f"Trajectory failed exact replay: {task_id}")
            run_id = path.parent.parent.name
            identity = (run_id, task_id, str(model or ""))
            if identity in seen:
                raise ValueError(f"Duplicate trajectory source: {identity}")
            seen.add(identity)
            episode_id = "/".join((root.name, run_id, task_id, "model"))
            compressed_sha = hashlib.sha256(path.read_bytes()).hexdigest()
            recorded = trajectory.get("transitions", [])
            initial = trajectory.get("initial_observation")
            if not isinstance(initial, dict) or not recorded:
                raise ValueError("Trajectory is missing its public initial observation or transitions")
            if _contains_private_key(trajectory):
                raise ValueError("Trajectory contains evaluator-only or future scenario fields")
            last = recorded[-1]
            episodes.append(dict(
                episode_id=episode_id, task_id=task_id, split="development", run_id=run_id,
                model=model, trajectory_sha256=compressed_sha, decisions=len(recorded),
                end_minute=last["observation"].get("minute"), terminated=last["terminated"],
                truncated=last["truncated"], reward_version=trajectory.get("reward_version"),
                final_reward=sum(float(record.get("objective_reward", 0.0)) for record in recorded),
                replay_verified=True, training_eligible=False,
            ))
            current = initial
            for step, transition in enumerate(recorded):
                following = transition["observation"]
                transitions.append(dict(
                    episode_id=episode_id, task_id=task_id, split="development", step=step,
                    minute=current.get("minute"), next_minute=following.get("minute"),
                    observation=current, action=transition["action"], next_observation=following,
                    reward=float(transition.get("objective_reward", 0.0)),
                    action_valid=transition.get("action_valid"), terminated=transition.get("terminated"),
                    truncated=transition.get("truncated"),
                ))
                current = following
            raw_records.append(dict(
                episode_id=episode_id,
                source_format="wildfire-response-benchmark-v1",
                source_file=path.relative_to(root).as_posix(),
                trajectory_sha256=compressed_sha,
                trajectory=trajectory,
            ))
    return episodes, transitions, raw_records


def export_dataset(prepared_inputs: str | Path, topographic_inputs: str | Path, output: str | Path,
                   trajectory_roots: list[str | Path] | None = None, *, allow_archived_catalog: bool = False) -> dict:
    from wildfire_core.catalog_lifecycle import require_archived_opt_in
    require_archived_opt_in("benchmark_v1", allow_archived=allow_archived_catalog)
    repo = _repo_root()
    out = Path(output).resolve()
    if out.exists():
        raise ValueError("Choose a fresh output directory")
    lock = _json(repo / "data/benchmark_v1/catalog_lock.json")
    prepared = load_inputs(prepared_inputs, expected_sha256=lock["prepared_inputs_sha256"])
    domain = load_domain(topographic_inputs, prepared, lock["topographic_inputs_sha256"])
    catalog = build_catalog(prepared, domain)
    if catalog.get("sha256") != lock["catalog_sha256"]:
        raise ValueError("Prepared inputs, terrain, or generated task catalog differ from the release lock")
    ids_by_split = {split: task_ids(catalog, split) for split in SPLITS}
    manifest = _json(repo / "data/benchmark_v1/manifest.json")
    expected = {(item["id"], item["split"]) for item in manifest["tasks"]}
    actual = {(identifier, split) for split, identifiers in ids_by_split.items() for identifier in identifiers}
    if expected != actual:
        raise ValueError("Generated catalog differs from the public task manifest")
    task_splits = {identifier: split for split, identifiers in ids_by_split.items() for identifier in identifiers}
    episodes, transitions, raw_records = _trace_exports(
        [Path(item).resolve() for item in (trajectory_roots or [])], task_splits,
        prepared, domain, catalog,
    )
    parent = out.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{out.name}-", dir=parent))
    files: dict[str, bytes] = {}
    try:
        bundle_names = {}
        for split, identifiers in ids_by_split.items():
            name = f"wildfire-{split}"
            bundle_names[split] = name
            bundle_root = staging / "bundles" / name
            _write_bundle(bundle_root, split, identifiers, prepared, domain, lock, repo)
            rows = _task_rows(bundle_root, name)
            files[f"tasks/{name}.parquet"] = agentenv_tables.parquet(rows, agentenv_tables.TASKS)
            files[f"episodes/{name}.parquet"] = agentenv_tables.parquet([], agentenv_tables.EPISODES)
            files[f"raw/{name}.jsonl"] = b""
            files.update({f"bundles/{name}/{path}": content for path, content in _bundle_files(bundle_root)})
        for relative, rows, schema in (
            ("trajectories/episodes.parquet", episodes, TRAJECTORY_EPISODES),
            ("trajectories/transitions.parquet", transitions, TRAJECTORY_TRANSITIONS),
        ):
            target = staging / relative
            _write_parquet(target, rows, schema)
            files[relative] = target.read_bytes()
        if raw_records:
            serialized = "".join(
                json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
                for record in raw_records
            ).encode("utf-8")
            files["trajectories/raw/wildfire-development.jsonl.gz"] = gzip.compress(serialized, mtime=0)

        card_text = None
        for split, name in bundle_names.items():
            card_text = dataset_card(
                card_text, name=name, split=split, repo=None,
                description=("ARCHIVED night-response reference; not the active incident-day training corpus. "
                             "Source-grounded synthetic wildfire-control tasks for a programmatic simulator. "
                             "The four split bundles are runnable AgentEnv tasks; model trajectories, when present, "
                             "are development-only and not training labels."),
                license=None,
                note=("Local export only. The repository root license and redistribution terms for the bundled "
                      "geospatial inputs are unresolved; do not publish this dataset yet."),
            )
        from huggingface_hub import DatasetCard

        card_obj = DatasetCard(card_text)
        card_obj.data.configs = [
            *(card_obj.data.get("configs") or []),
            {"config_name": "wildfire_trajectory_episodes", "data_files": [
                {"split": "development", "path": "trajectories/episodes.parquet"}]},
            {"config_name": "wildfire_trajectory_transitions", "data_files": [
                {"split": "development", "path": "trajectories/transitions.parquet"}]},
        ]
        card_obj.text += (
            "\n\n## Transition exports\n\n"
            "`trajectories/episodes.parquet` and `trajectories/transitions.parquet` preserve public observations, "
            "actions, rewards, terminal flags, and replay checks. Imported runs are restricted to the development "
            "split and are marked `training_eligible=false`; private evaluator audits and provider request logs "
            "are not exported. The `raw/wildfire-*.jsonl.gz` files are the original public simulator trace objects, "
            "not AgentEnv-native model transcripts.\n"
        )
        files["README.md"] = str(card_obj).encode("utf-8")
        output_manifest = {
            "format_version": FORMAT_VERSION,
            "catalog_status": "archived",
            "archival_use_acknowledged": True,
            "active_incident_day_training_corpus": False,
            "agentenv_framework": "0.9.1358",
            "agentenv_protocol": "0.1.373",
            "agentenv_hf": "0.3.0",
            "catalog_sha256": lock["catalog_sha256"],
            "prepared_inputs_sha256": lock["prepared_inputs_sha256"],
            "topographic_inputs_sha256": lock["topographic_inputs_sha256"],
            "tasks_by_split": {split: len(identifiers) for split, identifiers in ids_by_split.items()},
            "trajectory_episodes": len(episodes),
            "trajectory_transitions": len(transitions),
            "trajectory_split_policy": "Development only; all imported traces are replay-verified and training_eligible=false",
            "publication_status": "Local only; source-code and geospatial asset redistribution rights unresolved",
            "agentenv_native_episode_rows": 0,
        }
        files["manifest.json"] = (json.dumps(output_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
        scan(files, known_values())
        for relative, content in files.items():
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        staging.replace(out)
        return output_manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared-inputs", required=True)
    parser.add_argument("--topographic-inputs", required=True)
    parser.add_argument("--trajectory-root", action="append", default=[])
    parser.add_argument("--output", required=True)
    parser.add_argument("--allow-archived-catalog", action="store_true",
                        help="Explicitly export the frozen night-response archive; not current day-mode training data")
    args = parser.parse_args()
    print(json.dumps(export_dataset(args.prepared_inputs, args.topographic_inputs, args.output,
                                    args.trajectory_root, allow_archived_catalog=args.allow_archived_catalog), sort_keys=True))


if __name__ == "__main__":
    main()
