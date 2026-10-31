"""Narrow actor facade for the unscored domain ground experiment."""
from wildfire_source_public_facade_v1 import redact_reports
from .host import DomainProfileGroundIncidentV1
from .walk import DomainSourceWalkV1
from .hold import DomainSourceWalkHoldV1

VERSION='wildfire-domain-source-ground-public-v1'
_ALLOWED=frozenset(('situation','route_options','clear_aircraft','dispatch','dispatch_aircraft',
                    'release','request_status','advance','dispatch_crew','retreat','start_hold'))


class PublicDomainGroundV1:
    """Allowlisted surface, not an in-process sandbox or a scoring API."""
    __slots__=('__host',)
    def __init__(self,host):
        if type(host) not in (DomainProfileGroundIncidentV1,DomainSourceWalkV1,DomainSourceWalkHoldV1):
            raise ValueError('exact domain ground component required')
        self.__host=host
    def reports(self,*,after_cursor=0,limit=20):
        return redact_reports(self.__host.reports(after_cursor=after_cursor,limit=limit))
    def __getattr__(self,name):
        if name not in _ALLOWED:raise AttributeError(name)
        return getattr(self.__host,name)
