import pytest

from envs.wildfire_response.regional.ops.cloudrift import CloudRiftTransport, MODEL
from eval.cloudrift_single_call_trial import SingleCallCloudRiftTrial


def test_trial_retains_budget_and_model_guards_and_forwards_single_call_control():
    options=dict(api_key='unit-test-only-not-a-credential',
                 model_metadata=dict(id=MODEL, pricing=dict(prompt='0', completion='0')), usd_cap=1)
    with pytest.raises(ValueError, match='authorized'):
        SingleCallCloudRiftTrial(**options)
    payload=dict(model=MODEL, messages=[], tools=[], max_tokens=10)
    old=CloudRiftTransport(**options).request_body(payload)
    trial=SingleCallCloudRiftTrial(allow_trial=True, **options)
    new=trial.request_body(payload)
    assert 'parallel_tool_calls' not in old
    assert new.pop('parallel_tool_calls') is False and new==old
    assert trial.calls==0 and trial.spent==0
    with pytest.raises(ValueError, match='substitution'):
        trial.request_body(dict(payload, model='unapproved-model'))
    with pytest.raises(ValueError, match='cap'):
        SingleCallCloudRiftTrial(allow_trial=True, **dict(options, usd_cap=6))
