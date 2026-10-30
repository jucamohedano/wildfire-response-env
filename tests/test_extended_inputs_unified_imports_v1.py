"""The import bridge works with a regular core envs package in one process."""
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_core_and_extended_modules_share_contract_identity():
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=os.pathsep.join((
        str(ROOT / 'envs/wildfire_extended_inputs_v1'),
        str(ROOT / 'envs'),
        str(ROOT / 'envs/wildfire_response/core/src'),
        str(ROOT / 'envs/wildfire_containment_v1/src'), str(ROOT))))
    script = '''
import envs.wildfire_response.incident_day.scenarios
from envs.wildfire_response.incident_day import environment as core
before_source = core.implementation_digest()
before_tick = core.IncidentDayEnv._tick
import adapter_bootstrap_v1
first = adapter_bootstrap_v1.enable()
assert adapter_bootstrap_v1.enable() is first
assert core.implementation_digest() == before_source
assert core.IncidentDayEnv._tick is before_tick
from wildfire_extended_inputs_v1 import Contract, Censor, VERSION, canonical_sha256, load_inputs
import wildfire_extended_inputs_v1 as canonical
from envs.wildfire_extended_inputs_v1 import Contract as legacy_contract
import adapter_v1
import wildfire_extended_inputs_v1.adapter_v1 as canonical_adapter
from adapter_fire_v1 import ExpandedContainmentFireV1
import adapter_fire_v1
import envs.wildfire_extended_inputs_v1.adapter_fire_v1 as legacy_fire
import wildfire_extended_inputs_v1.adapter_fire_v1 as canonical_fire
from wildfire_expanded_incident_v1 import mixed_v2
assert canonical.Contract is Contract
assert legacy_contract is Contract
assert canonical.Censor is Censor
assert adapter_v1 is canonical_adapter
assert adapter_fire_v1 is legacy_fire is canonical_fire
assert mixed_v2.api is adapter_v1 and mixed_v2.fire_v1 is adapter_fire_v1
assert adapter_v1._contract_module.Contract is Contract
assert issubclass(ExpandedContainmentFireV1, adapter_v1.ExtendedContainmentBackendV1)
pins = {name: char * 64 for name, char in zip(('surface','fuel','elevation','access'), 'abcd')}
doc = dict(version=VERSION, grid=dict(width=3,height=3,crs='synthetic',transform=[500,0,0,0,-500,1500]),
    domain_mask=[[True]*3 for _ in range(3)], surface=[['land']*3 for _ in range(3)],
    fuel=[[4]*3 for _ in range(3)], elevation=[[100.]*3 for _ in range(3)],
    access=[['unknown']*3 for _ in range(3)], source_pins=pins)
contract = load_inputs(doc, canonical_sha256(doc), expected_source_pins=pins, opt_in=True)
fire = ExpandedContainmentFireV1(contract, seed=1, containment_config=dict(
    work_gain=2,max_strength=5,decay=.5,wind_factor=1,report_delay=2), pressure_scale=1, wind=[0,0])
assert isinstance(fire, canonical_adapter.ExtendedContainmentBackendV1)
'''
    subprocess.run([sys.executable, '-c', script], cwd=ROOT, env=env,
                   check=True, capture_output=True, text=True)


def test_conflicting_preloaded_copy_is_rejected():
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=os.pathsep.join((
        str(ROOT / 'envs/wildfire_extended_inputs_v1'),
        str(ROOT / 'envs/wildfire_response/core/src'), str(ROOT))))
    script = '''
import adapter_v1
import adapter_bootstrap_v1
try:
    adapter_bootstrap_v1.enable()
except RuntimeError as error:
    assert 'before importing' in str(error)
else:
    raise AssertionError('conflicting early import must fail closed')
'''
    subprocess.run([sys.executable, '-c', script], cwd=ROOT, env=env,
                   check=True, capture_output=True, text=True)
