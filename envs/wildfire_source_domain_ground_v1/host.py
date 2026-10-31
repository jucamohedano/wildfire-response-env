"""Versioned estimated-fire host adapter for the source ground experiment."""
import copy
import hashlib
from pathlib import Path

import adapter_v1 as api
from wildfire_expanded_incident_v1.estimated_fuel_v1 import EstimatedMixedFireV1
from wildfire_source_domain_v3 import host as original
from .seam import EstimatedHoldFireV1, source_sha256 as seam_source_sha256

VERSION='wildfire-source-domain-ground-incident-v1'
_ROOT=Path(__file__).resolve().parents[2]


def _source():
    path=_ROOT/'envs/wildfire_source_domain_ground_v1/host.py'
    if Path(__file__).resolve()!=path or original.DomainProfileSourceIncidentV3.__module__!=original.__name__:
        raise RuntimeError('domain ground host origin differs')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def input_identity_sha256(inputs):
    # A distinct namespace binds the new host without changing the v3 digest.
    return api._hash(dict(version=VERSION,domain_v3_input=original.input_identity_sha256(inputs)))


class DomainProfileGroundIncidentV1(original.DomainProfileSourceIncidentV3):
    """Exact legacy lifecycle with an explicitly bound estimated hold seam."""
    def __init__(self,inputs,source_catalog,parent_inputs,*,expected_input_sha256,opt_in=False,**kwargs):
        if opt_in is not True or input_identity_sha256(inputs)!=expected_input_sha256:
            raise ValueError('explicit opt-in and independently approved bridge input required')
        super().__init__(inputs,source_catalog,parent_inputs,
            expected_input_sha256=original.input_identity_sha256(inputs),opt_in=True,**kwargs)
        self._bridge_input=expected_input_sha256;self._bridge_source=_source();self._seam_source=seam_source_sha256()
        self._identity.update(version=VERSION,bridge_input=self._bridge_input,bridge_source=self._bridge_source,
                              seam_source=self._seam_source)
        self._restoring=False

    def _ensure(self):
        if self._poisoned:raise RuntimeError('domain ground host indeterminate; restore trusted checkpoint')
        try:
            self._selector._ensure();self._weather._ensure()
            backend=self._fire._inner if type(self._fire) is EstimatedHoldFireV1 else self._fire
            if (original._source()!=self._source or original.core_pins._sources()!=self._dependencies
                or original._clock_source()!=self._clock or _source()!=self._bridge_source
                or seam_source_sha256()!=self._seam_source or self._identity.get('bridge_source')!=self._bridge_source
                or self._identity.get('seam_source')!=self._seam_source
                or self._identity.get('bridge_input')!=self._bridge_input
                or input_identity_sha256(self._domain_inputs)!=self._bridge_input
                or self._selector._identity!=self._identity['catalog'] or self._weather._identity!=self._identity['fire']
                or api._hash({k:v for k,v in self._case.items() if k!='sha256'})!=self._identity['original']
                or api._hash({k:v for k,v in self._firecase.items() if k!='sha256'})!=self._identity['expanded']
                or type(self._weather) is not original._DOMAIN_WEATHER or self._weather._inputs is not self._domain_inputs
                or self._weather._fire is not self._fire or type(backend) is not EstimatedMixedFireV1
                or self._weather._minute!=self._minute
                or self._ops.last_minute!=(self._minute if self._minute else None)
                or (self._status!='censored' and self._bus._last_tick!=self._minute)
                or (self._status=='censored' and self._bus._last_tick not in (self._minute,self._minute-1))
                or type(self._fire) not in (EstimatedMixedFireV1,EstimatedHoldFireV1)
                or self._domain_inputs.base is not self._inputs or original._domain_source()!=self._domain_source
                or self._input_sha!=self._identity['domain_input'] or self._domain_source!=self._identity['domain_source']
                or original.input_identity_sha256(self._domain_inputs)!=self._input_sha):
                raise RuntimeError('domain ground host identity changed')
            original._check_live_inputs(self._domain_inputs,backend)
        except BaseException:self._poisoned=True;raise

    def situation(self):
        result=super().situation();result['version']=VERSION
        if result['status']!='censored':
            result['scope']='domain-profile estimated-fuel ground-bridge experiment; no evacuation; unscored'
        return result

    def checkpoint(self):
        value=super().checkpoint()
        if not self._restoring:
            value['version']=VERSION;value['sha256']=api._hash({k:v for k,v in value.items() if k!='sha256'})
        return value

    def restore(self,checkpoint,*,expected_checkpoint_sha256):
        try:
            value=copy.deepcopy(checkpoint)
            if (type(expected_checkpoint_sha256) is not str or len(expected_checkpoint_sha256)!=64
                    or any(c not in '0123456789abcdef' for c in expected_checkpoint_sha256)
                    or api._hash(value)!=expected_checkpoint_sha256 or type(value) is not dict
                    or value.get('version')!=VERSION
                    or value.get('sha256')!=api._hash({k:v for k,v in value.items() if k!='sha256'})):
                raise ValueError('trusted domain ground checkpoint differs')
            base=copy.deepcopy(value);base['version']=original.VERSION
            base['sha256']=api._hash({k:v for k,v in base.items() if k!='sha256'})
            self._restoring=True
            try:super().restore(base,expected_checkpoint_sha256=api._hash(base))
            finally:self._restoring=False
            if api._encoded(self.checkpoint())!=api._encoded(value):raise ValueError('domain ground restore differs')
        except BaseException:self._poisoned=True;raise

    def transcript(self):
        value=super().transcript();value['version']=VERSION;return value

    def replay(self,transcript,*,expected_sha256,expected_identity_sha256,expected_final_checkpoint_sha256):
        if (type(transcript) is not dict or transcript.get('version')!=VERSION
                or api._hash(transcript)!=expected_sha256):raise ValueError('trusted domain ground transcript differs')
        base=copy.deepcopy(transcript);base['version']=original.VERSION
        return super().replay(base,expected_sha256=api._hash(base),expected_identity_sha256=expected_identity_sha256,
                              expected_final_checkpoint_sha256=expected_final_checkpoint_sha256)
