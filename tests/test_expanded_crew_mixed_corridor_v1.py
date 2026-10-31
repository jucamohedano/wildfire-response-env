import copy
import os
from pathlib import Path
import subprocess
import sys

from shapely.geometry import LineString
from shapely.strtree import STRtree
from eval import expanded_crew_mixed_corridor_v1 as mixed
from eval import expanded_crew_walk_front_v1 as walk
from test_expanded_crew_walk_front_v1 import inputs


def test_only_admitted_source_fuel_mixed_cells_are_candidates():
    v2={'cells':[[{'surface':'land','fuel_status':'source','source_fuel_class':1},
                  {'surface':'unknown','fuel_status':'source','source_fuel_class':2},
                  {'surface':'unknown','fuel_status':'source','source_fuel_class':2},
                  {'surface':'unknown','fuel_status':'estimated','source_fuel_class':None}]],
        'terrain_candidate':[[1.,2.,3.,4.]]}
    v4={'cells':[{'column':1,'row':0},{'column':3,'row':0}],
        'additional_dera_censors':[{'column':2,'row':0}]}
    cells,mixed_cells=mixed.eligible_cells(v2,v4)
    assert cells=={(0,0),(1,0)} and (1,0) in mixed_cells


def test_exact_mixed_corridor_candidate_is_not_whole_cell_access():
    grid,cells,heights,water=inputs()
    admitted={(1,5),(2,5),(3,5)}
    result=mixed.plan(walk.center((1,5),grid),[[4,5]],[1,0],admitted,heights,grid,water)
    assert result['status']=='synthetic_mixed_corridor_hypothesis'
    assert result['walk_distance_m']==1240 and result['work_authorization']=='none'
    assert result['road_return_to_base']=='unknown'
    assert all(tuple(c) in admitted for c in result['walk_cells'])


def test_mapped_crossing_or_missing_layer_remains_unknown():
    grid,_,heights,water=inputs();cells={(1,5),(2,5),(3,5)}
    water['osm']=STRtree([LineString([(1000,2000),(1000,2500)])])
    result=mixed.plan(walk.center((1,5),grid),[[4,5]],[1,0],cells,heights,grid,water)
    assert result['status']=='search_incomplete_unknown'
    assert result['screen_reasons']['osm_mapped_water_corridor_conflict']
    grid,_,heights,water=inputs();water['dera']=STRtree([])
    result=mixed.plan(walk.center((1,5),grid),[[4,5]],[1,0],cells,heights,grid,water)
    assert result['status']=='search_incomplete_unknown'
    assert result['screen_reasons']['dera_water_layer_missing']


def test_bounds_grade_hazard_and_boundary_supercover_are_enforced():
    grid,_,heights,water=inputs();cells={(1,5),(2,5),(3,5)}
    start=walk.center((1,5),grid)
    assert mixed.plan(start,[[4,5]],[1,0],cells,heights,grid,water,max_walk=1000)['status']=='no_hypothesis_in_profile'
    heights[5][2]=251
    assert mixed.plan(start,[[4,5]],[1,0],cells,heights,grid,water)['status']=='no_hypothesis_in_profile'
    assert mixed.plan(start,[[1,5]],[0,0],cells,heights,grid,water)['reason']=='unsupported_or_excluded_start'
    # A source node on a cell edge does not silently snap away an unsupported cell.
    assert mixed.screen_segment([1000.,2250.],walk.center((2,5),grid),grid=grid,
        supported={(2,5)},hazards=set(),water=water)==('excluded','unsupported_centerline_cell')


def test_later_crews_are_inventory_only():
    post={'resources':[{'id':'c','kind':'crew','available_at':10,'duty_end_at':60,'work_remaining':20},
                       {'id':'d','kind':'crew','available_at':60,'duty_end_at':60,'work_remaining':20},
                       {'id':'e','kind':'engine','available_at':10,'duty_end_at':60,'work_remaining':20}]}
    result=mixed.later_crews(post)
    assert len(result)==1 and result[0]['available_at']==10
    assert result[0]['position_at_availability']=='unknown' and result[0]['route_to_staging']=='unknown'


def test_private_cli_guards(tmp_path):
    root=Path(__file__).resolve().parents[1];script=root/'eval/expanded_crew_mixed_corridor_v1.py'
    for args in (['--output-dir',str(tmp_path/'private')],
                 ['--opt-in-synthetic-mixed-corridors','--output-dir',str(root/'assets'/'private')]):
        p=subprocess.run([sys.executable,str(script),*args],cwd=root,capture_output=True,
                         env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
        assert p.returncode != 0
    assert not (tmp_path/'private').exists()
