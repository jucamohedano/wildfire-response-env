"""Private scoring checks on synthetic component stand-ins."""
import pytest
import adapter_v1 as api
from test_domain_population_host_v2 import make
from wildfire_containment_score_v1.scoring import recompute
import wildfire_domain_population_scored_v2 as scored


def build(monkeypatch):
    owner,_=make(monkeypatch)
    # The minimal ground fixture has no facilities; exercise the real static
    # binding with an explicit empty synthetic facility inventory.
    owner._host._ops.profile['facilities']={}
    owner._walk._seal=api._hash(owner._host.checkpoint())
    owner._seal=api._hash(owner._hold.checkpoint())
    mask=scored.fixed_mask(owner)
    identity=scored.configuration_identity(owner,mask)
    return scored.DomainPopulationScoredV2(owner,scoring_mask=mask,expected_identity=identity,opt_in=True),mask,identity


def test_mask_privacy_rejected_command_restore_and_continuation(monkeypatch):
    env,mask,identity=build(monkeypatch);h=env._owner._host
    assert 12*16+13 not in mask and 0 in mask
    assert all('private-group' not in row['population'] for row in env._audit)
    public=scored.PublicDomainPopulationScoredV2(env)
    with pytest.raises(AttributeError):public.checkpoint
    assert 'score' not in public.situation()
    before=env.checkpoint()
    with pytest.raises(ValueError):env.advance(0)
    assert env.checkpoint()==before
    env.protect('cohort-1','shelter');env.advance(3)
    snapshot=env.checkpoint();expected=env.advance(1)
    clone,mask2,identity2=build(monkeypatch)
    assert mask2==mask and identity2==identity
    clone.restore(snapshot,expected_checkpoint_sha256=api._hash(snapshot))
    assert clone.advance(1)==expected
    assert scored.verify_outcome(clone,fresh_owner=lambda:None,expected_identity=identity,scoring_mask=mask)['status']=='unfinished'


def test_independent_80_20_and_distinct_scope():
    first=dict(minute=0,footprint=[0],burnable=[0,1,2,3,4],cell_area_hectares=1.,new_footprint=[],
        population={'cohort-1':dict(count=10,exposed=0,status='at_home',protection_pending=False)})
    rows=[first]
    for minute in range(1,1441):
        rows.append(dict(minute=minute,new_footprint=[1] if minute==1 else [],
            population={'cohort-1':dict(count=10,exposed=10 if minute>=1 else 0,status='at_home',protection_pending=False)}))
    result=recompute(rows,scoring_mask=[0,1,2,3,4],terminal=True,abandoned=False,censored=False)
    assert result['graded_score']==pytest.approx(.2*.75)
    assert scored.REWARD_VERSION!='wildfire-restricted-no-ground-no-evac-outcomes-v1'
    assert scored.SCOPE=='synthetic_single_candidate_domain_ground_population'


def test_source_fuel_does_not_promote_mixed_surface_into_mask(monkeypatch):
    from types import SimpleNamespace
    owner,_=make(monkeypatch);inputs=owner._host._domain_inputs;c=inputs.base.contract
    surface=[list(row) for row in c.surface];surface[0][0]='mixed'
    inputs.base=SimpleNamespace(contract=SimpleNamespace(width=c.width,height=c.height,domain=c.domain,
        surface=surface,fire_support=c.fire_support,elevation=c.elevation))
    assert 0 not in scored.fixed_mask(owner)


def test_full_day_verifier_control_flow_with_synthetic_clock(monkeypatch):
    """Exercise the verifier with a deterministic fake clock, not a real incident."""
    from types import SimpleNamespace
    from envs.wildfire_response.incident_day.outcomes import OutcomeCounter
    mask=[0,1,2,3,4];identity={'mask':scored.mask_sha256(mask)}
    def initialize(self,owner,*,scoring_mask,expected_identity,opt_in):
        self._identity=expected_identity;self._owner=owner;self._calls=[]
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
    monkeypatch.setattr(scored.DomainPopulationScoredV2,'__init__',initialize)
    monkeypatch.setattr(scored.DomainPopulationScoredV2,'advance',advance)
    monkeypatch.setattr(scored.DomainPopulationScoredV2,'checkpoint',checkpoint)
    monkeypatch.setattr(scored.DomainPopulationScoredV2,'_ensure',lambda self:None)
    def fresh():
        ops=SimpleNamespace(groups={'group':dict(count=10,exposed=False,status='at_home')},orders={})
        return SimpleNamespace(_host=SimpleNamespace(_minute=0,_status='active',_ops=ops))
    env=scored.DomainPopulationScoredV2(fresh(),scoring_mask=mask,expected_identity=identity,opt_in=True)
    for _ in range(24):env.advance(60)
    result=scored.verify_outcome(env,fresh_owner=fresh,expected_identity=identity,scoring_mask=mask)
    assert result['status']=='synthetic_domain_population_v2_full_day' and result['score']==pytest.approx(.15)
    env._calls[-1]['result']={'minute':0}
    assert scored.verify_outcome(env,fresh_owner=fresh,expected_identity=identity,scoring_mask=mask)['status']=='verification_failed'
