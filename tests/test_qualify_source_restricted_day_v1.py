"""Restricted results must never enter the full qualification gate."""
from eval.qualify_source_day_v2 import qualify as full_qualify
from eval.qualify_source_restricted_day_v1 import qualify as restricted_qualify, SCOPE, REWARD_VERSION

IDS=[f'case-{i}' for i in range(12)]

class Host:
    def __init__(self):self.minute=0
    def situation(self):return {'minute':self.minute,'status':'end_unscored' if self.minute==1440 else 'active'}
    def advance(self,minutes):self.minute+=minutes

class Policy:
    def act(self,host,obs):host.advance(60)

def run(qualifier,path,gate):
    pins = dict(expected_scope='full_source_environment', expected_reward_version='full-source-reward-v1') if qualifier is full_qualify else {}
    return qualifier(IDS,checkpoint=path,run_id='test',make_environment=lambda _:Host(),
                     make_policy=lambda _:Policy(),verify_outcome=gate,**pins)

def test_restricted_results_have_distinct_scope_and_full_runner_rejects(tmp_path):
    gate=lambda *_:dict(status='restricted_full_day',scope=SCOPE,reward_version=REWARD_VERSION,
                        verified=True,score_eligible=True,score=.75)
    restricted=run(restricted_qualify,tmp_path/'restricted',gate)
    assert restricted['scope']==SCOPE
    assert all(row['status']=='restricted_scored' for row in restricted['cases'])
    full=run(full_qualify,tmp_path/'full',gate)
    assert all(row['status']=='blocked' and row['reason']=='verification_failed' for row in full['cases'])

def test_unrestricted_and_mislabeled_gates_are_rejected(tmp_path):
    full=lambda *_:dict(status='full_day',verified=True,score_eligible=True,score=.9)
    report=run(restricted_qualify,tmp_path/'wrong',full)
    assert all(row['status']=='blocked' for row in report['cases'])
    bad=lambda *_:dict(status='restricted_full_day',scope=SCOPE,reward_version='wrong',verified=True,score_eligible=True,score=.9)
    report=run(restricted_qualify,tmp_path/'bad',bad)
    assert all(row['status']=='blocked' for row in report['cases'])


def test_restricted_resume_preserves_scope_and_scores(tmp_path):
    path=tmp_path/'resume'
    gate=lambda *_:dict(status='restricted_full_day',scope=SCOPE,reward_version=REWARD_VERSION,
                        verified=True,score_eligible=True,score=.75)
    first=run(restricted_qualify,path,gate)
    again=restricted_qualify(IDS,checkpoint=path,run_id='test',make_environment=lambda _:None,
                             make_policy=lambda _:None,verify_outcome=gate,resume=True)
    assert again==first and again['scope']==SCOPE
