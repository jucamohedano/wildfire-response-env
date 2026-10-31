"""Legacy runner policy boundary redacts destination data only for actors."""
import pytest
from envs.wildfire_response.incident_day.events import EventBus
from wildfire_source_restricted_v1.host import RestrictedSourceIncidentV1
from eval.qualify_source_restricted_redacted_v1 import qualify,VERSION
from eval.qualify_source_restricted_day_v1 import SCOPE,REWARD_VERSION

IDS=[f'case-{i}' for i in range(12)]


def host():
    instance=object.__new__(RestrictedSourceIncidentV1)
    instance._ensure=lambda:None;instance._status='active';instance._minute=0
    instance._bus=EventBus(1,['center'])
    instance._bus.emit('destination','destination_threatened',
        {'destination':{'id':'private-id','position':[1111.25,2222.75],'capacity':987654321}},0,delay=0)
    instance._bus.tick(0,{})
    instance.situation=lambda:dict(minute=instance._minute,status=instance._status)
    def advance(n):
        instance._minute+=n
        if instance._minute==1440:instance._status='end_unscored'
        return instance.situation()
    instance.advance=advance
    return instance


class Policy:
    def act(self,actor,obs):
        records=actor.reports(limit=10)
        assert records[0]['payload']=={'text':'A destination has closed.'}
        assert 'private-id' not in str(records) and '1111.25' not in str(records)
        with pytest.raises(AttributeError):getattr(actor,'checkpoint')
        actor.advance(60)


def test_policy_redaction_keeps_verifier_and_checkpoint_private(tmp_path):
    path=tmp_path/'checkpoint';seen=[]
    def verify(case,raw):
        assert raw.reports(limit=10)[0]['payload']['destination']['id']=='private-id'
        seen.append(case)
        return dict(status='restricted_full_day',scope=SCOPE,reward_version=REWARD_VERSION,
                    verified=True,score_eligible=True,score=.5)
    kwargs=dict(checkpoint=path,run_id='test',make_environment=lambda _:host(),
                make_policy=lambda _:Policy(),verify_outcome=verify)
    result=qualify(IDS,**kwargs)
    assert result['run_id']==VERSION+':test' and seen==IDS
    assert all(row['status']=='restricted_scored' for row in result['cases'])
    assert qualify(IDS,**kwargs,resume=True)==result
    # A legacy run with the same user label cannot silently reuse this file.
    from eval.qualify_source_restricted_day_v1 import qualify as old_qualify
    with pytest.raises(ValueError):old_qualify(IDS,**kwargs,resume=True)


def test_unknown_host_is_blocked_without_running_policy(tmp_path):
    result=qualify(IDS,checkpoint=tmp_path/'state',run_id='bad',make_environment=lambda _:object(),
        make_policy=lambda _:pytest.fail('unreviewed host reached policy'),verify_outcome=lambda *_:None)
    assert all(row['status']=='blocked' for row in result['cases'])


def test_authenticated_continuation_passes_original_policy_to_adapter(tmp_path):
    import hashlib,hmac,json
    class Adapter:
        def __init__(self):self.restored=[]
        def capture(self,raw,policy):
            assert type(raw) is RestrictedSourceIncidentV1
            assert policy is None or type(policy) is Policy
            return json.dumps({'minute':raw._minute}).encode()
        def seal(self,body):return hmac.digest(b'test-key',body,hashlib.sha256)+body
        def open(self,sealed):
            if not hmac.compare_digest(sealed[:32],hmac.digest(b'test-key',sealed[32:],hashlib.sha256)):
                raise ValueError('bad signature')
            return sealed[32:]
        def restore(self,case,state):
            raw=host();raw._minute=json.loads(state)['minute'];self.restored.append(case)
            return raw,Policy()
    adapter=Adapter();path=tmp_path/'continuation'
    def verify(case,raw):
        assert raw.reports(limit=10)[0]['payload']['destination']['id']=='private-id'
        return dict(status='restricted_full_day',scope=SCOPE,reward_version=REWARD_VERSION,
                    verified=True,score_eligible=True,score=.5)
    kwargs=dict(checkpoint=path,run_id='resume',make_environment=lambda _:host(),
                make_policy=lambda _:Policy(),verify_outcome=verify,state_adapter=adapter)
    initial=qualify(IDS,**kwargs,max_decisions=1)
    assert all(row['minute']==60 and row['status']=='incomplete' for row in initial['cases'])
    completed=qualify(IDS,**kwargs,max_decisions=23,resume=True)
    assert adapter.restored==IDS
    assert all(row['status']=='restricted_scored' for row in completed['cases'])
