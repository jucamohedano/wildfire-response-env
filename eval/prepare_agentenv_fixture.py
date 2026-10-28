"""Build a disposable synthetic no-model AgentEnv smoke bundle for Docker CI."""
import argparse
import json
from pathlib import Path

from envs.wildfire_response.agentenv.export import _repo_root, _write_bundle
from envs.wildfire_response.regional.benchmark_v1.catalog import build_catalog, task_ids
from envs.wildfire_response.tests.test_topographic_development import fixture_domain, fixture_inputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('output must not exist')
    prepared = fixture_inputs()
    domain = fixture_domain(prepared)
    catalog = build_catalog(prepared, domain)
    task = task_ids(catalog, 'development')[0]
    frozen = json.loads((_repo_root() / 'data/benchmark_v1/catalog_lock.json').read_text())
    if catalog['sha256'] == frozen['catalog_sha256'] or prepared['sha256'] == frozen['prepared_inputs_sha256']:
        raise ValueError('synthetic fixture must not claim frozen real-input pins')
    lock = dict(version='synthetic-agentenv-fixture-v1', prepared_inputs_sha256=prepared['sha256'],
                topographic_inputs_sha256=domain['sha256'], catalog_sha256=catalog['sha256'])
    _write_bundle(args.output, 'development', [task], prepared, domain, lock, _repo_root())
    (args.output / 'README.md').write_text(
        '# Synthetic AgentEnv fixture v1\n\n'
        'Generated from synthetic test inputs. Catalog and input hashes differ from the frozen real-input benchmark. '
        'Development-only full-horizon exercise; no model or historical replay.\n')
    driver = args.output / 'artifacts' / 'scripted_driver'
    driver.mkdir()
    driver.joinpath('run.py').write_text(Path(__file__).with_name('agentenv_fixture_driver.py').read_text())
    verifier = args.output / 'artifacts' / 'verify_wildfire' / 'verify.py'
    verifier.write_text('''import json\nfrom mcp import ClientSession\nfrom mcp.client.streamable_http import streamable_http_client\n\nasync def verify(mcp_url):\n    async with streamable_http_client(mcp_url) as (read, write, _):\n        async with ClientSession(read, write) as session:\n            await session.initialize()\n            response = await session.call_tool("verify_episode")\n    if response.isError:\n        raise RuntimeError("MCP verification failed")\n    result = json.loads("".join(getattr(part, "text", "") for part in response.content))\n    return [{"id": "wildfire-replay", "result": result.get("passed") is True,\n             "score": float(result["reward"]) if result.get("passed") is True else 0.0,\n             "description": "Replay verification status and graded simulator score; not operational validation"}]\n''')
    steps = [
        {'id':'deploy', 'type':'deploy_env', 'env_id':'wildfire', 'sandbox_type':'local'},
        {'id':'drive', 'type':'run_code', 'env_id':'wildfire', 'script_artifact_id':'scripted_driver',
         'args':{'task_id':task}, 'timeout_seconds':180, 'depends_on':[{'task_step_id':'deploy'}]},
        {'id':'verify', 'type':'env_outcome_verifier', 'env_id':'wildfire', 'file_artifact_id':'verify_wildfire',
         'verifier_id':'wildfire_reward', 'score_aggregator':'weighted_average', 'depends_on':[{'task_step_id':'drive'}]},
    ]
    (args.output / 'tasks' / f'{task}.json').write_text(json.dumps(steps, indent=2)+'\n')
    print(json.dumps({'task_id':task, 'scope':'synthetic fixture; no model; development only'}))


if __name__ == '__main__':
    main()
