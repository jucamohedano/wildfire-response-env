"""Redact legacy destination reports without changing private replay state."""
import json

import adapter_v1 as api
import pytest
from envs.wildfire_response.incident_day.events import EventBus
from wildfire_source_public_facade_v1 import PublicSourceFacadeV1
from wildfire_source_restricted_v1.host import RestrictedSourceIncidentV1
from wildfire_source_domain_v1.host import DomainProfileSourceIncidentV1
from wildfire_source_domain_v2.host import DomainProfileSourceIncidentV2
from wildfire_source_domain_v3.host import DomainProfileSourceIncidentV3


@pytest.mark.parametrize('host_type',[RestrictedSourceIncidentV1,DomainProfileSourceIncidentV1,
                                     DomainProfileSourceIncidentV2,DomainProfileSourceIncidentV3])
def test_legacy_destination_closure_is_redacted_without_mutation(host_type):
    host=object.__new__(host_type);host._ensure=lambda:None;host._status='active'
    host._bus=EventBus(1,['center'])
    host._bus.emit('observer','incoming_report',{'text':'ordinary observation'},0,delay=0)
    host._bus.emit('destination','destination_threatened',
        {'destination':{'id':'private-id','name':'private-name','position':[1111.25,2222.75],
                        'capacity':987654321,'note':'private-marker','status':'closed'}},
        0,location=[1111.25,2222.75],correlation_id='private-correlation',delay=0)
    host._bus.tick(0,{})
    before=api._encoded(host._bus.checkpoint())
    raw=host.reports(limit=10);assert raw[1]['payload']['destination']['id']=='private-id'
    actor=PublicSourceFacadeV1(host);reports=actor.reports(limit=10)
    assert reports[0]==raw[0] and len(reports)==len(raw)
    assert reports[1]['id']==raw[1]['id'] and reports[1]['observed_at']==raw[1]['observed_at']
    assert reports[1]['payload']=={'text':'A destination has closed.'}
    assert reports[1]['location'] is None and reports[1]['correlation_id'] is None
    public=json.dumps(reports)
    assert all(item not in public for item in ('private-id','private-name','private-marker',
        'private-correlation','1111.25','2222.75','987654321'))
    assert actor.reports(after_cursor=1,limit=1)==reports[1:]
    assert api._encoded(host._bus.checkpoint())==before
    with pytest.raises(AttributeError):getattr(actor,'checkpoint')
    with pytest.raises(AttributeError):getattr(actor,'transcript')


def test_unknown_or_subclass_host_fails_closed():
    class Subclass(RestrictedSourceIncidentV1):pass
    with pytest.raises(ValueError):PublicSourceFacadeV1(object())
    with pytest.raises(ValueError):PublicSourceFacadeV1(object.__new__(Subclass))


def test_scored_walk_and_hold_wrappers_also_redact(monkeypatch):
    from wildfire_source_restricted_scored_v1 import RestrictedSourceScoredV1
    from wildfire_source_walk_exercise_v1 import SourceWalkExerciseV1
    from wildfire_source_walk_hold_v1 import SourceWalkHoldV1
    bus=EventBus(1,['center'])
    bus.emit('destination','destination_threatened',{'destination':{'id':'private-id'}},0,delay=0)
    bus.tick(0,{})
    for cls in (RestrictedSourceScoredV1,SourceWalkExerciseV1,SourceWalkHoldV1):
        instance=object.__new__(cls)
        instance.reports=lambda **kwargs:bus.get_reports(**kwargs)
        assert PublicSourceFacadeV1(instance).reports()[0]['payload']=={'text':'A destination has closed.'}
