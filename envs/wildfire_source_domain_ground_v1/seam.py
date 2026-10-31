"""Estimated-fire containment seam; never promotes unknown cells or access."""
import copy
import hashlib
from pathlib import Path

import adapter_v1 as api
from wildfire_containment_v1.kernel import ContainmentKernel, _edge
from wildfire_expanded_incident_v1.estimated_fuel_v1 import EstimatedMixedFireV1
from wildfire_expanded_incident_v1.mixed_v2 import _MixedFire

VERSION='wildfire-domain-estimated-hold-fire-v1'
_MIXED_STEP=_MixedFire.step


def source_sha256():return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class EstimatedHoldFireV1:
    """Apply one checked batch inside the existing estimated-fire tick."""
    def __init__(self,fire,validator):
        if type(fire) is not EstimatedMixedFireV1 or type(fire._fire) is not _MixedFire or not callable(validator):
            raise ValueError('exact estimated fire and work validator required')
        self._inner=fire;self._validator=validator;self._source=source_sha256()
        self._pending=None;self.costs=None;self.work_blocked=False

    def __getattr__(self,name):return getattr(self._inner,name)

    def stage(self,assignments):
        if self._pending is not None or type(assignments) is not list:
            raise ValueError('exactly one assignment batch may be staged')
        self._pending=copy.deepcopy(assignments);self.costs=None;self.work_blocked=False

    def step(self,command=None):
        fire=self._inner;fire._ensure()
        if source_sha256()!=self._source or _MixedFire.step is not _MIXED_STEP:
            fire._poisoned=True;raise RuntimeError('estimated hold fire binding changed')
        if command is not None or self._pending is None:raise ValueError('staged hold batch required')
        try:
            if not fire._check():raise api.CensoredError('censored before hold tick')
            assignments=self._pending
            if assignments and not self._validator():assignments=[];self.work_blocked=True
            self._pending=None;inner=fire._fire
            for item in assignments:
                for x,y in _edge(item.get('edge')):
                    if not (0<=x<inner.width and 0<=y<inner.height and fire._is_land(x,y)):
                        raise ValueError('unsupported containment cell')
            probe=ContainmentKernel.restore(inner._containment.checkpoint())
            probe.tick(probe._minute+1,assignments=assignments,attacks=[],wind=list(inner.wind))
            if inner._assignments or inner._suppressing:raise RuntimeError('outstanding fire assignments')
            inner._assignments=copy.deepcopy(assignments)
            try:inner.step(1)
            finally:inner._assignments=[];inner._suppressing=[]
            if inner.tick!=inner._containment._minute:raise RuntimeError('fire and containment clocks differ')
            self.costs=copy.deepcopy(inner._last_costs)
            return fire._check()
        except BaseException:
            fire._poisoned=True;raise
