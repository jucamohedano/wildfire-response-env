"""Current control-center model policy, without a provider call on import."""
from envs.wildfire_response.regional.ops.model import (
    OpsModelPolicy, CONTRACT_VERSION, action_schema, parse_action, public_context,
)

__all__ = ['OpsModelPolicy', 'CONTRACT_VERSION', 'action_schema', 'parse_action', 'public_context']
