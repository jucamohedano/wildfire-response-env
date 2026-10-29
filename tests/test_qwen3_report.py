from eval.render_qwen3_day_report import error_breakdown, public_timeline


def test_error_categories_include_committed_feedback_but_not_null_errors():
    rows=[{'tool':'check_action','result':{'valid':True,'error':None}},
          {'tool':'check_action','result':{'valid':False,'error':'pending'}},
          {'tool':'commit_orders','result':{'info':{'error':'pending'}}},
          {'error':'invalid JSON'}, {'tool':'get_reports','result':[]}]
    assert error_breakdown(rows)=={'draft_rejection':1,'invalid_commit':1,'malformed_response':1}


def test_public_timeline_escapes_arguments_and_excludes_response_and_result():
    value=public_timeline([{'tool':'get_reports','minute':3,'arguments':{'id':'<script>'},
                           'response':'PRIVATE_RESPONSE','result':{'audit':'PRIVATE_AUDIT'}}])
    assert '<script>' not in value and '&lt;script&gt;' in value
    assert 'PRIVATE_RESPONSE' not in value and 'PRIVATE_AUDIT' not in value


def test_partial_run_never_earns_a_score_or_leaks_raw_replies(tmp_path):
    import json
    from eval.render_qwen3_day_report import make_report
    case='incident-day-realmap-v2-000'
    (tmp_path/'summary.json').write_text(json.dumps({'status':'running','cases':[{
        'case':case,'minute':10,'completed':False,'outcomes':{'graded_score':98765.4321,'score_eligible':True}}]}))
    directory=tmp_path/case;directory.mkdir()
    (directory/'requests.jsonl').write_text(json.dumps({'tool':'get_situation','minute':10,'arguments':{},
        'response':'PRIVATE_RESPONSE','result':{'private_audit':'PRIVATE_AUDIT'}})+'\n')
    baseline=tmp_path/'baseline.json';baseline.write_text('{}')
    output=make_report(tmp_path,baseline)
    assert '98765.4321' not in output and 'Unscored' in output
    assert 'PRIVATE_RESPONSE' not in output and 'PRIVATE_AUDIT' not in output
    assert 'Queued; not yet attempted' in output
