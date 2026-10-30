"""Versioned control-centre interface over the unchanged incident-day engine."""
import hashlib
from pathlib import Path

INTERFACE_VERSION = "wildfire-control-interface-v3"


def _source_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


_LOADED_PACKAGE_SOURCE_SHA256 = _source_sha256(__file__)
