"""No-model driver for a disposable AgentEnv fixture, executed by run_code."""
import asyncio
import ast
import inspect
from pathlib import Path
import hashlib
import json
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def canonical_hash(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def critical_file_hashes():
    from envs.wildfire_response.regional.benchmark_v1 import environment

    tree = ast.parse(inspect.getsource(environment.implementation_digest))
    assignment = next(node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == 'modules' for target in node.targets))
    modules = ast.literal_eval(assignment.value)
    source = Path(environment.__file__).resolve().parents[4]
    regional = source / 'envs/wildfire_response/regional'
    paths = sorted([*(regional / directory / (name + '.py') for directory, names in modules.items() for name in names),
                    *(source / 'simulator').rglob('*.py'), source / 'wildfire_core/benchmark_v1.py'])
    return {str(path.relative_to(source)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


async def _run(input_data):
    async with streamable_http_client(input_data['env']['mcp_url']) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            async def call(name, arguments=None):
                response = await session.call_tool(name, arguments or {})
                if response.isError:
                    raise RuntimeError(f'{name} failed')
                return json.loads(''.join(getattr(item, 'text', '') for item in response.content))
            await call('reset_episode', {'task_id': input_data['args']['task_id']})
            completed = False
            for _ in range(60):
                result = await call('submit_action', {'action': {}})
                if result['terminated'] or result['truncated']:
                    completed = True
                    break
            if not completed:
                raise RuntimeError('fixture did not complete within 60 decisions')
            trajectory = await call('get_trajectory')
            trace_hash = canonical_hash(trajectory)
            header_hash = canonical_hash({key: value for key, value in trajectory.items() if key != 'transitions'})
            verification = await call('verify_episode')
            transitions = trajectory.get('transitions') or []
            if (len(transitions) != 60 or transitions[-1]['observation']['minute'] != 120
                    or not result['terminated'] or result['truncated']):
                raise RuntimeError('fixture did not complete the full 120-minute horizon')
            if not verification.get('passed'):
                raise RuntimeError('fixture exact replay failed')
            return {'task_id': input_data['args']['task_id'], 'decisions': len(trajectory['transitions']),
                    'terminated': result['terminated'], 'truncated': result['truncated'],
                    'replay_passed': True, 'end_minute': 120, 'model_used': False,
                    'trajectory_json': canonical_json(trajectory), 'verification': verification,
                    'source_trajectory_sha256': trace_hash, 'source_header_sha256': header_hash,
                    'source_critical_file_sha256': critical_file_hashes()}


def run(input_data):
    return asyncio.run(_run(input_data))
