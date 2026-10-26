import unittest

from eval.run_benchmark import experiment_spec, grouped_interval, summarize


class BenchmarkRunnerTests(unittest.TestCase):
    def test_diagnostic_action_cadence_cannot_enter_ranked_evaluation(self):
        from types import SimpleNamespace
        diagnostic=SimpleNamespace(_max_decisions=120,_ticks_per_action=1)
        with self.assertRaisesRegex(ValueError,'two-minute'):
            experiment_spec(diagnostic,'validation',('passive',),[],False)

    def row(self,policy='safe',safe=True,area=100,score=1,group='a'):
        return dict(policy=policy,score_eligible=True,safe_success=safe,
                    protected_population_fraction=1 if safe else .9,
                    additional_burned_hectares=area,graded_score=score,
                    group_id=group,invalid_actions=0)

    def test_safety_ranking_overrides_scalar_and_partial_runs_never_rank(self):
        rows=[self.row('safe',True,1000,.8),self.row('unsafe',False,0,.95),self.row('partial',True,0,1)]
        board=summarize(rows,{'safe':1,'unsafe':1,'partial':2})
        self.assertEqual([row['policy'] for row in board],['safe','unsafe','partial'])
        self.assertFalse(board[-1]['rank_eligible'])
        self.assertEqual(board[-1]['safe_episode_rate'],.5)

    def test_bootstrap_resamples_configuration_groups_not_seed_rows(self):
        rows=[self.row(score=.2,group='a'),self.row(score=.8,group='a')]
        self.assertEqual(grouped_interval(rows,'graded_score'),[.5,.5])

    def test_abandonment_incomplete_matrix_and_limited_split_do_not_rank(self):
        abandoned=dict(self.row('abandoned',False,0,0),invalid_abandonment=True,protected_population_fraction=1)
        rows=[abandoned,self.row('valid',False,100,.9)]
        board=summarize(rows)
        self.assertEqual(board[0]['policy'],'valid')
        self.assertFalse(board[1]['rank_eligible'])
        self.assertTrue(all(not row['rank_eligible'] for row in summarize(rows,complete=False)))
        self.assertTrue(all(not row['rank_eligible'] for row in summarize(rows,full_split=False)))
