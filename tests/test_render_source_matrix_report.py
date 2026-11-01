import copy
import json
import os
import subprocess
import sys

import pytest

from eval.render_source_matrix_report import render


def report():
    ident = dict(runtime_id='source', case_id='case-001', policy='passive')
    return dict(status='incomplete', expected_cells=[ident, dict(ident, policy='stress')],
        cells=[dict(ident, status='complete', minute=1440, elapsed_seconds=12.5,
            verification=dict(passed=True, exact_replay=True, independent_reward_agreement=True),
            outcome=dict(score_eligible=True, completed_minutes=1440, graded_score=.75,
                protected_population_fraction=.5, new_exposed_population=2,
                inherited_exposed_population=3, additional_burned_hectares=4,
                inherited_burned_hectares=5, reward_version='v2'),
            pins=dict(inputs_sha256='input-pin', network_sha256='network-pin', scenario_sha256='scenario-pin'),
            operation_coverage={'route_options': dict(status='not_exercised', observed=0)},
            durations={'advance_time': dict(count=2, min_minutes=1, median_minutes=2, max_minutes=3)},
            findings=[dict(category='blocking', code='bad <route>', count=1)])],
        implementation_sha256='implementation-pin', validator_sha256='validator-pin',
        lifecycle_sha256='lifecycle-pin', package_sha256='package-pin',
        runtime_findings=[dict(runtime_id='source', case_id='case-001', policy='passive',
                               category='error', code='failure', count=1)],
        static_cases=[], limitations=['synthetic only'])


def test_render_outcomes_pins_findings_coverage_and_missing_cells():
    page = render(report())
    for text in ('0.75', '0.5', 'input-pin', 'scenario-pin', 'validator-pin', 'package-pin',
                 'route_options', 'not_exercised', 'advance_time', '12.5', 'failure',
                 'case-001 / stress', 'missing', 'unscored', 'synthetic only'):
        assert text in page
    assert 'bad &lt;route&gt;' in page
    assert 'bad <route>' not in page
    assert 'does not establish physical calibration' in page


@pytest.mark.parametrize('change', [
    lambda cell: cell.update(status='incomplete'),
    lambda cell: cell.update(minute=100),
    lambda cell: cell['outcome'].update(score_eligible=False),
    lambda cell: cell['outcome'].pop('completed_minutes'),
    lambda cell: cell['verification'].pop('exact_replay'),
])
def test_incomplete_ineligible_or_unverified_cells_are_unscored(change):
    data = report()
    change(data['cells'][0])
    page = render(data)
    assert '0.75' not in page
    assert 'unscored' in page


def test_missing_score_and_component_are_unscored():
    data = report()
    del data['cells'][0]['outcome']['graded_score']
    del data['cells'][0]['outcome']['protected_population_fraction']
    page = render(data)
    assert page.count('unscored') >= 2
    assert '0.75' not in page


def test_duplicate_identities_fail_instead_of_hiding_a_cell():
    data = report()
    data['cells'].append(copy.deepcopy(data['cells'][0]))
    with pytest.raises(ValueError, match='duplicate'):
        render(data)


def test_cli_creates_private_html_without_overwriting_input_or_existing_file(tmp_path):
    source = tmp_path / 'report.json'
    source.write_text(json.dumps(report()))
    output = tmp_path / 'review.html'
    command = [sys.executable, '-m', 'eval.render_source_matrix_report',
               '--report', str(source), '--output']
    result = subprocess.run(command + [str(output)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert output.stat().st_mode & 0o777 == 0o600
    assert 'case-001' in output.read_text()
    before = source.read_bytes()
    assert subprocess.run(command + [str(source)], capture_output=True).returncode != 0
    assert source.read_bytes() == before
    assert subprocess.run(command + [str(output)], capture_output=True).returncode != 0
