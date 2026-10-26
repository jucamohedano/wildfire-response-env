"""Opt-in competitive wildfire response benchmark; legacy modes are unchanged."""
from .environment import BenchmarkEnv, VERSION, verify_benchmark_trajectory
from .outcomes import recompute_outcomes

__all__ = ['BenchmarkEnv', 'VERSION', 'verify_benchmark_trajectory', 'recompute_outcomes']
