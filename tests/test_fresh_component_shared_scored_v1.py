"""Private synthetic scorer integration; no real-source qualification."""
import pytest
import adapter_v1 as api
from test_fresh_component_shared_v1 import make
from wildfire_fresh_component_shared_v1 import scored


def test_component_score_mask_checkpoint_continuation_and_privacy(monkeypatch):
    owner,_=make(monkeypatch);mask=scored.fixed_mask(owner);identity=scored.configuration_identity(owner,mask)
    env=scored.FreshComponentScoredV1(owner,scoring_mask=mask,expected_identity=identity,opt_in=True)
    assert mask and 12*84+13 not in mask
    assert 'score' not in env.situation()
    env.protect('cohort-1','shelter');snapshot=env.checkpoint();expected=env.advance(1)
    fresh,_=make(monkeypatch);mask2=scored.fixed_mask(fresh);ident2=scored.configuration_identity(fresh,mask2)
    clone=scored.FreshComponentScoredV1(fresh,scoring_mask=mask2,expected_identity=ident2,opt_in=True)
    clone.restore(snapshot,expected_checkpoint_sha256=api._hash(snapshot))
    assert clone.advance(1)==expected
    assert scored.verify_outcome(clone,fresh_owner=lambda:None,expected_identity=identity,scoring_mask=mask)['status']=='unfinished'


def test_full_day_verifier_control_flow_with_synthetic_clock(monkeypatch):
    """Exercise the verifier with a deterministic fake clock, not a real incident."""
    from types import SimpleNamespace
    from envs.wildfire_response.incident_day.outcomes import OutcomeCounter
    mask=[0,1,2,3,4];identity={'mask':scored.mask_sha256(mask)}
    def initialize(self,owner,*,scoring_mask,expected_identity,opt_in):
        self._identity=expected_identity;self._owner=owner;self._calls=[];self._censored=False
        groups=owner._host._ops.groups
        self._counter=OutcomeCounter([0],mask,groups,1.)
        self._audit=[dict(minute=0,footprint=[0],burnable=mask,cell_area_hectares=1.,new_footprint=[],
            population={'cohort-1':dict(count=10,exposed=0,status='at_home',protection_pending=False)})]
    def advance(self,n):
        for _ in range(n):
            h=self._owner._host;h._minute+=1
            if h._minute==1:self._counter.update([1]);h._ops.groups['group']['exposed']=True
            self._audit.append(dict(minute=h._minute,new_footprint=[1] if h._minute==1 else [],
                population={'cohort-1':dict(count=10,exposed=10,status='at_home',protection_pending=False)}))
        if h._minute==1440:h._status='end_unscored'
        result={'minute':h._minute};self._calls.append(dict(name='advance',args=[n],result=result));return result
    def checkpoint(self):return dict(minute=self._owner._host._minute,audit=self._audit,calls=self._calls,counter=self._counter.checkpoint())
    monkeypatch.setattr(scored.FreshComponentScoredV1,'__init__',initialize)
    monkeypatch.setattr(scored.FreshComponentScoredV1,'advance',advance)
    monkeypatch.setattr(scored.FreshComponentScoredV1,'checkpoint',checkpoint)
    monkeypatch.setattr(scored.FreshComponentScoredV1,'_ensure',lambda self:None)
    def fresh():
        ops=SimpleNamespace(groups={'group':dict(count=10,exposed=False,status='at_home')},orders={})
        return SimpleNamespace(_host=SimpleNamespace(_minute=0,_status='active',_ops=ops))
    env=scored.FreshComponentScoredV1(fresh(),scoring_mask=mask,expected_identity=identity,opt_in=True)
    for _ in range(24):env.advance(60)
    result=scored.verify_outcome(env,fresh_owner=fresh,expected_identity=identity,scoring_mask=mask)
    assert result['status']=='synthetic_fresh_component_full_day' and result['score']==pytest.approx(.15)
    env._calls[-1]['result']={'minute':0}
    assert scored.verify_outcome(env,fresh_owner=fresh,expected_identity=identity,scoring_mask=mask)['status']=='verification_failed'


def test_component_censor_cannot_be_score_eligible_and_can_restore(monkeypatch):
    import copy
    from eval import incident_weather_v1 as weather
    owner,_=make(monkeypatch,boundary_unknown=True);f=owner._host._fresh;model=f._model
    conditions=weather.conditions(f._profile,1);row=copy.deepcopy(conditions);row.update(minute=0,received_at=0)
    selected=weather.WeatherProfile.from_records([row]);previous=weather.initial_state(selected,moisture_proxy=f._weather_state['moisture_proxy'])
    updated=weather.advance(selected,previous,1);model.fire.wind=tuple(conditions['wind'])
    model.fire.rules['spread_factor']=weather.spread_factor(updated,base_factor=.5)
    model.fire.rules['ignition_probability_per_eligible_cell']=100.
    model._components[12][12]['vegetation']['age']=model.fire.spread_interval(1,0)-1
    model.fire.rules['spread_factor']=.5;model._sync_projection()
    owner._walk._seal=api._hash(owner._host.checkpoint());owner._seal=api._hash(owner._hold.checkpoint())
    mask=scored.fixed_mask(owner);identity=scored.configuration_identity(owner,mask)
    env=scored.FreshComponentScoredV1(owner,scoring_mask=mask,expected_identity=identity,opt_in=True);saved=env.checkpoint()
    assert env.protect('cohort-1','shelter')['status']=='censored'
    verdict=scored.verify_outcome(env,fresh_owner=lambda:None,expected_identity=identity,scoring_mask=mask)
    assert verdict['status']=='censored' and verdict['score_eligible'] is False and verdict['score'] is None
    with pytest.raises(api.CensoredError):env.reports()
    env.restore(saved,expected_checkpoint_sha256=api._hash(saved));assert env.checkpoint()==saved
