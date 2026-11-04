import pytest
from eval.qwen3_history import trim_once, MARKER


def test_latest_tool_feedback_survives_all_context_trimming():
    system={'role':'system','content':'rules'}
    latest=[{'role':'assistant','tool_calls':[{'function':{'name':'get_situation'}}]},
            {'role':'tool','content':'CURRENT_DELIVERED_STATE'}]
    messages=[system,{'role':'user','content':'OLD_INITIAL_STATE'},
              {'role':'assistant','content':'old call'},{'role':'tool','content':'OLD_RESULT'},*latest]
    first=trim_once(messages)
    assert first[-2:]==latest and first[1]['content']==MARKER
    second=trim_once(first)
    assert second==[system,{'role':'user','content':MARKER},*latest]
    with pytest.raises(ValueError,match='Latest tool response'):
        trim_once(second)


def test_oversize_initial_state_is_not_silently_dropped():
    with pytest.raises(ValueError,match='Initial situation'):
        trim_once([{'role':'system'},{'role':'user','content':'large'}])
