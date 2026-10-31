"""Experimental trusted-driver seam for a future extended-input containment engine.

This module does not adapt the existing incident-day lifecycle. See the companion
contract for the backend obligations, which cannot be inferred from a checkpoint.
"""
from abc import ABC, abstractmethod
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

if __package__:
    from . import contract as _contract_module
else:
    # The core distribution owns a regular top-level ``envs`` package, which
    # hides this repository's namespace package. A top-level adapter_v1 import
    # provides an explicit seam without changing either package's __init__.
    _name = '_wildfire_extended_inputs_v1_contract'
    _path = Path(__file__).with_name('contract.py').resolve()
    _contract_module = sys.modules.get(_name)
    if _contract_module is None:
        _spec = importlib.util.spec_from_file_location(_name, _path)
        _contract_module = importlib.util.module_from_spec(_spec)
        sys.modules[_name] = _contract_module
        try:
            _spec.loader.exec_module(_contract_module)
        except BaseException:
            del sys.modules[_name]
            raise
    elif Path(_contract_module.__file__).resolve() != _path:
        raise ImportError('conflicting extended-input contract module')
Censor = _contract_module.Censor
load_inputs = _contract_module.load_inputs

VERSION = 'wildfire-extended-containment-seam-v1'
_REASONS = frozenset({'invalid_burning_cell', 'grid_boundary', 'unknown_fire_state',
                      'invalid_fire_observation'})


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      allow_nan=False, ensure_ascii=True)


def _hash(value):
    return hashlib.sha256(_encoded(value).encode()).hexdigest()


def _source_pin():
    return _hash({name: hashlib.sha256(Path(path).read_bytes()).hexdigest()
                  for name, path in (('adapter', __file__), ('contract', _contract_module.__file__))})


class ExtendedContainmentBackendV1(ABC):
    """Trusted, reviewed implementation of the companion backend protocol.

    Construction must not ignite or step. The driver owns all subsequent
    mutations. Snapshots must contain every state variable including all RNGs.
    """
    @property
    @abstractmethod
    def identity(self):
        """Stable version/source/configuration identity, as a JSON value."""

    @abstractmethod
    def burning(self):
        """Return the complete current (column, row) burning set."""

    @abstractmethod
    def step(self, command):
        """Perform exactly one spread step; do not perform a second step."""

    @abstractmethod
    def change(self, command):
        """Perform one non-spread state change, possibly adding fire."""

    @abstractmethod
    def snapshot(self):
        """Return complete private JSON state, without changing state."""

    @abstractmethod
    def restore(self, snapshot):
        """Restore the complete private state and RNGs from a snapshot."""

    @abstractmethod
    def outcomes(self):
        """Return outcomes, without mutating state."""


class CensoredError(RuntimeError):
    """The episode is censored; further mutations and outcomes are prohibited."""


class CensoredContainmentSeamV1:
    """Own one reviewed backend and enforce checks at its mutation boundaries.

    This is a trusted-driver API, not a sandbox around an untrusted backend.
    """
    def __init__(self, document, expected_sha256, *, expected_source_pins,
                 backend_factory, expected_backend_identity, opt_in=False):
        self._inputs = load_inputs(document, expected_sha256,
                                   expected_source_pins=expected_source_pins, opt_in=opt_in)
        self._source = _source_pin()
        self._identity = copy.deepcopy(expected_backend_identity)
        _encoded(self._identity)
        self._backend = backend_factory(self._inputs)
        if not isinstance(self._backend, ExtendedContainmentBackendV1):
            raise TypeError('backend must implement ExtendedContainmentBackendV1')
        if _encoded(self._backend.identity) != _encoded(self._identity):
            raise ValueError('backend identity differs from independently supplied pin')
        self._censor = Censor(self._inputs)
        self._poisoned = False
        try:
            # A factory must not perform ignition or warm-up before handing over.
            if self._backend.burning():
                raise ValueError('backend must start without burning cells')
            self._check()
        except BaseException:
            self._poisoned = True
            raise

    def _ensure(self):
        if self._poisoned:
            raise RuntimeError('backend state is indeterminate; restore a trusted checkpoint')
        try:
            matches = (self._source == _source_pin()
                       and _encoded(self._backend.identity) == _encoded(self._identity))
        except BaseException:
            self._poisoned = True
            raise
        if not matches:
            self._poisoned = True
            raise RuntimeError('seam source or backend identity changed')

    def _check(self):
        return self._censor.check(self._backend.burning())

    @property
    def censored(self):
        return self._censor.censored

    @property
    def reasons(self):
        return self._censor.reasons

    def _mutate(self, method, command):
        self._ensure()
        try:
            safe = self._check()
        except BaseException:
            self._poisoned = True
            raise
        if not safe:
            raise CensoredError('episode censored before mutation')
        try:
            # No backend result is exposed: it could contain a censored outcome.
            method(copy.deepcopy(command))
            safe = self._check()
        except BaseException:
            # Even an exception before an apparent clock change may have mutated
            # fire, operations, RNG, or the completeness of the observation.
            self._poisoned = True
            raise
        if not safe:
            raise CensoredError('episode censored after mutation')

    def step(self, command=None):
        self._mutate(self._backend.step, command)

    def change(self, command):
        self._mutate(self._backend.change, command)

    def outcomes(self):
        self._ensure()
        try:
            safe = self._check()
        except BaseException:
            self._poisoned = True
            raise
        if not safe:
            raise CensoredError('outcomes suppressed for censored episode')
        try:
            result = self._backend.outcomes()
            _encoded(result)
            return copy.deepcopy(result)
        except BaseException:
            self._poisoned = True
            raise

    def checkpoint(self):
        self._ensure()
        try:
            self._check()
            value = dict(version=VERSION, source=self._source,
                         inputs=self._inputs.document_sha256, backend=copy.deepcopy(self._identity),
                         reasons=list(self.reasons), state=copy.deepcopy(self._backend.snapshot()))
            value['sha256'] = _hash(value)
            return value
        except BaseException:
            self._poisoned = True
            raise

    def restore(self, checkpoint):
        # A restore can recover a poisoned instance, but any failure poisons it.
        try:
            value = copy.deepcopy(checkpoint)
            if (type(value) is not dict or set(value) != {'version', 'source', 'inputs', 'backend', 'reasons', 'state', 'sha256'}
                    or value['version'] != VERSION or value['source'] != self._source
                    or self._source != _source_pin() or value['inputs'] != self._inputs.document_sha256
                    or _encoded(value['backend']) != _encoded(self._identity)
                    or _encoded(self._backend.identity) != _encoded(self._identity)
                    or value['sha256'] != _hash({k: v for k, v in value.items() if k != 'sha256'})):
                raise ValueError('checkpoint identity or integrity differs')
            reasons = value['reasons']
            if (type(reasons) is not list or any(type(x) is not str for x in reasons)
                    or reasons != sorted(set(reasons)) or not set(reasons) <= _REASONS):
                raise ValueError('invalid checkpoint censor state')
            self._backend.restore(copy.deepcopy(value['state']))
            if _encoded(self._backend.snapshot()) != _encoded(value['state']):
                raise ValueError('backend did not restore the exact snapshot')
            censor = Censor(self._inputs)
            censor._reasons.update(reasons)
            censor.check(self._backend.burning())
            if censor.reasons != tuple(reasons):
                raise ValueError('checkpoint omits current censor reasons')
            self._censor = censor
            self._poisoned = False
        except BaseException:
            self._poisoned = True
            raise
