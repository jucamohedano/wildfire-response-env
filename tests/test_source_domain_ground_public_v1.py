"""Domain ground actor facade excludes private controls and closure records."""
import pytest
from envs.wildfire_response.incident_day.events import EventBus
from wildfire_source_domain_ground_v1.public import PublicDomainGroundV1
from wildfire_source_domain_ground_v1.host import DomainProfileGroundIncidentV1
from wildfire_source_domain_ground_v1.walk import DomainSourceWalkV1
from wildfire_source_domain_ground_v1.hold import DomainSourceWalkHoldV1


@pytest.mark.parametrize('cls',[DomainProfileGroundIncidentV1,DomainSourceWalkV1,DomainSourceWalkHoldV1])
def test_public_facade_redacts_and_hides_checkpoint(cls):
    host=object.__new__(cls);bus=EventBus(1,['center'])
    bus.emit('destination','destination_threatened',{'destination':{'id':'secret','position':[17,19]}},0,delay=0)
    bus.tick(0,{})
    host.reports=lambda **kw:bus.get_reports(**kw)
    actor=PublicDomainGroundV1(host)
    assert actor.reports()[0]['payload']=={'text':'A destination has closed.'}
    assert 'secret' not in str(actor.reports())
    with pytest.raises(AttributeError):getattr(actor,'checkpoint')
    with pytest.raises(AttributeError):getattr(actor,'transcript')


def test_public_facade_rejects_unrelated_host():
    with pytest.raises(ValueError):PublicDomainGroundV1(object())
