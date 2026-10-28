"""Public entry point for the opt-in synthetic coverage expansion."""
from envs.wildfire_response.regional.expansion import ExpandedEnv
from envs.wildfire_response.regional.expansion.catalog import build_catalog, coverage, public_manifest

__all__ = ['ExpandedEnv', 'build_catalog', 'coverage', 'public_manifest']
