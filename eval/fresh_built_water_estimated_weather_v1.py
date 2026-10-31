"""Private fresh-from-seed source-draft two-component weather diagnostic.

No legacy fire checkpoint is split. This is a bounded, unscored evaluator-side
experiment, not an actor API, full incident host, or calibrated fire model.
This separate version supports an explicitly bounded warmup and day horizon.
"""
import copy
import hashlib
import math
import random
from pathlib import Path

import adapter_v1 as api
from eval import incident_weather_v1 as weather
from eval import source_built_water_estimated_adapter_v1 as staging_adapter
from eval import two_component_fire_v3 as model
from eval.extended_map_common import digest
from eval import extended_scenario_drafts_v1 as draft_module
from eval.extended_scenario_drafts_v1 import build_drafts
from envs.wildfire_response.incident_day import scenarios as scenario_module
from envs.wildfire_response.incident_day.scenarios import DayFire
from envs.wildfire_response.regional import fire as regional_fire
from simulator.engine import Simulation
from wildfire_containment_v1.fire import ContainmentDayFire
from wildfire_containment_v1.kernel import ContainmentKernel
from wildfire_expanded_incident_v1 import weather_fire_v1 as source_weather
from wildfire_expanded_incident_v1.weather_fire_v1 import _profile,WEATHER_SOURCE_SHA256

VERSION='wildfire-fresh-source-built-water-estimated-weather-v1'
STAGING_ADAPTER_PIN='b0fec53c8b505905b6f2f86d0f1d817410d041052e04b81221d7889cb0d3d841'
MODEL_PIN='b247a550fbb78746cdf01c5bdd4b0861ec153a304dbde41f73e002975aa34ce3'
STAGING_MODEL_PIN='fd0ca091ccc6f50d0f122b4305ab2d0effa69d61196a714f05cf76032e7027b3'
PROFILE_PIN='7b0ea639a5c6c917252d7dc78b8bbf8c5cd21de4a12211938289a2d9267bc97a'
DRAFT_SOURCE_PIN='739f479ef5ac5849237e3cbe13cb5d3933a45f14a07d1e9185c367d3883039ae'
WEATHER_DRIVER_PIN='568ed74072426c6cddc170c36aceb5d699d0ad4a4ce41a9e9081905d388b4cd4'
SCENARIO_SOURCE_PIN='891394294687b156471705b68e34f7e687fb8e3c34272882f00270696ca6c654'
REGIONAL_FIRE_PIN='3f03dc618bf46bf0d24289a29e201cc084a39a2ca760e983d41b3504a9093a8c'
_STAGE_ADAPT=staging_adapter.adapt
_MODEL_CLASS=model.TwoComponentFireV3
_REGIONAL_SPREAD=regional_fire.RegionalFire.spread_interval
_SIMULATOR_SPREAD=Simulation.spread_interval
_SIMULATOR_WIND=Simulation.set_wind
_WEATHER_FUNCTIONS={k:getattr(weather,k) for k in ('initial_state','advance','conditions','spread_factor','checkpoint','restore')}


def _sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _ensure_code():
    if (_sha(staging_adapter.__file__)!=STAGING_ADAPTER_PIN or _sha(model.__file__)!=MODEL_PIN
            or _sha(weather.__file__)!=WEATHER_SOURCE_SHA256 or _sha(draft_module.__file__)!=DRAFT_SOURCE_PIN
            or _sha(source_weather.__file__)!=WEATHER_DRIVER_PIN or _sha(scenario_module.__file__)!=SCENARIO_SOURCE_PIN
            or _sha(regional_fire.__file__)!=REGIONAL_FIRE_PIN or build_drafts is not draft_module.build_drafts
            or _profile is not source_weather._profile or DayFire is not scenario_module.DayFire
            or regional_fire.RegionalFire.spread_interval is not _REGIONAL_SPREAD
            or Simulation.spread_interval is not _SIMULATOR_SPREAD or Simulation.set_wind is not _SIMULATOR_WIND
            or any(getattr(weather,k) is not v for k,v in _WEATHER_FUNCTIONS.items())
            or staging_adapter.adapt is not _STAGE_ADAPT
            or model.TwoComponentFireV3 is not _MODEL_CLASS or staging_adapter.PROFILE_PIN!=PROFILE_PIN
            or staging_adapter.MODEL_PIN!=STAGING_MODEL_PIN):
        raise RuntimeError('unreviewed staging, model or weather implementation')
    staging_adapter._check_implementation()


