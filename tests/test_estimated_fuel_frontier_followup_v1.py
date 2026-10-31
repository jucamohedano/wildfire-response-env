"""Selection remains tied to censored burning stencils, never cold cells."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_frontier_selection():
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=os.pathsep.join([
        str(ROOT / 'envs'), str(ROOT / 'envs/wildfire_extended_inputs_v1'),
        str(ROOT / 'envs/wildfire_response/core/src'),
        str(ROOT / 'envs/wildfire_containment_v1/src'), str(ROOT)]))
    result = subprocess.run([sys.executable, __file__], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def run():
    import pytest
    modules = []
    for version in (1, 2):
        spec = importlib.util.spec_from_file_location('followup', ROOT / f'eval/estimated_fuel_frontier_followup_v{version}.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    def row(name, status, heat=1, fuel=1, x=2, y=2):
        return {'case': name, 'checkpoint': {'status': status, 'fire': {'land': [
            [x, y, {'heat': heat, 'fuel': fuel}]]}}}
    prior = {'cases': [row('one', 'censored'), row('two', 'censored', x=4),
                       row('cold', 'censored', heat=0), row('spent', 'censored', fuel=0),
                       row('done', 'diagnostic_end'), row('far', 'censored', x=9)]}
    for module in modules:
        assert module.selected_cases(prior, [(3, 3)]) == ['one', 'two']
        with pytest.raises(ValueError, match='empty'):
            module.selected_cases(prior, [])
        with pytest.raises(ValueError, match='duplicate'):
            module.selected_cases({'cases': [row('x', 'censored'), row('x', 'censored')]}, [(3, 3)])


if __name__ == '__main__':
    run()
