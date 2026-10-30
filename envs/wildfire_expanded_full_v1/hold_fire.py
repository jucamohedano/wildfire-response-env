"""Private, versioned hold-assignment seam around the pinned mixed fire."""
import copy
import hashlib
from pathlib import Path

import adapter_v1 as api
from wildfire_containment_v1.kernel import ContainmentKernel, _edge
from wildfire_expanded_incident_v1.mixed_v2 import InteriorMixedFireV2, _MixedFire

VERSION = 'wildfire-expanded-mixed-hold-fire-v1'
_MIXED_STEP = _MixedFire.step


def source_sha256():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class MixedHoldFireV1:
    """Trusted single-driver wrapper; costs and snapshots remain private.

    The existing weather owner calls step() once after setting current weather.
    An exception after mutation poisons the backend: restore a trusted joint
    checkpoint. A post-step censor retains the completed physical tick and costs.
    """
    def __init__(self, fire):
        if type(fire) is not InteriorMixedFireV2 or type(fire._fire) is not _MixedFire:
            raise ValueError('exact mixed fire backend required')
        self._inner = fire
        self._source = source_sha256()
        self._pending = None
        self.costs = None

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def stage(self, assignments):
        if self._pending is not None or type(assignments) is not list:
            raise ValueError('exactly one assignment batch may be staged')
        self._pending = copy.deepcopy(assignments)
        self.costs = None

    def step(self, command=None):
        fire = self._inner
        fire._ensure()
        if source_sha256() != self._source or _MixedFire.step is not _MIXED_STEP:
            fire._poisoned = True
            raise RuntimeError('hold fire implementation changed')
        if command is not None or self._pending is None:
            raise ValueError('a staged hold batch is required')
        assignments = self._pending
        self._pending = None
        try:
            if not fire._check():
                raise api.CensoredError('censored before hold tick')
            inner = fire._fire
            for item in assignments:
                for x, y in _edge(item.get('edge')):
                    if not (0 <= x < inner.width and 0 <= y < inner.height and fire._is_land(x, y)):
                        raise ValueError('hold edge must be supported land or mixed surface')
            # Validate all assignments on a copy before touching physical state.
            probe = ContainmentKernel.restore(inner._containment.checkpoint())
            probe.tick(probe._minute + 1, assignments=assignments, attacks=[], wind=list(inner.wind))
            if inner._assignments or inner._suppressing:
                raise RuntimeError('unexpected outstanding fire assignments')
            inner._assignments = copy.deepcopy(assignments)
            try:
                inner.step(1)
            finally:
                inner._assignments = []
                inner._suppressing = []
            if inner.tick != inner._containment._minute:
                raise RuntimeError('fire and containment clocks differ')
            self.costs = copy.deepcopy(inner._last_costs)
            return fire._check()
        except BaseException:
            fire._poisoned = True
            raise
