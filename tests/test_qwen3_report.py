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
