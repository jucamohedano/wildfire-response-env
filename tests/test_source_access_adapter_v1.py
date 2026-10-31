"""Synthetic host-contract tests; no source roads or private data exposed."""
import copy
from types import SimpleNamespace
import pytest
import adapter_v1 as api
from wildfire_core.mobility_routing import make_leg,make_journey
from envs.wildfire_response.incident_day.operations import Operations
import wildfire_source_access_adapter_v1 as access


def fixture(monkeypatch, *, same=False):
    class Catalog:
        def __init__(self):
            self._identity={'graph':None,'source':'source','input_identity':{'grid':'pinned'}}
            self.items={}
            for i in range(12):
                unit={'id':'unit','kind':'engine','state':'available','available_at':0,
                      'position':[0.,0.],'base_position':[0.,0.], 'water_litres':50,
                      'capacity_litres':50,'work_remaining':20}
                self.items['case-'+str(i)]={'original':{'id':'original-'+str(i),'sha256':'o'+str(i)},
                    'expanded_fire':{'sha256':'f'+str(i)},'anchored_ground_resources':[unit]}
        def _ensure(self):pass
        def case_ids(self):return list(self.items)
        def private_case(self,case):return copy.deepcopy(self.items[case])
    class Road:
        def __init__(self):
            self._identity={'graph_sha256':None,'source_sha256':'source','input_identity':{'grid':'pinned'}}
            self._allowed=frozenset(('f','r'))
            self._network=SimpleNamespace(nodes={'1':[0.,0.],'2':[500.,0.]},edges={})
            for eid,a,b,direction in (('f','1','2','f'),('r','2','1','r')):
                self._network.edges[eid]={'id':eid,'source':a,'target':b,'source_way':1,'first_segment':0,
                    'last_segment':0,'direction':direction,'allowed_modes':['engine','crew'],
                    'coordinates':[self._network.nodes[a][:],self._network.nodes[b][:]],'speed_kph':30.,'length_m':500.}
            self._network.adjacency={'1':['f'],'2':['r']}
            self._network.document={'nodes':[{'id':k,'position':v[:]} for k,v in self._network.nodes.items()],
                                    'edges':copy.deepcopy(list(self._network.edges.values()))}
            self._network.document['sha256']=api._hash(self._network.document)
            self._identity['graph_sha256']=self._network.document['sha256']
            self._guard=SimpleNamespace(_router=SimpleNamespace(network=self._network,allowed_edge_ids=self._allowed))
        def _ensure(self):pass
        def options(self,origin,destination,*,mode,burning,wind,excluded_cells):
            if burning or excluded_cells:return {'status':'unmapped','options':[]}
            eid='f' if origin==[0.,0.] else 'r';edge=self._network.edges[eid]
            leg=make_leg('road',edge['coordinates'],30,eid,edge['target'])
            journey=make_journey([leg],origin,destination,'')
            return {'status':'reachable','options':[{'journey':journey}]}
        def movement_safe(self,journey,minute,*,current_position,mode,burning,wind,excluded_cells):
            return not burning and not excluded_cells and current_position==journey['origin']
    monkeypatch.setattr(access,'SourceCaseCatalogV1',Catalog);monkeypatch.setattr(access,'SourceRoadOnlyV1',Road)
    catalog=Catalog();road=Road();catalog._identity['graph']=road._identity['graph_sha256'];evidence=[]
    for e in road._network.edges.values():
        evidence.append({'edge_id':e['id'],'status':'assumed_eligible','reason':'two_mapped_water_screens_and_land_corrob',
            'geometry_sha256':api._hash(e['coordinates']),'source_node':e['source'],'target_node':e['target'],
            'source_way':e['source_way'],'first_segment':e['first_segment'],'last_segment':e['last_segment'],
            'direction':e['direction'],'allowed_modes':e['allowed_modes']})
    cases=[]
    for i in range(12):
        base=[0.,0.];dest=base if same else [500.,0.]
        v={'version':access.PARENT_VERSION,'resource_id':'unit','resource_kind':'engine',
            'original_case_sha256':'o'+str(i),'fire_draft_sha256':'f'+str(i),'post_draft_sha256':'post',
            'public_handover_sha256':'handover','work_authorization':'none','handover_route':None,
            'original_base_position':base,'original_base_node_id':'1','source_node_id':'1' if same else '2',
            'initial_position':dest}
        v['sha256']=api._hash(v)
        cases.append({'original_case_id':'original-'+str(i),'original_case_sha256':'o'+str(i),
            'fire_draft_sha256':'f'+str(i),'post_draft_sha256':'post','handover_public_observation_sha256':'handover',
            'search_incomplete':True,'variants':[v]})
    candidate={'version':access.PARENT_VERSION,'status':'private_sensitivity_not_host_integrated',
               'cases':cases,'edge_evidence':evidence};candidate['sha256']=api._hash(candidate)
    return catalog,road,candidate


def build(c,r,d):return access.SourceAccessAdapterV1(c,r,d,expected_candidate_sha256=d['sha256'],opt_in=True)


