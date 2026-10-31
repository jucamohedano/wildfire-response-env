"""Actor-facing redaction for independently trusted legacy source hosts.

This is an accidental-access guard, not an in-process Python sandbox. Keep the
wrapped object, its checkpoint, transcript and private configuration away from
actors. No underlying host state or legacy replay behavior is modified.
"""
import copy
import importlib

VERSION='wildfire-source-public-redaction-facade-v1'
_TEXT='A destination has closed.'
_ALLOWED=frozenset(('situation','route_options','clear_aircraft','dispatch','dispatch_hold',
                    'dispatch_aircraft','protect','release','request_status','advance',
                    'dispatch_crew','retreat','start_hold'))
_REPORT_FIELDS=('id','source','role','channel','location','kind','payload','text',
                'observed_at','reported_at','received_at','urgent','actionable','correlation_id')


_TYPES={
    'wildfire_source_restricted_v1.host':'RestrictedSourceIncidentV1',
    'wildfire_source_domain_v1.host':'DomainProfileSourceIncidentV1',
    'wildfire_source_domain_v2.host':'DomainProfileSourceIncidentV2',
    'wildfire_source_domain_v3.host':'DomainProfileSourceIncidentV3',
    'wildfire_source_restricted_scored_v1':'RestrictedSourceScoredV1',
    'wildfire_source_walk_exercise_v1':'SourceWalkExerciseV1',
    'wildfire_source_walk_hold_v1':'SourceWalkHoldV1',
}


def _supported(host):
    cls=type(host);name=_TYPES.get(cls.__module__)
    return name is not None and getattr(importlib.import_module(cls.__module__),name,None) is cls


class PublicSourceFacadeV1:
    """Forward only actor operations, replacing private destination reports."""
    __slots__=('__host',)

    def __init__(self,host):
        if not _supported(host):raise ValueError('exact supported legacy source host required')
        self.__host=host

    def reports(self,*,after_cursor=0,limit=20):
        return redact_reports(self.__host.reports(after_cursor=after_cursor,limit=limit))

    def __getattr__(self,name):
        if name not in _ALLOWED:raise AttributeError(name)
        return getattr(self.__host,name)


def redact_reports(records):
    """Copy delivered records, redacting every destination-closure envelope."""
    if type(records) is not list:raise RuntimeError('unexpected report contract')
    result=[]
    for record in records:
        if type(record) is not dict:raise RuntimeError('unexpected report record')
        if record.get('kind')!='destination_threatened':
            result.append(copy.deepcopy(record));continue
        # Whitelist the stable EventBus envelope; remove private payload,
        # location, source extensions and correlation data entirely.
        if any(key not in record for key in _REPORT_FIELDS):raise RuntimeError('invalid closure report')
        clean={key:copy.deepcopy(record[key]) for key in _REPORT_FIELDS}
        clean.update(source='destination',location=None,correlation_id=None,
                     payload={'text':_TEXT},text=_TEXT)
        result.append(clean)
    return result
