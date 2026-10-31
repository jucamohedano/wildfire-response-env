"""Source-pinned weather and v4 mixed-fire exercise; no host or resource claims."""
import copy
import hashlib
from pathlib import Path

import adapter_v1 as api
from eval import incident_weather_v1 as weather
from .mixed_v2 import MixedInputsV2, InteriorMixedFireV2, _VERIFIED

VERSION = 'wildfire-v4-weather-fire-diagnostic-v1'
WEATHER_SOURCE_SHA256 = '746ed89b81ed859f73d40979af8cd80bf4b839dbaed031a695dd7b74e19449bc'


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _profile(case):
    schedule = case.get('weather_schedule')
    if type(schedule) is not list or not schedule:
        raise ValueError('weather schedule required')
    profile = weather.WeatherProfile.from_records(schedule)
    if (profile.records[0].received_at != 0 or any(r.minute > 1440 or r.received_at-r.minute > 60
                                                    for r in profile.records)
            or list(profile.records[0].wind) != case.get('wind')):
        raise ValueError('weather receipt, horizon or handover wind differs')
    changes = [(r.minute, list(r.wind)) for prev, r in zip(profile.records, profile.records[1:])
               if r.wind != prev.wind]
    if changes != [(e['minute'], e['wind']) for e in case['events'] if e['kind'] == 'wind']:
        raise ValueError('wind events and weather schedule differ')
    warm = weather.WeatherProfile.from_records([profile.records[0].as_dict()])
    return profile, warm


