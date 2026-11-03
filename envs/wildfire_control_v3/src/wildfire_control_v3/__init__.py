"""Versioned control-centre interface over the unchanged incident-day engine."""
import hashlib
import marshal
from pathlib import Path
import sys

INTERFACE_VERSION = "wildfire-control-interface-v3"


def _source_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _capture_source(name, path, loader):
    """Check source against loader code in this interpreter, failing closed."""
    try:
        source = Path(path).read_bytes()
        code = loader.get_code(name)
        compiled = compile(source, str(path), 'exec', dont_inherit=True, optimize=sys.flags.optimize)
        return hashlib.sha256(source).hexdigest(), code is not None and marshal.dumps(code) == marshal.dumps(compiled)
    except Exception:
        return None, False


_LOADED_PACKAGE_SOURCE_SHA256, _LOADED_PACKAGE_CODE_MATCH = _capture_source(__name__, __file__, __loader__)
