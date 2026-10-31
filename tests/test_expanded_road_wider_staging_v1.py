"""Wider staging is a bounded synthetic sensitivity, not access permission."""
from pathlib import Path
import subprocess
import sys

import pytest
from eval import expanded_road_wider_staging_v1 as wider
from eval import expanded_road_staging_v1 as staging
from envs.wildfire_response.regional.ops.generator import digest
from test_expanded_predeployed_handover_v1 import fixture


def test_wider_heuristic_finds_a_more_distant_source_node_without_work_permission():
    args = list(fixture())
    args[1]['sector_grid'] = [10, 4]
    assert not staging.candidate_nodes(args[4], args[5], args[6], args[3]['public_fire'],
                                       args[3]['wind'], args[1]['sector_grid'], args[10])
    result = wider.compare_case(*args)
    assert result['variants']
    for v in result['variants']:
        assert v['version'] == wider.VERSION and v['work_authorization'] == 'none'
        assert v['handover_route'] is None and v['evidence']['return_to_base'] == 'unknown'
        assert v['cell_access'] == 'unknown' and v['work_target'] == 'unknown'
        assert v['sha256'] == digest({k: x for k, x in v.items() if k != 'sha256'})


def test_wider_selection_preserves_hazard_and_supported_cell_guards():
    args = fixture()
    nodes = wider.wider_nodes(args[4], args[5], args[6], args[3]['public_fire'],
                              args[3]['wind'], args[1]['sector_grid'], args[10])
    assert nodes
    assert not wider.wider_nodes(args[4], args[5], args[6], args[3]['public_fire'],
                                args[3]['wind'], args[1]['sector_grid'], set())
    fire = [list(args[5]._cell(args[4].nodes[n])) for n in nodes]
    assert not wider.wider_nodes(args[4], args[5], args[6], fire, [0, 0], args[1]['sector_grid'], args[10])


def test_override_restored_on_exception():
    original = staging.candidate_nodes
    with pytest.raises(RuntimeError):
        with wider.wider_selection():
            assert staging.candidate_nodes is wider.wider_nodes
            raise RuntimeError()
    assert staging.candidate_nodes is original


def test_cli_requires_opt_in_and_private_output(tmp_path):
    root = Path(__file__).resolve().parents[1]
    script = root / 'eval/expanded_road_wider_staging_v1.py'
    for arguments in (['--output-dir', str(tmp_path / 'private')],
                      ['--opt-in-wider-synthetic-staging', '--output-dir', str(root / 'assets' / 'private')]):
        result = subprocess.run([sys.executable, str(script), *arguments], cwd=root, capture_output=True)
        assert result.returncode != 0
    assert not (tmp_path / 'private').exists()


def test_wider_search_keeps_water_conflict_and_budget_unknown(monkeypatch):
    from shapely.geometry import LineString
    from shapely.strtree import STRtree
    args = list(fixture())
    args[1]['sector_grid'] = [10, 4]
    args[8]['osm'] = STRtree([LineString([(290800, 4062280), (292000, 4062280)])])
    result = wider.compare_case(*args)
    assert result['status'] == 'blocked' and not result['variants']
    assert result['reasons']['osm_mapped_water_or_buffer_conflict']
    args = list(fixture())
    from eval import expanded_predeployed_handover_v1 as pre
    monkeypatch.setattr(pre, 'MAX_SCREENS', 0)
    result = wider.compare_case(*args)
    assert result['status'] == 'search_incomplete_unknown' and not result['variants']
