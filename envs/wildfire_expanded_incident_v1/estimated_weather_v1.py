"""Private weather/fire sensitivity driver for estimated-fuel experiments."""
import copy
import adapter_v1 as api
from eval import incident_weather_v1 as weather
from . import weather_fire_v1 as existing
from .estimated_fuel_v1 import EstimatedInputsV1, EstimatedMixedFireV1, _VERIFIED_ESTIMATE, _file_sha

VERSION='wildfire-estimated-fuel-weather-fire-v1'


class EstimatedWeatherFireV1(existing.WeatherMixedFireDiagnosticV1):
    def __init__(self, inputs, drafts, *, expected_drafts_sha256, case_id, containment_config,
                 pressure_scale, opt_in_estimated_weather=False):
        if (opt_in_estimated_weather is not True or not isinstance(inputs,EstimatedInputsV1)
                or inputs._verified is not _VERIFIED_ESTIMATE):
            raise ValueError('validated estimated inputs and explicit weather opt-in required')
        if existing._sha(weather.__file__)!=existing.WEATHER_SOURCE_SHA256:
            raise RuntimeError('unreviewed weather proxy source')
        if (type(drafts) is not dict or drafts.get('version')!='wildfire-extended-scenario-drafts-v1'
                or drafts.get('status')!='unqualified-not-runnable' or drafts.get('sha256')!=expected_drafts_sha256
                or api._hash({k:v for k,v in drafts.items() if k!='sha256'})!=expected_drafts_sha256
                or drafts.get('source_catalog_sha256')!=dict(inputs.base.identity)['catalog']):
            raise ValueError('draft identity or source catalog differs')
        cases=[c for c in drafts['cases'] if c['id']==case_id]
        if len(cases)!=1:raise ValueError('unknown draft')
        case=copy.deepcopy(cases[0])
        if (case.get('sha256')!=api._hash({k:v for k,v in case.items() if k!='sha256'})
                or type(case.get('warmup_minutes')) is not int or not 0<=case['warmup_minutes']<=1440
                or type(case.get('events')) is not list):raise ValueError('invalid draft')
        self._profile,self._warm=existing._profile(case)
        self._case=case;self._inputs=inputs
        self._fire=EstimatedMixedFireV1(inputs,seed=case['seed'],containment_config=containment_config,
            pressure_scale=pressure_scale,wind=case['wind'],opt_in_estimated_fuel=True)
        self._identity=dict(version=VERSION,source=existing._sha(existing.__file__),estimated_weather_source=_file_sha(__file__),
            weather_source=existing.WEATHER_SOURCE_SHA256,inputs=dict(inputs.identity),drafts=expected_drafts_sha256,
            scenario=case['sha256'],profile=self._profile.sha256,seed=case['seed'],fire=self._fire.identity)
        self._warmup_target=case['warmup_minutes'];self._warmup_done=0;self._minute=0
        self._weather_kind='warm' if self._warmup_target else 'day'
        self._weather_state=weather.initial_state(self._warm if self._warmup_target else self._profile)
        self._status='active';self._poisoned=False
        try:
            if not self._fire.change({'ignite':case['initial_fire']}):self._status='censored'
        except BaseException:self._poisoned=True;raise

    def _ensure(self):
        super()._ensure()
        if _file_sha(__file__)!=self._identity['estimated_weather_source']:
            self._poisoned=True;raise RuntimeError('estimated weather source changed')

    def situation(self):
        value=super().situation();value['version']=VERSION;return value

    def checkpoint(self):
        value=super().checkpoint();value['version']=VERSION
        value['sha256']=api._hash({k:v for k,v in value.items() if k!='sha256'})
        return value

    def restore(self, checkpoint):
        try:
            v=copy.deepcopy(checkpoint)
            expected={'version','identity','status','warmup_target','warmup_done','minute','weather_kind',
                      'weather','wind','spread_factor','fire','sha256'}
            # Revalidate source identity even when recovering a poisoned host.
            self._poisoned=False
            self._ensure()
            if (type(v) is not dict or set(v)!=expected or v['version']!=VERSION
                    or api._encoded(v['identity'])!=api._encoded(self._identity)
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
                    or (v['status']=='active' and v['weather_kind']=='warm' and v['warmup_done']==self._warmup_target)):
                raise ValueError('estimated weather checkpoint identity or clock differs')
            selected=self._warm if v['weather_kind']=='warm' else self._profile
            state=weather.restore(selected,v['weather'])
            if state['minute']!=(v['warmup_done'] if v['weather_kind']=='warm' else v['minute']):
                raise ValueError('weather proxy clock differs')
            f=EstimatedMixedFireV1(self._inputs,seed=self._case['seed'],containment_config=self._identity['fire']['config'],
                pressure_scale=self._identity['fire']['pressure_scale'],wind=self._case['wind'],opt_in_estimated_fuel=True)
            f.restore(v['fire'])
            if f.snapshot()['tick']!=v['warmup_done']+v['minute'] or f.censored!=(v['status']=='censored'):
                raise ValueError('fire and censor clocks differ')
            conditions=weather.conditions(self._warm,0) if v['weather_kind']=='warm' else weather.conditions(self._profile,v['minute'])
            factor=weather.spread_factor(state,base_factor=.5) if v['warmup_done']+v['minute'] else .5
            if api._encoded(v['wind'])!=api._encoded(conditions['wind']) or v['spread_factor']!=factor:
                raise ValueError('weather effects and fire state differ')
            f._fire.wind=tuple(v['wind']);f._fire.rules['spread_factor']=v['spread_factor']
            self._fire,self._weather_state,self._weather_kind=f,state,v['weather_kind']
            self._status,self._warmup_done,self._minute=v['status'],v['warmup_done'],v['minute'];self._poisoned=False
            if api._encoded(self.checkpoint())!=api._encoded(v):raise ValueError('estimated weather restore differs')
        except BaseException:self._poisoned=True;raise
