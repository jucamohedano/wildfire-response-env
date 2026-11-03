"""Identity checks only: resets/reads, isolated loaders and temporary wheel install."""
import copy
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

from wildfire_core.incident_day import fixture_inputs
from wildfire_ops_v3 import IncidentDayEnvV3, observation_identity
from wildfire_ops_v3.identity import contract_identity
from wildfire_ops_v3.population import PopulationEngineV3, population_engine_identity

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / 'envs/wildfire_ops_v3'
CORE_SRC = ROOT / 'envs/wildfire_response/core/src'
MODULES = ('wildfire_ops_v3', 'wildfire_ops_v3.identity',
           'wildfire_ops_v3.population', 'wildfire_ops_v3.feedback')


def create(cls):
    inputs, network = fixture_inputs()
    env = cls(inputs, network, expected_inputs_sha256=inputs['sha256'],
              expected_network_sha256=network['sha256'])
    env.reset('incident-day-dev-000')
    return env


@pytest.mark.parametrize('cls,identity,contract', [
    (PopulationEngineV3, population_engine_identity, 'wildfire_ops_v3.population.PopulationEngineV3'),
    (IncidentDayEnvV3, observation_identity, 'wildfire_ops_v3.IncidentDayEnvV3'),
])
def test_supported_contract_has_own_checkpoint_and_trajectory_pin(cls, identity, contract):
    env = create(cls)
    pin = identity()
    checkpoint = json.loads(json.dumps(env.checkpoint()))
    assert checkpoint['contract'] == contract
    assert checkpoint['implementation_sha256'] == env.trajectory()['implementation_sha256'] == pin
    other = create(cls)
    assert other.restore(checkpoint) == env.get_situation()
    assert other.checkpoint() == env.checkpoint()
    assert contract_identity(cls) == pin


def test_public_identity_is_combined_contract_and_distinct_from_population_only():
    assert observation_identity() == create(IncidentDayEnvV3).trajectory()['implementation_sha256']
    assert population_engine_identity() == create(PopulationEngineV3).trajectory()['implementation_sha256']
    assert observation_identity() != population_engine_identity()


@pytest.mark.parametrize('source,target', [
    (PopulationEngineV3, IncidentDayEnvV3), (IncidentDayEnvV3, PopulationEngineV3),
])
def test_cross_contract_restore_rejected_without_changing_target(source, target):
    original, receiver = create(source), create(target)
    checkpoint = copy.deepcopy(original.checkpoint())
    before = receiver.checkpoint()
    with pytest.raises(ValueError, match='checkpoint identity'):
        receiver.restore(checkpoint)
    assert receiver.checkpoint() == before
    assert original.checkpoint() == checkpoint


@pytest.mark.parametrize('parent', [PopulationEngineV3, IncidentDayEnvV3])
def test_unregistered_subclass_cannot_claim_supported_identity(parent):
    class Unregistered(parent):
        pass
    inputs, network = fixture_inputs()
    env = Unregistered(inputs, network, expected_inputs_sha256=inputs['sha256'],
                       expected_network_sha256=network['sha256'])
    with pytest.raises(ValueError, match='Unsupported concrete observation contract'):
        env.reset()
    with pytest.raises(ValueError, match='Unsupported concrete observation contract'):
        contract_identity(Unregistered)


@pytest.mark.parametrize('module_name', MODULES)
@pytest.mark.parametrize('attribute,value', [('_LOADED_CODE_MATCH', False),
                                          ('_LOADED_SOURCE_SHA256', '0' * 64)])
def test_every_module_loaded_code_or_source_mismatch_fails_closed(monkeypatch, module_name, attribute, value):
    module = importlib.import_module(module_name)
    monkeypatch.setattr(module, attribute, value)
    for identity in (observation_identity, population_engine_identity):
        with pytest.raises(ValueError, match='loaded (code|source).*differs'):
            identity()


def test_changed_disk_source_manifest_fails_closed_without_writing_source(monkeypatch):
    identity_module = importlib.import_module('wildfire_ops_v3.identity')
    changed = dict(identity_module._sources(), **{'feedback.py': '0' * 64})
    monkeypatch.setattr(identity_module, '_sources', lambda: changed)
    with pytest.raises(ValueError, match='changed after loading'):
        observation_identity()


def python(code, addon_path, cwd):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
               PYTHONPATH=os.pathsep.join((str(addon_path), str(CORE_SRC))))
    return subprocess.run([sys.executable, '-B', '-c', code], cwd=cwd, env=env,
                          text=True, capture_output=True, check=True, timeout=45).stdout


@pytest.mark.parametrize('target', ['wildfire_ops_v3', 'wildfire_ops_v3.feedback'])
def test_custom_loader_source_mismatch_rejects_feedback_and_package_root(tmp_path, target):
    code = f'''
import importlib, importlib.machinery, json
original = importlib.machinery.SourceFileLoader.get_code
def mismatched(loader, name):
    if name == {target!r}:
        return compile(loader.get_source(name) + "\\n_IDENTITY_TEST_STALE_CODE = True\\n", loader.path, 'exec')
    return original(loader, name)
importlib.machinery.SourceFileLoader.get_code = mismatched
from wildfire_ops_v3 import observation_identity
from wildfire_ops_v3.population import population_engine_identity
module = importlib.import_module({target!r})
assert module._LOADED_CODE_MATCH is False
rejected = []
for identity in (observation_identity, population_engine_identity):
    try:
        identity()
    except ValueError:
        rejected.append(identity.__name__)
    else:
        raise AssertionError('Mismatched loaded code received a valid pin')
print(json.dumps(rejected))
'''
    assert json.loads(python(code, ADDON / 'src', tmp_path)) == [
        'observation_identity', 'population_engine_identity']


def test_source_and_wheel_installation_have_equivalent_contract_pins(tmp_path):
    pytest.importorskip('setuptools.build_meta')
    pytest.importorskip('wheel')
    # Build from a temporary copy: no source checkout build/egg-info changes.
    package = tmp_path / 'package'
    shutil.copytree(ADDON, package, ignore=shutil.ignore_patterns('__pycache__', '*.egg-info', 'build'))
    wheels = tmp_path / 'wheels'; wheels.mkdir()
    python('import setuptools.build_meta as b; b.build_wheel(' + repr(str(wheels)) + ')',
           ADDON / 'src', package)
    wheel, = wheels.glob('*.whl')
    installed = tmp_path / 'site-packages'; installed.mkdir()
    with zipfile.ZipFile(wheel) as archive:
        archive.extractall(installed)
    code = '''
import json
from wildfire_ops_v3 import observation_identity
from wildfire_ops_v3.population import population_engine_identity
print(json.dumps({'combined': observation_identity(), 'population': population_engine_identity()}))
'''
    source = json.loads(python(code, ADDON / 'src', tmp_path))
    wheel_identity = json.loads(python(code, installed, tmp_path))
    assert source == wheel_identity
    assert source['combined'] != source['population']
