"""Make the independent extended-input contract coexist with the core in pytest."""
import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[1] / 'envs/wildfire_extended_inputs_v1/adapter_bootstrap_v1.py'
_spec = importlib.util.spec_from_file_location('_wildfire_test_bootstrap_v1', _path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
_module.enable()
