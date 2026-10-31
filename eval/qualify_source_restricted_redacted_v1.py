"""Restricted runner adapter that redacts legacy destination reports for policies.

The existing runner, verifier and state adapter continue to receive the exact
private host. The new run-id namespace prevents reuse of an older checkpoint.
"""
from eval import qualify_source_restricted_day_v1 as legacy
from wildfire_source_public_facade_v1 import PublicSourceFacadeV1, redact_reports

VERSION='wildfire-source-restricted-redacted-runner-v1'
_PREFIX=VERSION+':'
_COMMANDS=legacy._COMMANDS
BlockedCase=legacy.BlockedCase


class _PolicyHost:
    __slots__=('__public',)
    def __init__(self,public):self.__public=public
    def reports(self,*,after_cursor=0,limit=20):
        return redact_reports(self.__public.reports(after_cursor=after_cursor,limit=limit))
    def __getattr__(self,name):
        if name not in _COMMANDS:raise AttributeError(name)
        return getattr(self.__public,name)


class _Policy:
    __slots__=('inner',)
    def __init__(self,inner):self.inner=inner
    def act(self,host,observation):return self.inner.act(_PolicyHost(host),observation)


def _validate(host):
    # Exact supported hosts have reviewed situation/action response shapes.
    # The facade is constructed for validation; the private verifier and state
    # adapter retain the original host and checkpoints, not a rewritten object.
    PublicSourceFacadeV1(host)
    return host


class _Adapter:
    def __init__(self,inner):self._inner=inner
    def capture(self,host,policy):return self._inner.capture(host,None if policy is None else policy.inner)
    def seal(self,body):return self._inner.seal(body)
    def open(self,sealed):return self._inner.open(sealed)
    def restore(self,case_id,state):
        host,policy=self._inner.restore(case_id,state)
        return _validate(host),None if policy is None else _Policy(policy)


def qualify(case_ids,*,run_id,make_environment,make_policy,state_adapter=None,**kwargs):
    """Use the legacy runner with redacted policy reports and a distinct run ID."""
    if type(run_id) is not str or not run_id:raise ValueError('public run_id required')
    def factory(case_id):return _validate(make_environment(case_id))
    def policy(case_id):return _Policy(make_policy(case_id))
    adapter=None if state_adapter is None else _Adapter(state_adapter)
    return legacy.qualify(case_ids,run_id=_PREFIX+run_id,make_environment=factory,
                          make_policy=policy,state_adapter=adapter,**kwargs)
