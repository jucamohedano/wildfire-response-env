"""Explicit import bridge for the core's regular ``envs`` package.

Call ``enable()`` before importing either extended-input spelling. It extends
only the package search path and aliases this project's adapter modules; it does
not patch the core or any simulator class. This module is a runtime/pytest
bootstrap, not part of the fire/input contract versions.
"""
import importlib
import importlib.util
from pathlib import Path
import sys


def enable():
    """Install one shared extended-input/adapter module identity, idempotently.

    Refuse an already-loaded conflicting copy rather than silently splitting
    Contract/Censor or backend base-class identities.
    """
    directory = Path(__file__).resolve().parent
    envs_directory = directory.parent
    for name in ('adapter_v1', 'adapter_fire_v1', 'wildfire_extended_inputs_v1',
                 'wildfire_extended_inputs_v1.contract',
                 'wildfire_extended_inputs_v1.adapter_v1',
                 'wildfire_extended_inputs_v1.adapter_fire_v1'):
        if name in sys.modules:
            module = sys.modules[name]
            origin = getattr(module, '__file__', None)
            if origin is None or Path(origin).resolve().parent != directory:
                raise RuntimeError(f'conflicting preloaded module: {name}')
    # A top-level adapter loaded before its package creates a separate contract
    # class. It cannot be made safe retroactively without changing live objects.
    top = sys.modules.get('adapter_v1')
    legacy_adapter = sys.modules.get('envs.wildfire_extended_inputs_v1.adapter_v1')
    top_fire = sys.modules.get('adapter_fire_v1')
    legacy_fire = sys.modules.get('envs.wildfire_extended_inputs_v1.adapter_fire_v1')
    private_contract = sys.modules.get('_wildfire_extended_inputs_v1_contract')
    legacy_contract = sys.modules.get('envs.wildfire_extended_inputs_v1.contract')
    canonical_package = sys.modules.get('wildfire_extended_inputs_v1')
    legacy_package = sys.modules.get('envs.wildfire_extended_inputs_v1')
    if top is not None and top is not legacy_adapter:
        raise RuntimeError('enable the import bridge before importing top-level adapter_v1')
    if top_fire is not None and top_fire is not legacy_fire:
        raise RuntimeError('enable the import bridge before importing top-level adapter_fire_v1')
    if private_contract is not None and private_contract is not legacy_contract:
        raise RuntimeError('enable the import bridge before loading the private contract copy')
    if canonical_package is not None and canonical_package is not legacy_package:
        raise RuntimeError('enable the import bridge before importing the top-level package')
    envs = importlib.import_module('envs')
    paths = envs.__path__
    if str(envs_directory) not in paths:
        paths.append(str(envs_directory))
    package = importlib.import_module('envs.wildfire_extended_inputs_v1')
    contract = importlib.import_module('envs.wildfire_extended_inputs_v1.contract')
    adapter = importlib.import_module('envs.wildfire_extended_inputs_v1.adapter_v1')
    if adapter._contract_module is not contract:
        raise RuntimeError('extended-input contract identity differs')
    aliases = {'adapter_v1': adapter,
               'wildfire_extended_inputs_v1': package,
               'wildfire_extended_inputs_v1.contract': contract,
               'wildfire_extended_inputs_v1.adapter_v1': adapter}
    for name, module in aliases.items():
        existing = sys.modules.get(name)
        if existing is not None and existing is not module:
            raise RuntimeError(f'conflicting preloaded module: {name}')
    sys.modules.update(aliases)
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    # Alias the optional fire backend too, but only if its dependencies are
    # already available. Calling enable() again after adding those paths is safe.
    try:
        # A namespace package directory alone does not make the optional
        # modules importable (for example, ``envs/wildfire_containment_v1``
        # exists even when its ``src`` directory is not on the path).
        fire_available = all(importlib.util.find_spec(name) is not None for name in (
            'wildfire_containment_v1.fire', 'wildfire_containment_v1.incident_day',
            'envs.wildfire_response.incident_day.scenarios', 'simulator.engine'))
    except (ImportError, ModuleNotFoundError, ValueError):
        fire_available = False
    if fire_available:
        fire = importlib.import_module('envs.wildfire_extended_inputs_v1.adapter_fire_v1')
        fire_aliases = {'adapter_fire_v1': fire,
                        'wildfire_extended_inputs_v1.adapter_fire_v1': fire}
        for name, module in fire_aliases.items():
            existing = sys.modules.get(name)
            if existing is not None and existing is not module:
                raise RuntimeError(f'conflicting preloaded module: {name}')
        sys.modules.update(fire_aliases)
    return package
