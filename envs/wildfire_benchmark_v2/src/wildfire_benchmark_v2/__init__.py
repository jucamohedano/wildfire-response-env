"""Pure new-harm costs and best-observed-reference grading."""
import hashlib
import marshal
from pathlib import Path
import sys


def _capture_source(name, path, loader):
    """Record source bytes and reject loaded code that differs from those bytes."""
    try:
        source = Path(path).read_bytes()
        code = loader.get_code(name)
        compiled = compile(source, str(path), 'exec', dont_inherit=True, optimize=sys.flags.optimize)
        return hashlib.sha256(source).hexdigest(), code is not None and marshal.dumps(code) == marshal.dumps(compiled)
    except Exception:
        return None, False


_LOADED_SOURCE_SHA256, _LOADED_CODE_MATCH = _capture_source(__name__, __file__, __loader__)

from .grading import (
    Outcome,
    aggregate,
    cost,
    grade,
    qualify,
    weighted_components,
)
from .verifier import BenchmarkVerifier

__all__ = ['Outcome', 'cost', 'weighted_components', 'qualify', 'grade', 'aggregate', 'BenchmarkVerifier']
