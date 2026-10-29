import pytest
from eval.qwen3_protocol import parse_call,execute_with_deadline

GOOD='<tool_call>{"name":"advance_time","arguments":{"max_minutes":60}}</tool_call>'

def test_single_complete_envelope():
    assert parse_call('\n'+GOOD+'\n')['name']=='advance_time'

@pytest.mark.parametrize('response',[GOOD+GOOD,'prose '+GOOD,GOOD+' trailing',
    '{"name":"advance_time","arguments":{}}','<tool_call>{"name":"x","name":"y","arguments":{}}</tool_call>',
    '<tool_call>{"name":"x","arguments":{"n":NaN}}</tool_call>',
    '<tool_call>{"name":"x","arguments":{"n":1e309}}</tool_call>'])
def test_ambiguous_and_nonfinite_responses_are_rejected(response):
    with pytest.raises(ValueError):parse_call(response)

def test_expired_response_is_never_executed():
    calls=[]
    assert execute_with_deadline(lambda:calls.append(1),10,lambda:10)==(False,None,False)
    assert not calls

def test_inflight_action_overrun_is_explicit_and_ineligible():
    clock=iter([9.,11.]);calls=[]
    assert execute_with_deadline(lambda:calls.append(1) or 'done',10,lambda:next(clock))==(True,'done',False)
    assert calls==[1]
