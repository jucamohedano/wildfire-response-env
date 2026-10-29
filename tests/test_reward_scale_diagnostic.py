import copy
import json
from pathlib import Path

import pytest

from eval.analyze_reward_scaling import analyze


def source():
    return json.loads((Path(__file__).parents[1]/'results/luna-subagent-benchmark-20261026.json').read_text())


def test_diagnostic_preserves_scores_and_exposes_active_fire_and_reference_difference():
    original=source(); before=copy.deepcopy(original)
    result=analyze(original)
    assert original==before and result['score_changes_applied'] is False
    row=next(item for item in result['rows'] if item['family']=='overlapping_fires')
    assert row['active_fire_cells']==4
    assert row['additional_area_vs_recorded_dispatcher']==175
    assert row['recorded_score']>0.99


def test_heldout_or_inconsistent_rewards_are_rejected():
    artifact=source(); artifact['summary']['experiment']['split']='test'
    with pytest.raises(ValueError,match='development'):analyze(artifact)
    artifact=source();artifact['summary']['episodes'][0]['outcomes']['graded_score']=.5
    with pytest.raises(ValueError,match='components'):analyze(artifact)
