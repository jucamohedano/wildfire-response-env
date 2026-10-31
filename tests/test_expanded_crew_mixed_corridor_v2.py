from pathlib import Path
import copy
import pytest
from shapely.geometry import LineString
from shapely.strtree import STRtree
from eval import expanded_crew_mixed_corridor_v2 as profile
from eval import expanded_crew_walk_front_v1 as walk
from test_expanded_crew_walk_front_v1 import inputs


class Cover:
    def __init__(self, values): self.values=values
    def sample(self, corridor):
        if isinstance(self.values, Exception):raise self.values
        return self.values


def test_vector_clear_raster_water_or_unknown_is_censored():
    grid,_,heights,water=inputs();cells={(1,5),(2,5),(3,5)};start=walk.center((1,5),grid)
    assert profile.plan(start,[[4,5]],[1,0],cells,heights,grid,water,Cover({10:2}))['status']=='synthetic_mixed_corridor_hypothesis'
    for cover in (Cover({10:5,80:1}),Cover({0:1}),Cover({}),Cover(ValueError('offline'))):
        result=profile.plan(start,[[4,5]],[1,0],cells,heights,grid,water,cover)
        assert result['status']=='search_incomplete_unknown'
        assert set(result['screen_reasons']) & {'worldcover_water_or_unknown','worldcover_evidence_unavailable'}


def test_zero_length_start_is_raster_checked_and_vector_conflicts_stay_unknown():
    grid,_,heights,water=inputs();cells={(1,5),(2,5),(3,5)};start=walk.center((1,5),grid)
    result=profile.plan(start,[[4,5]],[1,0],cells,heights,grid,water,Cover({80:1}))
    assert result['reason']=='start_connector_censored'
    water['osm']=STRtree([LineString([(1000,2000),(1000,2500)])])
    result=profile.plan(start,[[4,5]],[1,0],cells,heights,grid,water,Cover({10:1}))
    assert result['status']=='search_incomplete_unknown'
    assert result['screen_reasons']['osm_mapped_water_corridor_conflict']


def test_geometry_compiler_mutation_invalidates_binding(tmp_path):
    first=tmp_path/'extended_map_resolved_v1.py';second=tmp_path/'extended_access_v1.py'
    first.write_text('compiler v1');second.write_text('access v1')
    paths={'geometry':first,'coast':second};expected=profile.implementation_hashes(paths)
    profile.verify_implementation(expected,paths)
    first.write_text('compiler mutated')
    with pytest.raises(ValueError,match='implementation changed'):
        profile.verify_implementation(expected,paths)
    first.write_text('compiler v1');second.write_text('coast mutated')
    with pytest.raises(ValueError,match='implementation changed'):
        profile.verify_implementation(expected,paths)


def test_effective_geometry_identity_changes_when_water_changes():
    _,_,_,water=inputs();water['shore']=LineString([(10,10),(11,11)])
    initial=profile.geometry_identity(water)
    altered=dict(water);altered['osm']=STRtree([LineString([(50,50),(51,51)])])
    assert profile.geometry_identity(altered)!=initial


def test_raster_and_vector_are_both_required_before_eligibility():
    grid,_,_,water=inputs();a=walk.center((1,5),grid);b=walk.center((2,5),grid)
    cells={(1,5),(2,5)}
    assert profile.screen_segment(a,b,grid=grid,supported=cells,hazards=set(),water=water,cover=Cover({10:1}))==('eligible','exact_corridor_passed')
    assert profile.screen_segment(a,b,grid=grid,supported=cells,hazards={(2,5)},water=water,cover=Cover({10:1}))[0]=='excluded'
