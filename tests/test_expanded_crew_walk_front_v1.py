import math
from shapely.geometry import LineString, box
from shapely.strtree import STRtree
from eval import expanded_crew_walk_front_v1 as walk


def inputs():
    grid = {'transform': [500, 0, 0, 0, -500, 5000]}
    supported = {(x, y) for x in range(10) for y in range(10)}
    heights = [[100] * 10 for _ in range(10)]
    far = LineString([(9000, 9000), (9001, 9001)])
    water = {'bounds': box(-100, -100, 5100, 5100), 'land': box(-100, -100, 5100, 5100),
             'osm': STRtree([far]), 'dera': STRtree([far])}
    return grid, supported, heights, water


def test_upwind_walk_reaches_real_observed_boundary_without_entering_fire():
    grid, cells, heights, water = inputs()
    r = walk.plan(walk.center((1, 5), grid), [[4, 5]], [1, 0], cells, heights, grid, water)
    assert r['status'] == 'synthetic_walk_front_hypothesis'
    assert r['site_cell'] == [3, 5] and r['front_cell'] == [4, 5]
    assert r['walk_distance_m'] == 1240
    assert r['work_authorization'] == 'none' and r['road_return_to_base'] == 'unknown'
    assert walk.cell_of(r['site_position'], grid) == (3, 5)
    assert r['site_position'][0] == 1990


def test_downwind_cells_and_burning_cells_are_excluded_even_for_diagonal_wind():
    _, cells, _, _ = inputs()
    hazards, front = walk.hazards_and_front([[4, 5]], [1, 1], cells)
    assert {(4, 5), (5, 5), (4, 6)} <= hazards
    assert (5, 5) not in front and (4, 6) not in front
    assert (3, 5) in front and (4, 4) in front


def test_supported_mask_distance_slope_and_water_block_walks():
    grid, cells, heights, water = inputs()
    start = walk.center((1, 5), grid)
    cells = {(1, 5), (2, 5), (3, 5)}
    assert walk.plan(start, [[4, 5]], [1, 0], cells, heights, grid, water, max_walk=1000)['status'] == 'no_hypothesis_in_profile'
    heights[5][2] = 251
    assert walk.plan(start, [[4, 5]], [1, 0], cells, heights, grid, water)['status'] == 'no_hypothesis_in_profile'
    heights[5][2] = 100
    water['osm'] = STRtree([LineString([(1000, 2000), (1000, 2500)])])
    assert walk.plan(start, [[4, 5]], [1, 0], cells, heights, grid, water)['status'] == 'no_hypothesis_in_profile'
    cells.remove((2, 5))
    assert walk.plan(start, [[4, 5]], [1, 0], cells, heights, grid, water)['status'] == 'no_hypothesis_in_profile'


def test_missing_water_is_unknown_and_no_fire_rejected():
    import pytest
    grid, cells, heights, water = inputs()
    water['dera'] = STRtree([])
    with pytest.raises(ValueError, match='water evidence'):
        walk.plan(walk.center((1, 5), grid), [[4, 5]], [1, 0], cells, heights, grid, water)
    with pytest.raises(ValueError, match='nonempty'):
        walk.hazards_and_front([], [0, 0], cells)


def test_only_source_known_land_and_finite_heights_is_supported():
    v2 = {'cells': [[{'surface':'land', 'fuel_status':'source','source_fuel_class':1},
                     {'surface':'water', 'fuel_status':'source','source_fuel_class':1},
                     {'surface':'land', 'fuel_status':'estimated','source_fuel_class':1},
                     {'surface':'land', 'fuel_status':'source','source_fuel_class':1}]],
          'terrain_candidate': [[1, 2, 3, 4]]}
    v4 = {'cells': [{'column':1, 'row':0}, {'column':3, 'row':0}], 'additional_dera_censors': []}
    assert walk.supported_land(v2, v4) == {(0, 0)}


def test_changed_fire_and_wind_reject_previously_valid_start():
    grid, cells, heights, water = inputs()
    start = walk.center((3, 5), grid)
    assert walk.plan(start, [[4, 5]], [1, 0], cells, heights, grid, water)['status'] == 'synthetic_walk_front_hypothesis'
    assert walk.plan(start, [[4, 5]], [-1, 0], cells, heights, grid, water)['reason'] == 'unsupported_or_excluded_start'
    assert walk.plan(start, [[3, 5]], [0, 0], cells, heights, grid, water)['reason'] == 'unsupported_or_excluded_start'


def test_cli_rejects_nonprivate_destination_and_missing_opt_in(tmp_path):
    import subprocess, sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    script = root / 'eval/expanded_crew_walk_front_v1.py'
    for args in (['--output-dir', str(tmp_path/'private')],
                 ['--opt-in-synthetic-front-walk', '--output-dir', str(root/'assets'/'private')]):
        proc = subprocess.run([sys.executable, str(script), *args], cwd=root, capture_output=True)
        assert proc.returncode != 0
    assert not (tmp_path/'private').exists()