def _validate_staging(staged,expected_identity):
    """Validate private staged grids against an independently retained identity."""
    if (type(staged) is not dict or staged.get('version')!=staging_adapter.VERSION
            or staged.get('role')!='private_v2_grid_staging_not_source_fire_owner'
            or type(expected_identity) is not str or len(expected_identity)!=64
            or digest(staged.get('identity'))!=expected_identity):
        raise ValueError('independently pinned staging identity differs')
    ident=staged['identity'];grid=staged['grid']
    if (ident.get('profile_sha256')!=PROFILE_PIN or ident.get('model_sha256')!=STAGING_MODEL_PIN
            or ident.get('stage_sha256')!=staging_adapter.STAGE_PIN or ident.get('adapter_sha256')!=STAGING_ADAPTER_PIN
            or any(ident.get(name+'_sha256')!=digest(staged[name]) for name in ('components','heights','crossings','transfers'))
            or ident.get('provenance_sha256')!=digest(staged['private_audit'])
            or grid['width']!=len(staged['components'][0]) or grid['height']!=len(staged['components'])
            or grid['crs']!='EPSG:25830' or grid['transform'][0]!=500 or grid['transform'][4]!=-500):
        raise ValueError('staged source grids or provenance differ')
    return copy.deepcopy(ident)


def _fresh_scaffold(parent,seed,grid,heights,wind,containment_config,pressure_scale):
    """Fresh seeded clock/slope carrier; no parent fire state is carried over."""
    day=DayFire(parent,seed)
    if (day.rng.getstate()!=random.Random(seed).getstate() or
            day.suppression_rng.getstate()!=random.Random(seed+1).getstate()):
        raise ValueError('fresh seed consumed during scaffold construction')
    day.width,day.height=grid['width'],grid['height']
    day.heights=copy.deepcopy(heights)
    day.cells=[[dict(terrain=None,fuel=0.,initial_fuel=0.,heat=0.,age=0,wet=0,burned=0.)
                for _ in range(day.width)] for _ in range(day.height)]
    if (type(wind) not in (tuple,list) or len(wind)!=2 or
            any(type(x) not in (int,float) or not math.isfinite(x) or abs(x)>3 for x in wind)):
        raise ValueError('invalid initial wind')
    # Simulation.set_wind rounds to two decimals; preserve the source vector.
    day.wind=tuple(wind)
    kernel=ContainmentKernel(**containment_config)
    return ContainmentDayFire.from_day_fire(day,containment=kernel,pressure_scale=pressure_scale)


