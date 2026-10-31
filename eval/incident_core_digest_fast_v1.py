"""Experimental, uncached equivalent of the incident-day implementation digest.

This is a standalone prototype, not a hook into the pinned runtime. Every call
rediscovers the same files and reads their contents; it has no metadata cache.
"""
import hashlib
import json
import os
from pathlib import Path

VERSION = 'incident-core-digest-fast-v1'


def _relative_paths(root):
    """Return the legacy Path ordering, including the fixed data file."""
    paths = []
    for folder in ('envs/wildfire_response', 'wildfire_core', 'simulator'):
        base = root.joinpath(*folder.split('/'))
        # Path.rglob does not follow symlinked directories. os.walk likewise
        # ignores directory scan errors by default, as pathlib's glob does.
        for directory, dirs, files in os.walk(base, followlinks=False):
            relative = Path(directory).relative_to(root)
            parts = relative.parts
            if 'tests' in root.parts or 'tests' in parts:
                dirs[:] = []
                continue
            dirs[:] = [name for name in dirs if name != 'tests']
            for name in dirs + files:
                if name.endswith('.py') and name != 'tests':
                    paths.append(parts + (name,))
    paths.append(('envs', 'wildfire_response', 'data', 'huelva_regional_v2.json'))
    return sorted(paths)


def implementation_digest(root=None):
    """Compute the legacy digest for a core/src root without changing its pins."""
    if root is None:
        from envs.wildfire_response.incident_day import environment
        root = Path(environment.__file__).parents[3]
    root = Path(root)
    rows = {}
    for parts in _relative_paths(root):
        relative = os.path.join(*parts)
        with open(root / relative, 'rb') as stream:
            rows[relative] = hashlib.sha256(stream.read()).hexdigest()
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode('utf-8')).hexdigest()
