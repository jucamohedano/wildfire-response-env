"""Opt-in corrected observation contract; v2 remains available for frozen replay."""
import hashlib
import marshal
from pathlib import Path
import sys


def _source_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _capture_source(name, path, loader):
    try:
        source = Path(path).read_bytes()
        code = loader.get_code(name)
        compiled = compile(source, str(path), 'exec', dont_inherit=True, optimize=sys.flags.optimize)
        return hashlib.sha256(source).hexdigest(), code is not None and marshal.dumps(code) == marshal.dumps(compiled)
    except Exception:
        return None, False

_LOADED_SOURCE_SHA256, _LOADED_CODE_MATCH = _capture_source(__name__, __file__, __loader__)

from .identity import observation_identity, VERSION
from .population import PopulationEngineV3
from .feedback import FeedbackMixin


class IncidentDayEnvV3(FeedbackMixin, PopulationEngineV3):
    """Same physical simulator and reward, corrected delivered knowledge/feedback."""

__all__ = ['IncidentDayEnvV3', 'observation_identity', 'VERSION']