def test_roundtrip_is_consumable_by_existing_operations_and_revalidated(monkeypatch):
    c,r,d=fixture(monkeypatch);adapter=build(c,r,d)
    result=adapter.candidates('case-0','unit',current_position=[0.,0.],burning=[],wind=[0,0])
    assert result['status']=='candidate' and result['search_incomplete']
    item=result['candidates'][0];assert item['work_authorization']=='none' and item['return_requires_current_revalidation']
    unit=c.private_case('case-0')['anchored_ground_resources'][0]
    ops=Operations([unit],[],[],{'mobilization_minutes':1})
    assert ops.dispatch('unit','incident',[1,0],item['outbound'],0)
    journey=copy.deepcopy(item['outbound']);journey['departed_at']=0
    assert adapter.movement_safe(journey,1,current_position=[0.,0.],mode='engine',burning=[],wind=[0,0])
    assert not adapter.movement_safe(journey,1,current_position=[0.,0.],mode='engine',burning=[[0,0]],wind=[0,0])


def test_unknown_access_hazards_and_same_node_do_not_become_routes(monkeypatch):
    c,r,d=fixture(monkeypatch);a=build(c,r,d)
    assert a.candidates('case-0','unit',current_position=[0.,0.],burning=[[0,0]],wind=[0,0])['status']=='unavailable'
    assert a.candidates('case-0','unit',current_position=[2.,0.],burning=[],wind=[0,0])['reason']=='current_position_not_original_base'
    with pytest.raises(ValueError):a.candidates('case-0','unit',current_position=[0.,0.],burning=None,wind=[0,0])
    c,r,d=fixture(monkeypatch,same=True);a=build(c,r,d)
    result=a.candidates('case-0','unit',current_position=[0.,0.],burning=[],wind=[0,0])
    assert result['status']=='unavailable' and result['candidate_reasons']==['staging_is_base_no_journey_or_work_site']


def test_unreviewed_edge_or_tampered_identity_fails_closed(monkeypatch):
    c,r,d=fixture(monkeypatch);d['edge_evidence'][0]['status']='unknown';d['sha256']=access._self_hash(d)
    with pytest.raises(ValueError,match='whitelist exceeds'):build(c,r,d)
    c,r,d=fixture(monkeypatch);d['edge_evidence'][0]['geometry_sha256']='bad';d['sha256']=access._self_hash(d)
    with pytest.raises(ValueError,match='screened edge differs'):build(c,r,d)
    c,r,d=fixture(monkeypatch);a=build(c,r,d);a._cases['case-0'][1]['unit']['base_position'][0]=10
    with pytest.raises(RuntimeError,match='identity changed'):a.candidates('case-0','unit',current_position=[0,0],burning=[],wind=[0,0])


def test_outbound_without_return_is_not_an_option(monkeypatch):
    c,r,d=fixture(monkeypatch);original=r.options
    def one_way(origin,destination,**kwargs):
        if origin==[500.,0.]:return {'status':'unmapped','options':[]}
        return original(origin,destination,**kwargs)
    r.options=one_way;a=build(c,r,d)
    result=a.candidates('case-0','unit',current_position=[0.,0.],burning=[],wind=[0,0])
    assert result['status']=='unavailable' and result['candidate_reasons']==['no_supported_return_route']


def test_no_assumption_that_later_unit_is_predeployed(monkeypatch):
    c,r,d=fixture(monkeypatch)
    # Keep the candidate evidence anchored, but the trusted original resource
    # declares later availability; the adapter must not invent its journey.
    for item in c.items.values():
        item['anchored_ground_resources'][0].update(state='unavailable',available_at=10)
    a=build(c,r,d)
    assert a.candidates('case-0','unit',current_position=[0.,0.],burning=[],wind=[0,0])['reason']=='resource_not_available_at_handover'


@pytest.mark.parametrize('mutation', ['geometry','speed','endpoint','adjacency','router_whitelist'])
@pytest.mark.parametrize('operation', ['plan','move'])
def test_live_reviewed_network_mutation_fails_before_planning_or_movement(monkeypatch,mutation,operation):
    c,r,d=fixture(monkeypatch);a=build(c,r,d)
    if mutation=='geometry':r._network.edges['f']['coordinates'][1][0]+=1
    elif mutation=='speed':r._network.edges['f']['speed_kph']=50.
    elif mutation=='endpoint':r._network.nodes['2'][0]+=1
    elif mutation=='adjacency':r._network.adjacency['1'].remove('f')
    else:r._guard._router.allowed_edge_ids=frozenset({'r'})
    with pytest.raises(RuntimeError,match='identity changed'):
        if operation=='plan':a.candidates('case-0','unit',current_position=[0,0],burning=[],wind=[0,0])
        else:a.movement_safe({},1,current_position=[0,0],mode='engine',burning=[],wind=[0,0])


def test_mutation_before_construction_cannot_redefine_reviewed_speed(monkeypatch):
    c,r,d=fixture(monkeypatch);r._network.edges['f']['speed_kph']=50.
    with pytest.raises(ValueError,match='live allowed road differs'):
        build(c,r,d)