class WeatherMixedFireDiagnosticV1:
    """One private fire-only episode, incrementally advanced at safe boundaries.

    Warm-up applies minute-zero weather. Day tick t applies the true conditions
    at t using the existing moisture proxy, before one censored fire step.
    """
    def __init__(self, inputs, drafts, *, expected_drafts_sha256, case_id,
                 containment_config, pressure_scale, opt_in_weather_fire=False):
        if (opt_in_weather_fire is not True or not isinstance(inputs, MixedInputsV2)
                or inputs._verified is not _VERIFIED
                or dict(inputs.identity).get('version') != 'wildfire-small-water-mixed-exercise-input-v4'):
            raise ValueError('validated v4 inputs and explicit weather-fire opt-in required')
        if _sha(weather.__file__) != WEATHER_SOURCE_SHA256:
            raise RuntimeError('unreviewed weather proxy source')
        if (type(drafts) is not dict or drafts.get('version') != 'wildfire-extended-scenario-drafts-v1'
                or drafts.get('status') != 'unqualified-not-runnable' or drafts.get('sha256') != expected_drafts_sha256
                or api._hash({k:v for k,v in drafts.items() if k!='sha256'}) != expected_drafts_sha256
                or drafts.get('source_catalog_sha256') != dict(inputs.identity)['catalog']):
            raise ValueError('draft identity or source catalog differs')
        candidates=[c for c in drafts['cases'] if c['id']==case_id]
        if len(candidates)!=1: raise ValueError('unknown source draft')
        case=copy.deepcopy(candidates[0])
        if (case.get('sha256') != api._hash({k:v for k,v in case.items() if k!='sha256'})
                or type(case.get('warmup_minutes')) is not int or not 0<=case['warmup_minutes']<=1440
                or type(case.get('events')) is not list):
            raise ValueError('invalid source draft')
        self._profile,self._warm=_profile(case)
        self._case=case
        self._inputs=inputs
        self._fire=InteriorMixedFireV2(inputs,seed=case['seed'],containment_config=containment_config,
            pressure_scale=pressure_scale,wind=case['wind'],opt_in_mixed_experiment=True)
        self._identity=dict(version=VERSION,source=_sha(__file__),weather_source=WEATHER_SOURCE_SHA256,
            inputs=dict(inputs.identity),drafts=expected_drafts_sha256,scenario=case['sha256'],
            profile=self._profile.sha256,seed=case['seed'],fire=self._fire.identity)
        self._warmup_target=case['warmup_minutes']
        self._warmup_done=0
        self._minute=0
        self._weather_kind='warm' if self._warmup_target else 'day'
        self._weather_state=weather.initial_state(self._warm if self._warmup_target else self._profile)
        self._status='active'
        self._poisoned=False
        try:
            if not self._fire.change({'ignite':case['initial_fire']}):self._status='censored'
        except BaseException:
            self._poisoned=True;raise

    def _ensure(self):
        if self._poisoned:raise RuntimeError('diagnostic indeterminate; restore a trusted checkpoint')
        if _sha(__file__)!=self._identity['source'] or _sha(weather.__file__)!=self._identity['weather_source']:
            self._poisoned=True;raise RuntimeError('weather-fire source changed')

    def situation(self):
        self._ensure()
        return dict(version=VERSION,status=self._status,minute=self._minute,warmup_steps=self._warmup_done,
                    mode='private_unscored_fire_only')

    def step(self):
        self._ensure()
        if self._status!='active':raise RuntimeError('diagnostic cannot advance')
        try:
            # No weather or clock changes if the current fire is unsupported.
            if not self._fire._check():
                self._status='censored';return self.situation()
            warming=self._weather_kind=='warm'
            if warming:
                next_weather=weather.advance(self._warm,self._weather_state,self._warmup_done+1)
                conditions=weather.conditions(self._warm,0)
            else:
                minute=self._minute+1
                conditions=weather.conditions(self._profile,minute)
                row=copy.deepcopy(conditions);row.update(minute=0,received_at=0)
                selected=weather.WeatherProfile.from_records([row])
                prior=weather.initial_state(selected,moisture_proxy=self._weather_state['moisture_proxy'])
                updated=weather.advance(selected,prior,1)
                next_weather=weather.initial_state(self._profile,moisture_proxy=updated['moisture_proxy'])
                next_weather['minute']=minute
            # Instance-local state only; no global hooks, no rounding of draft wind.
            self._fire._fire.wind=tuple(conditions['wind'])
            self._fire._fire.rules['spread_factor']=weather.spread_factor(next_weather,base_factor=.5)
            safe=self._fire.step()
            self._weather_state=next_weather
            if warming:
                self._warmup_done+=1
                if safe and self._warmup_done==self._warmup_target:
                    self._weather_state=weather.initial_state(self._profile,moisture_proxy=next_weather['moisture_proxy'])
                    self._weather_kind='day'
            else:
                self._minute+=1
            if not safe:self._status='censored'
            elif not warming and self._minute==1440:self._status='diagnostic_end'
            return self.situation()
        except BaseException:
            self._poisoned=True;raise

    def outcomes(self):
        raise NotImplementedError('no outcome or reward model')

    def checkpoint(self):
        self._ensure()
        try:
            selected=self._warm if self._weather_kind=='warm' else self._profile
            value=dict(version=VERSION,identity=copy.deepcopy(self._identity),status=self._status,
                warmup_target=self._warmup_target,warmup_done=self._warmup_done,minute=self._minute,
                weather_kind=self._weather_kind,weather=weather.checkpoint(selected,self._weather_state),
                wind=list(self._fire._fire.wind),spread_factor=self._fire._fire.rules['spread_factor'],
                fire=self._fire.snapshot())
            value['sha256']=api._hash(value)
            return value
        except BaseException:
            self._poisoned=True;raise

    def restore(self, checkpoint):
        try:
            v=copy.deepcopy(checkpoint)
            expected={'version','identity','status','warmup_target','warmup_done','minute','weather_kind',
                      'weather','wind','spread_factor','fire','sha256'}
            if (type(v) is not dict or set(v)!=expected or v['version']!=VERSION
                    or api._encoded(v['identity'])!=api._encoded(self._identity)
                    or _sha(__file__)!=self._identity['source'] or _sha(weather.__file__)!=WEATHER_SOURCE_SHA256
                    or v['sha256']!=api._hash({k:x for k,x in v.items() if k!='sha256'})
                    or v['status'] not in ('active','censored','diagnostic_end')
                    or v['warmup_target']!=self._warmup_target
                    or type(v['warmup_done']) is not int or not 0<=v['warmup_done']<=self._warmup_target
                    or type(v['minute']) is not int or not 0<=v['minute']<=1440
                    or v['weather_kind'] not in ('warm','day')
                    or (v['weather_kind']=='warm' and (self._warmup_target==0 or v['minute']!=0))
                    or (v['weather_kind']=='day' and v['warmup_done']!=self._warmup_target)
                    or (v['status']=='diagnostic_end' and v['minute']!=1440)
                    or (v['status']=='active' and v['minute']==1440)
                    or (v['status']=='active' and v['weather_kind']=='warm'
                        and v['warmup_done']==self._warmup_target)):
                raise ValueError('weather-fire checkpoint identity or clock differs')
            selected=self._warm if v['weather_kind']=='warm' else self._profile
            state=weather.restore(selected,v['weather'])
            expected_minute=v['warmup_done'] if v['weather_kind']=='warm' else v['minute']
            if state['minute']!=expected_minute:
                raise ValueError('weather proxy clock differs')
            f=InteriorMixedFireV2(self._inputs,seed=self._case['seed'],
                containment_config=self._identity['fire']['config'],pressure_scale=self._identity['fire']['pressure_scale'],
                wind=self._case['wind'],opt_in_mixed_experiment=True)
            f.restore(v['fire'])
            if f.snapshot()['tick']!=v['warmup_done']+v['minute'] or f.censored!=(v['status']=='censored'):
                raise ValueError('fire and censor clocks differ')
            conditions=weather.conditions(self._warm,0) if v['weather_kind']=='warm' else weather.conditions(self._profile,v['minute'])
            expected_wind=conditions['wind']
            expected_factor=(weather.spread_factor(state,base_factor=.5)
                             if v['warmup_done']+v['minute'] else .5)
            if api._encoded(v['wind'])!=api._encoded(expected_wind) or v['spread_factor']!=expected_factor:
                raise ValueError('weather effects and fire state differ')
            f._fire.wind=tuple(v['wind']);f._fire.rules['spread_factor']=v['spread_factor']
            self._fire,self._weather_state,self._weather_kind=f,state,v['weather_kind']
            self._status,self._warmup_done,self._minute=v['status'],v['warmup_done'],v['minute']
            self._poisoned=False
            if api._encoded(self.checkpoint())!=api._encoded(v):raise ValueError('weather-fire restore differs')
        except BaseException:
            self._poisoned=True;raise