class FreshBuiltWaterEstimatedWeatherV1:
    """Private, bounded source-draft weather/fire owner; no score or operations."""
    def __init__(self,staged,profile,parent,catalog,*,expected_staging_identity,expected_parent_sha256,
                 expected_catalog_sha256,case_id,ignition_components,containment_config,
                 pressure_scale,day_horizon,opt_in=False):
        if opt_in is not True:raise ValueError('explicit synthetic source-draft opt-in required')
        _ensure_code();staged=copy.deepcopy(staged);profile=copy.deepcopy(profile)
        ident=_validate_staging(staged,expected_staging_identity)
        if (type(profile) is not dict or profile.get('sha256')!=PROFILE_PIN
                or api._hash({k:v for k,v in profile.items() if k!='sha256'})!=PROFILE_PIN):
            raise ValueError('independently pinned source profile differs')
        if (type(parent) is not dict or parent.get('version')!='incident-day-inputs-v2'
                or parent.get('sha256')!=expected_parent_sha256
                or api._hash({k:v for k,v in parent.items() if k!='sha256'})!=expected_parent_sha256
                or type(catalog) is not dict or catalog.get('sha256')!=expected_catalog_sha256
                or api._hash({k:v for k,v in catalog.items() if k!='sha256'})!=expected_catalog_sha256
                or catalog.get('inputs_sha256')!=expected_parent_sha256):
            raise ValueError('source parent or catalog pin differs')
        grid=staged['grid'];full=staged['private_audit']['bbox'];source_grid=profile['grid']
        # Draft coordinates are full-grid coordinates: cropped runs need a
        # separate version with explicit remapping and boundary qualification.
        if (full!=[0,0,grid['width'],grid['height']] or ident['bbox']!=full
                or grid!={k:source_grid[k] for k in ('width','height','crs','transform')}
                or grid['width']!=84 or grid['height']!=59):
            raise ValueError('source owner requires the complete 84x59 grid')
        drafts=build_drafts(catalog,parent['grid'],grid)
        candidates=[c for c in drafts['cases'] if c['id']==case_id]
        if len(candidates)!=1:raise ValueError('unknown source draft')
        case=copy.deepcopy(candidates[0]);self._profile,self._warm=_profile(case)
        points=[tuple(p) for p in case['initial_fire']]
        if (len(set(points))!=len(points) or type(ignition_components) is not dict
                or set(ignition_components)!=set(points)
                or any(type(pos) is not tuple or len(pos)!=2 or any(type(v) is not int for v in pos) for pos in ignition_components)
                or any(k not in ('vegetation','built') for k in ignition_components.values())):
            raise ValueError('explicit component selection required for every initial ignition')
        components=copy.deepcopy(staged['components'])
        # Fresh means every represented pool starts unburned; caller-supplied
        # heat or age cannot smuggle a legacy or fabricated fire history.
        for row in components:
            for cell in row:
                for kind in ('vegetation','built'):
                    state=cell[kind]
                    if type(state) is dict and (state['heat']!=0 or state['age']!=0 or state['wet']!=0
                            or state['burned']!=0 or state['fuel']!=state['initial_fuel']):
                        raise ValueError('staging contains prior fire history')
        initial_supported=True
        for x,y in points:
            if not (0<=x<grid['width'] and 0<=y<grid['height']):raise ValueError('initial ignition outside source grid')
            value=components[y][x][ignition_components[(x,y)]]
            if type(value) is not dict or value['fuel']<=0:initial_supported=False
        if initial_supported:
            for x,y in points:components[y][x][ignition_components[(x,y)]].update(heat=.25,age=0)
        scaffold=_fresh_scaffold(parent,case['seed'],grid,staged['heights'],case['wind'],containment_config,pressure_scale)
        self._model=model.TwoComponentFireV3(scaffold,components,transfers=staged['transfers'],crossings=staged['crossings'],
            max_cells=grid['width']*grid['height'],warmup_steps=case['warmup_minutes'],day_horizon=day_horizon,opt_in=True)
        self._case=case;self._staging_identity=expected_staging_identity;self._private_audit=copy.deepcopy(staged['private_audit'])
        self._warmup_target=case['warmup_minutes'];self._warmup_done=0;self._minute=0
        self._weather_kind='warm' if self._warmup_target else 'day'
        self._weather_state=weather.initial_state(self._warm if self._warmup_target else self._profile)
        self._status='active';self._reason=None;self._poisoned=False
        if not initial_supported:self._status='censored';self._reason='unsupported_initial_component'
        else:
            reason=self._model._guard()
            if reason:self._status='censored';self._reason=reason
        self._identity={'version':VERSION,'owner_sha256':_sha(__file__),'weather_sha256':WEATHER_SOURCE_SHA256,
            'staging_identity_sha256':expected_staging_identity,'staging':ident,
            'draft_source_sha256':DRAFT_SOURCE_PIN,'weather_driver_sha256':WEATHER_DRIVER_PIN,
            'scenario_source_sha256':SCENARIO_SOURCE_PIN,'regional_fire_sha256':REGIONAL_FIRE_PIN,
            'parent_sha256':expected_parent_sha256,
            'catalog_sha256':expected_catalog_sha256,'drafts_sha256':drafts['sha256'],'case_sha256':case['sha256'],
            'seed':case['seed'],'ignition_selection_sha256':digest(sorted((x,y,v) for (x,y),v in ignition_components.items())),
            'model_input_sha256':self._model._input_sha256,'warmup_steps':case['warmup_minutes'],
            'day_horizon':day_horizon,'max_steps':self._model.max_steps}

    def _ensure(self):
        if self._poisoned:raise RuntimeError('owner indeterminate; restore a trusted checkpoint')
        try:
            _ensure_code()
            if _sha(__file__)!=self._identity['owner_sha256']:raise RuntimeError('owner source changed')
            self._model._ensure()
        except BaseException:self._poisoned=True;raise

    def situation(self):
        """Only timing and status; no source layers, coordinates or assumptions."""
        self._ensure()
        return {'version':VERSION,'status':self._status,'minute':self._minute,'warmup_steps':self._warmup_done,
                'scope':'private_unscored_fire_weather_only'}

    def step(self):
        self._ensure()
        if self._status!='active':raise RuntimeError('diagnostic cannot advance')
        try:
            reason=self._model._guard()
            if reason:
                self._status='censored';self._reason=reason;return self.situation()
            warming=self._weather_kind=='warm'
            if warming:
                next_weather=weather.advance(self._warm,self._weather_state,self._warmup_done+1)
                conditions=weather.conditions(self._warm,0)
            else:
                minute=self._minute+1;conditions=weather.conditions(self._profile,minute)
                row=copy.deepcopy(conditions);row.update(minute=0,received_at=0)
                selected=weather.WeatherProfile.from_records([row])
                previous=weather.initial_state(selected,moisture_proxy=self._weather_state['moisture_proxy'])
                updated=weather.advance(selected,previous,1)
                next_weather=weather.initial_state(self._profile,moisture_proxy=updated['moisture_proxy'])
                next_weather['minute']=minute
            self._model.fire.wind=tuple(conditions['wind'])
            self._model.fire.rules['spread_factor']=weather.spread_factor(next_weather,base_factor=.5)
            safe=self._model.step();self._weather_state=next_weather
            if warming:
                self._warmup_done+=1
                if safe and self._warmup_done==self._warmup_target:
                    self._weather_state=weather.initial_state(self._profile,moisture_proxy=next_weather['moisture_proxy'])
                    self._weather_kind='day'
            else:self._minute+=1
            if not safe:self._status='censored';self._reason=self._model.reason
            elif not warming and self._minute==self._model.day_horizon:self._status='diagnostic_end'
            elif self._model.fire.tick-self._model._model_start_tick>=self._model.max_steps:
                raise RuntimeError('model exhausted before day horizon')
            return self.situation()
        except BaseException:self._poisoned=True;raise

    def checkpoint(self):
        """Private trusted checkpoint; censored states suppress component output."""
        self._ensure()
        if self._status not in ('active','diagnostic_end'):raise RuntimeError('checkpoint unavailable after censor')
        selected=self._warm if self._weather_kind=='warm' else self._profile
        value={'version':VERSION,'identity':copy.deepcopy(self._identity),'status':self._status,
            'warmup_done':self._warmup_done,'minute':self._minute,'weather_kind':self._weather_kind,
            'weather':weather.checkpoint(selected,self._weather_state),'model':self._model.checkpoint()}
        value['sha256']=api._hash(value);return value

    def restore(self,value,*,expected_checkpoint_sha256):
        """Restore only this owner's checkpoint, under an independently trusted digest."""
        try:
            v=copy.deepcopy(value)
            if (type(expected_checkpoint_sha256) is not str or len(expected_checkpoint_sha256)!=64
                or type(v) is not dict or set(v)!={'version','identity','status','warmup_done','minute','weather_kind','weather','model','sha256'}
                or v['version']!=VERSION or v['sha256']!=expected_checkpoint_sha256
                or api._hash({k:x for k,x in v.items() if k!='sha256'})!=expected_checkpoint_sha256
                or api._encoded(v['identity'])!=api._encoded(self._identity) or v['status'] not in ('active','diagnostic_end')
                or type(v['warmup_done']) is not int or not 0<=v['warmup_done']<=self._warmup_target
                or type(v['minute']) is not int or not 0<=v['minute']<=self._identity['day_horizon']
                or v['weather_kind'] not in ('warm','day')
                or (v['weather_kind']=='warm' and (self._warmup_target==0 or v['minute']!=0 or v['warmup_done']>=self._warmup_target))
                or (v['weather_kind']=='day' and v['warmup_done']!=self._warmup_target)):
                raise ValueError('owner checkpoint identity or time differs')
            _ensure_code();selected=self._warm if v['weather_kind']=='warm' else self._profile
            state=weather.restore(selected,v['weather'])
            if state['minute']!=(v['warmup_done'] if v['weather_kind']=='warm' else v['minute']):
                raise ValueError('weather checkpoint clock differs')
            restored=model.TwoComponentFireV3.restore(v['model'],expected_input_sha256=self._identity['model_input_sha256'],
                expected_checkpoint_sha256=v['model']['sha256'],opt_in=True)
            elapsed=v['warmup_done']+v['minute']
            if restored.fire.tick-restored._model_start_tick!=elapsed:
                raise ValueError('fire and weather clocks differ')
            if ((v['status']=='diagnostic_end') != (v['minute']==restored.day_horizon)
                    or (v['status']=='active' and elapsed>=restored.max_steps)
                    or restored.warmup_steps!=self._warmup_target
                    or restored.day_horizon!=self._identity['day_horizon']):
                raise ValueError('horizon and fire clock differ')
            conditions=weather.conditions(self._warm,0) if v['weather_kind']=='warm' else weather.conditions(self._profile,v['minute'])
            expected_factor=weather.spread_factor(state,base_factor=.5) if elapsed else .5
            if list(restored.fire.wind)!=conditions['wind'] or restored.fire.rules['spread_factor']!=expected_factor:
                raise ValueError('weather effects and fire state differ')
            self._model=restored;self._weather_state=state;self._weather_kind=v['weather_kind']
            self._warmup_done=v['warmup_done'];self._minute=v['minute'];self._status=v['status'];self._reason=None;self._poisoned=False
            if api._encoded(self.checkpoint())!=api._encoded(v):raise ValueError('owner checkpoint restoration differs')
        except BaseException:self._poisoned=True;raise


def run_bounded(owner,*,wall_seconds,step_limit):
    """Advance at most 240 steps and 600 seconds; one step may overrun.

    A wall or step limit leaves an active owner checkpointable. Censor and
    horizon statuses remain terminal and are never rewritten as a success.
    """
    import time
    if not isinstance(owner,FreshBuiltWaterEstimatedWeatherV1):raise TypeError('fresh v2 owner required')
    if (type(wall_seconds) not in (int,float) or not math.isfinite(wall_seconds) or not 0<wall_seconds<=600
            or type(step_limit) is not int or not 1<=step_limit<=240):
        raise ValueError('invalid bounded-run limits')
    start=time.monotonic();deadline=start+wall_seconds;steps=0;reason='step_limit'
    while steps<step_limit:
        if time.monotonic()>=deadline:reason='wall_limit';break
        if owner.situation()['status']!='active':reason='terminal';break
        if time.monotonic()>=deadline:reason='wall_limit';break
        owner.step();steps+=1
    if owner.situation()['status']!='active':reason='terminal'
    return {'status':owner.situation()['status'],'steps':steps,'stop_reason':reason,
            'wall_seconds':round(time.monotonic()-start,6)}
