"""Opt-in transport experiment; no provider request is made by importing this file.

Preserves the frozen transport and historical results. Actual provider acceptance
and tool parsing require a separately authorized, budgeted live trial.
"""

from envs.wildfire_response.regional.ops.cloudrift import CloudRiftTransport


class SingleCallCloudRiftTrial(CloudRiftTransport):
    def __init__(self, *, allow_trial=False, **options):
        if allow_trial is not True:
            raise ValueError('A separately authorized single-call trial is required')
        super().__init__(**options)

    def request_body(self, payload):
        body = super().request_body(payload)
        body['parallel_tool_calls'] = False
        return body
