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
    assert '1 of 6 bounded cases have stopped' in output
    assert 'stopping point is minute 10 of 1440' in output
    assert 'cannot be ranked against completed baselines' in output


def test_report_uses_recorded_bounds(tmp_path):
    import json
    from eval.render_qwen3_day_report import make_report
    (tmp_path/'summary.json').write_text(json.dumps({'context_tokens':32768,'max_new_tokens':768,'case_wall_seconds':900,'global_wall_seconds':7200,'max_model_calls':1200}))
    b=tmp_path/'b.json';b.write_text('{}')
    output=make_report(tmp_path,b)
    assert 'context 32768 tokens' in output and 'context 12,000 tokens' not in output
    assert 'python -m eval.run_qwen3_day' in output
    assert '0217aef' in output and '--case-seconds 900' in output


def test_pre_dispatch_contract_error_is_not_an_engine_commit():
    rows=[{'tool':'commit_orders','result':{'error':'Missing action','committed':False}}]
    assert error_breakdown(rows)=={'tool_contract_error':1}


def test_repeated_rejected_drafts_group_without_hiding_feedback():
    row={'tool':'check_action','minute':0,'arguments':{'action':{}},'result':{'error':'Invalid <route>'}}
    output=public_timeline([row,row,row])
    assert '×3 repeated' in output and 'Invalid &lt;route&gt;' in output


def test_completed_grade_requires_independent_request_replay():
    from eval.render_qwen3_day_report import verified
    row={'completed':True,'minute':1440,'truncated':False,
         'outcomes':{'score_eligible':True},
         'verification':{'passed':True,'exact_replay':True,'independent_reward_agreement':True}}
    assert verified(row) is False
    row['request_replay']={'passed':True,'score_eligible':False}
    assert verified(row) is False
    row['request_replay']['score_eligible']=True
    assert verified(row) is True


def test_feedback_distinguishes_delayed_population_state_and_completed_order():
    from eval.render_qwen3_day_report import public_feedback
    value=public_feedback({'tool':'get_situation','result':{'districts':[{'id':'p0','status':'unwarned','observed_at':0,'received_at':0}],
        'protection_orders':[{'group_id':'p0','state':'completed','observed_at':6,'received_at':10}],
        'offers':[],'private_audit':'DO_NOT_EXPOSE'}})
    assert value['groups'][0]['status']=='unwarned'
    assert value['orders'][0]['state']=='completed' and value['orders'][0]['received_at']==10
    assert 'private_audit' not in value


def test_stale_evacuation_warning_matches_the_same_group_and_delivered_order():
    from eval.render_qwen3_day_report import has_stale_completed_evacuation
    row = {'tool': 'get_situation', 'result': {
        'districts': [{'id': 'p0', 'status': 'unwarned'}],
        'protection_orders': [{'group_id': 'p0', 'action': 'evacuate',
                               'state': 'completed', 'received_at': 10}]}}
    assert has_stale_completed_evacuation(row)
    row['result']['protection_orders'][0]['group_id'] = 'p1'
    assert not has_stale_completed_evacuation(row)
    row['result']['protection_orders'][0]['group_id'] = 'p0'
    row['result']['protection_orders'][0]['received_at'] = None
    assert not has_stale_completed_evacuation(row)


def test_replay_outcome_summary_is_partial_and_uses_recorded_counts():
    from eval.render_qwen3_day_report import replay_outcome_summary
    text = replay_outcome_summary({'population_by_status': {'arrived': 350},
                                   'resources_with_applied_work': 1, 'active_fire_cells': 25})
    assert '350 simulated people had arrived' in text
    assert '1 resource(s) had performed suppression work' in text
    assert '25 fire cells remained active' in text
    assert replay_outcome_summary({}) == ''


def test_reused_offer_only_counts_prior_accepted_commits():
    from eval.render_qwen3_day_report import rejected_reused_authorizations
    def row(offer, error):
        return {'tool':'commit_orders', 'arguments':{'action':{'authorizations':[offer]}},
                'result':{'info':{'error':error}}}
    rejected = 'Authorize a live offered allocation'
    assert rejected_reused_authorizations([row('a', rejected)]) == 0
    assert rejected_reused_authorizations([row('a', None), row('b', rejected)]) == 0
    assert rejected_reused_authorizations([row('a', None), row('a', rejected)]) == 1
