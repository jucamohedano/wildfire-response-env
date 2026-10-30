"""Private passive host for independently validated v3 mixed hypotheses."""
import copy
import hashlib
from pathlib import Path

import adapter_v1 as api
from envs.wildfire_response.incident_day.events import EventBus
from envs.wildfire_response.incident_day.operations import Operations
from .host import ExpandedIncidentV1, SourceGroundedDiagnosticV1, PROFILE, _source
from .mixed_v2 import MixedInputsV2, InteriorMixedFireV2, _VERIFIED
from .exercise_v3 import VERSION as INPUT_VERSION

VERSION='wildfire-inland-mixed-fire-diagnostic-v3'
V4_VERSION='wildfire-small-water-mixed-fire-diagnostic-v4'


def _sha():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class SourceGroundedMixedDiagnosticV3(SourceGroundedDiagnosticV1):
    """Original ignition and warm-up; no resources, weather updates or scoring."""
    _input_version=INPUT_VERSION
    _version=VERSION
    def __init__(self, inputs, drafts, *, expected_drafts_sha256, case_id,
                 containment_config, pressure_scale, opt_in_mixed_experiment=False):
        if (opt_in_mixed_experiment is not True or not isinstance(inputs,MixedInputsV2)
                or inputs._verified is not _VERIFIED or dict(inputs.identity).get('version') != self._input_version):
            raise ValueError('validated v3 inputs and explicit opt-in required')
        identity=dict(inputs.identity)
        if (type(drafts) is not dict or drafts.get('version') != 'wildfire-extended-scenario-drafts-v1'
                or drafts.get('status') != 'unqualified-not-runnable' or drafts.get('sha256') != expected_drafts_sha256
                or api._hash({k:v for k,v in drafts.items() if k!='sha256'}) != expected_drafts_sha256
                or drafts.get('source_catalog_sha256') != identity['catalog']):
            raise ValueError('draft identity or source catalog differs')
        candidates=[c for c in drafts['cases'] if c['id']==case_id]
        if len(candidates)!=1: raise ValueError('unknown source draft')
        draft=copy.deepcopy(candidates[0])
        if (draft.get('sha256') != api._hash({k:v for k,v in draft.items() if k!='sha256'})
                or type(draft.get('warmup_minutes')) is not int or not 0 <= draft['warmup_minutes'] <= 1440
                or not draft.get('centers') or not draft.get('weather_schedule')):
            raise ValueError('invalid source draft')
        changes=[i['minute'] for i in draft['weather_schedule'] if i['minute']>0]
        changes += [i['minute'] for i in draft['events'] if i['minute']>0]
        if any(type(v) is not int or not 1<=v<=1440 for v in changes): raise ValueError('invalid future event')
        self._first_event=min(changes,default=1441)
        self._mixed_inputs=inputs
        self._inputs=inputs.contract
        self._case=dict(id=case_id,seed=draft['seed'],initial_fire=copy.deepcopy(draft['initial_fire']),
                        wind=copy.deepcopy(draft['wind']),center=draft['centers'][0])
        self._fire=InteriorMixedFireV2(inputs,seed=draft['seed'],containment_config=containment_config,
            pressure_scale=pressure_scale,wind=draft['wind'],opt_in_mixed_experiment=True)
        self._identity=dict(version=self._version,source=_source(),mixed_host_sha256=_sha(),mixed_input=identity,
                            drafts=expected_drafts_sha256,scenario=draft['sha256'],case=case_id,fire=self._fire.identity)
        self._ops=Operations([],[],[],PROFILE)
        self._bus=EventBus(draft['seed'],[draft['centers'][0]])
        self._known,self._known_at={},{}
        self._known_fire=copy.deepcopy(draft['initial_fire'])
        self._minute=0;self._warmup_target=draft['warmup_minutes'];self._warmup_done=0
        self._censored=self._complete=self._blocked=self._poisoned=False
        try:
            if not self._fire.change({'ignite':draft['initial_fire']}):
                self._censored=True;return
            for _ in range(self._warmup_target):
                safe=self._fire.step();self._warmup_done+=1
                if not safe:self._censored=True;return
        except BaseException:
            self._poisoned=True;raise

    def _ensure(self):
        ExpandedIncidentV1._ensure(self)
        if self._identity['mixed_host_sha256'] != _sha():
            self._poisoned=True;raise RuntimeError('mixed host source changed')

    def situation(self):
        result=super().situation();result['version']=self._version
        return result

    def checkpoint(self):
        value=super().checkpoint();value.pop('sha256');value['version']=self._version
        value['sha256']=api._hash(value);return value

    def restore(self,checkpoint):
        try:
            value=copy.deepcopy(checkpoint)
            expected={'version','identity','minute','censored','complete','fire','operations','communications',
                      'known','known_at','known_fire','sha256','warmup_target','warmup_done','first_event','blocked'}
            if (type(value) is not dict or set(value)!=expected or value['version']!=self._version
                    or api._encoded(value['identity'])!=api._encoded(self._identity)
                    or self._identity['source']!=_source() or self._identity['mixed_host_sha256']!=_sha()
                    or value['sha256']!=api._hash({k:v for k,v in value.items() if k!='sha256'})
                    or value['warmup_target']!=self._warmup_target or value['first_event']!=self._first_event
                    or type(value['warmup_done']) is not int or not 0<=value['warmup_done']<=self._warmup_target
                    or type(value['minute']) is not int or not 0<=value['minute']<=1440
                    or any(type(value[k]) is not bool for k in ('censored','blocked','complete'))
                    or (not value['censored'] and value['warmup_done']!=self._warmup_target)
                    or value['operations']['last_minute']!=(value['minute'] if value['minute'] else None)):
                raise ValueError('mixed host checkpoint identity or clock differs')
            f=InteriorMixedFireV2(self._mixed_inputs,seed=self._case['seed'],
                containment_config=self._identity['fire']['config'],pressure_scale=self._identity['fire']['pressure_scale'],
                wind=self._case['wind'],opt_in_mixed_experiment=True)
            f.restore(value['fire'])
            if f.snapshot()['tick']!=value['minute']+value['warmup_done'] or f.censored!=value['censored']:
                raise ValueError('mixed host fire and censor clocks differ')
            ops,bus=Operations.restore(value['operations']),EventBus.restore(value['communications'])
            if bus.checkpoint()['last_tick']>value['minute']:raise ValueError('communication clock differs')
            self._fire,self._ops,self._bus=f,ops,bus
            self._minute,self._censored,self._complete=value['minute'],value['censored'],value['complete']
            self._warmup_done,self._blocked=value['warmup_done'],value['blocked']
            self._known,self._known_at,self._known_fire=value['known'],value['known_at'],value['known_fire']
            self._poisoned=False
            if api._encoded(self.checkpoint())!=api._encoded(value):raise ValueError('mixed host restoration differs')
        except BaseException:
            self._poisoned=True;raise


class SourceGroundedMixedDiagnosticV4(SourceGroundedMixedDiagnosticV3):
    """Accept only the separately source-reconstructed v4 mixed input."""
    _input_version='wildfire-small-water-mixed-exercise-input-v4'
    _version=V4_VERSION
