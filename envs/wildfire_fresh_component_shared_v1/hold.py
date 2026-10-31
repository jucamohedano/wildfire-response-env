"""Narrow domain-profile adapter for the existing walk-and-hold lifecycle."""
import copy
import hashlib
from pathlib import Path

import adapter_v1 as api
from wildfire_containment_v1.kernel import _edge
from eval.fresh_two_component_operations_v1 import FreshTwoComponentOperationsV1
import wildfire_source_walk_hold_v1 as original
from . import source_host
from .walk import FreshComponentWalkV1

VERSION='wildfire-fresh-component-walk-hold-exercise-v1'
SCOPE='domain_synthetic_predeployed_walk_hold_no_evacuation_unscored'
_ROOT=Path(__file__).resolve().parents[2]



def _source():
    path=_ROOT/'envs/wildfire_fresh_component_shared_v1/hold.py'
    if Path(__file__).resolve()!=path:
        raise RuntimeError('domain hold adapter binding differs')
    return api._hash(dict(adapter=hashlib.sha256(path.read_bytes()).hexdigest(),
                         parent=original._source(),host=source_host._source()))


class _ComponentHoldBridge:
    def __init__(self,walk,validator):
        self.walk=walk;self.validator=validator;self._pending=None;self.costs=None;self.work_blocked=False
    @property
    def _fire(self):return self.walk._host._fire._fire
    def stage(self,assignments):
        if self._pending is not None or type(assignments) is not list:raise ValueError('one assignment batch required')
        self._pending=copy.deepcopy(assignments);self.costs=None;self.work_blocked=False


def _install(walk,validator):
    return _ComponentHoldBridge(walk,validator)


def _candidate_identity(walk):
    """Validate a fresh walk and describe the hold without installing it."""
    if type(walk) is not FreshComponentWalkV1:
        raise ValueError('exact domain walk required')
    walk._ensure()
    if (walk._host._minute!=0 or walk._calls or walk._host._status!='active'
            or type(walk._host._fresh) is not FreshTwoComponentOperationsV1
            or walk._host._weather._fire is not walk._host._fire):
        raise ValueError('fresh active unwrapped walk handover required')
    edge=_edge((walk._proposal['front_cell'],walk._proposal['site_cell']))
    protected=tuple(walk._proposal['site_cell'])
    if protected not in edge or any(not walk._host._fire._is_land(x,y) for x,y in edge):
        raise ValueError('candidate front requires supported containment cells')
    return dict(version=VERSION,scope=SCOPE,implementation=_source(),
        walk=copy.deepcopy(walk._identity),handover=api._hash(walk.checkpoint()),
        edge=[list(p) for p in edge],protected_cell=list(protected),
        setup_minutes=original._setup_minutes(walk),authorization='explicit_synthetic_candidate_front_only',
        accounting='one_existing_walk_duty_minute_per_tick_no_extra_water')


class FreshComponentHoldV1(original.SourceWalkHoldV1):
    """Reuse the existing hold lifecycle with an estimated-fire censor seam."""
    def __init__(self,walk,*,expected_identity,opt_in=False):
        if opt_in is not True:raise ValueError('explicit opt-in and exact domain walk required')
        identity=_candidate_identity(walk)
        if type(expected_identity) is not dict or api._encoded(expected_identity)!=api._encoded(identity):
            raise ValueError('independently approved domain hold identity differs')
        self._walk=walk;self._identity=identity
        self._edge=copy.deepcopy(identity['edge']);self._protected=copy.deepcopy(identity['protected_cell'])
        self._setup=identity['setup_minutes'];self._source=identity['implementation']
        self._identity_seal=api._hash(self._identity);self._fire=_install(walk,self._live_work_safe)
        self._hold=dict(phase='idle',due_at=None,work_minutes=0)
        self._hold_seal=api._hash(self._hold);self._calls=[];self._poisoned=False
        if api._hash(walk._host.checkpoint())!=walk._seal:raise RuntimeError('hold installation changed handover')

    def _ensure(self):
        if self._poisoned:raise RuntimeError('domain hold exercise indeterminate')
        try:
            self._walk._ensure()
            if (_source()!=self._source or self._identity['implementation']!=self._source
                    or api._hash(self._identity)!=self._identity_seal
                    or api._encoded(self._walk._identity)!=api._encoded(self._identity['walk'])
                    or self._edge!=self._identity['edge'] or self._protected!=self._identity['protected_cell']
                    or self._setup!=self._identity['setup_minutes'] or original._setup_minutes(self._walk)!=self._setup
                    or type(self._fire) is not _ComponentHoldBridge or self._fire._pending is not None
                    or self._fire.walk is not self._walk or self._walk._host._weather._fire is not self._walk._host._fire
                    or api._hash(self._hold)!=self._hold_seal):
                raise RuntimeError('domain hold implementation or state changed')
        except BaseException:self._poisoned=True;raise

    def situation(self):
        value=super().situation();value['version']=VERSION
        if value['status']!='censored':value['scope']=SCOPE
        return value

    def checkpoint(self):
        value=super().checkpoint();value['version']=VERSION
        value['sha256']=api._hash({k:v for k,v in value.items() if k!='sha256'})
        return value

    def restore(self,checkpoint,*,expected_checkpoint_sha256):
        try:
            value=copy.deepcopy(checkpoint)
            if (type(expected_checkpoint_sha256) is not str or len(expected_checkpoint_sha256)!=64
                    or any(c not in '0123456789abcdef' for c in expected_checkpoint_sha256)
                    or api._hash(value)!=expected_checkpoint_sha256 or type(value) is not dict
                    or set(value)!={'version','identity','walk','hold','calls','sha256'}
                    or value['version']!=VERSION or value['identity']!=self._identity
                    or value['sha256']!=api._hash({k:v for k,v in value.items() if k!='sha256'})
                    or _source()!=self._source):raise ValueError('trusted domain hold checkpoint differs')
            h=value['hold']
            if (type(h) is not dict or set(h)!={'phase','due_at','work_minutes'}
                    or h['phase'] not in ('idle','setting_up','holding','stopped','breached')
                    or (h['phase']=='setting_up' and (type(h['due_at']) is not int or h['due_at']<1))
                    or (h['phase']!='setting_up' and h['due_at'] is not None)
                    or type(h['work_minutes']) is not int or h['work_minutes']<0 or type(value['calls']) is not list):
                raise ValueError('invalid domain hold checkpoint state')
            self._walk.restore(value['walk'],expected_checkpoint_sha256=api._hash(value['walk']))
            self._fire=_install(self._walk,self._live_work_safe)
            self._hold,self._calls=h,value['calls'];self._hold_seal=api._hash(h);self._poisoned=False
            if api._encoded(self.checkpoint())!=api._encoded(value):raise ValueError('domain hold continuation differs')
        except BaseException:self._poisoned=True;raise

    def transcript(self):
        value=super().transcript();value['version']=VERSION;return value

    def replay(self,transcript,*,expected_sha256,expected_final_checkpoint_sha256):
        if (type(transcript) is not dict or transcript.get('version')!=VERSION
                or api._hash(transcript)!=expected_sha256):raise ValueError('trusted domain hold transcript differs')
        base=copy.deepcopy(transcript);base['version']=original.VERSION
        return super().replay(base,expected_sha256=api._hash(base),
                              expected_final_checkpoint_sha256=expected_final_checkpoint_sha256)


def configuration_identity(walk,*,opt_in=False):
    """Return a review candidate without mutating or consuming the walk."""
    if opt_in is not True:raise ValueError('explicit opt-in required')
    return copy.deepcopy(_candidate_identity(walk))
