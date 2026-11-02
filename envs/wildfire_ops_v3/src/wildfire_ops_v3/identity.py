"""Independent, loaded-source observation identity over the unchanged v2 core."""
import hashlib
from pathlib import Path
import sys
from envs.wildfire_response.incident_day.environment import implementation_digest
from envs.wildfire_response.regional.ops.generator import digest
from . import _capture_source

_LOADED_SOURCE_SHA256, _LOADED_CODE_MATCH = _capture_source(__name__, __file__, __loader__)
VERSION = 'wildfire-incident-day-ops-v3'
_ROOT = Path(__file__).parent
_CONTRACTS = ('wildfire_ops_v3.IncidentDayEnvV3', 'wildfire_ops_v3.population.PopulationEngineV3')

def _sources():
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(_ROOT.glob('*.py'))}

_LOADED = _sources()

def _contract_pin(contract):
    if contract not in _CONTRACTS:
        raise ValueError('Unsupported concrete observation contract')
    if _sources() != _LOADED:
        raise ValueError('Observation implementation changed after loading')
    for name in ('wildfire_ops_v3', 'wildfire_ops_v3.identity', 'wildfire_ops_v3.population', 'wildfire_ops_v3.feedback'):
        module = sys.modules.get(name)
        if (module is None or getattr(module, '_LOADED_CODE_MATCH', False) is not True
                or getattr(module, '_LOADED_SOURCE_SHA256', None) != _LOADED[Path(module.__file__).name]):
            raise ValueError('Observation loaded code differs from source: ' + name)
    return digest(dict(version=VERSION, core=implementation_digest(), observation_sources=_LOADED, contract=contract))

def contract_identity(cls):
    return _contract_pin(cls.__module__ + '.' + cls.__qualname__)

def observation_identity():
    return _contract_pin('wildfire_ops_v3.IncidentDayEnvV3')
