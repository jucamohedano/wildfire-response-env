"""No-model driver for a disposable AgentEnv fixture, executed by run_code."""
import asyncio
import json
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


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
                    'trajectory': trajectory, 'verification': verification}


def run(input_data):
    return asyncio.run(_run(input_data))
