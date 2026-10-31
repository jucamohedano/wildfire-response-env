"""Trusted-driver source-road candidate adapter; no host, walking or simulation."""
import copy
import hashlib
from pathlib import Path

import adapter_v1 as api
from wildfire_source_road_v1.catalog import SourceCaseCatalogV1
from wildfire_source_road_v1.road import SourceRoadOnlyV1
from wildfire_core.mobility_routing import point

VERSION='wildfire-source-access-adapter-v1'
PARENT_VERSION='expanded-wider-road-staging-sensitivity-v1'


def _self_hash(value):
    return api._hash({k:v for k,v in value.items() if k!='sha256'})


class SourceAccessAdapterV1:
    """Turn pinned staging proposals into private, host-compatible road journeys.

    Catalog, road router, hashes and current true state belong to the trusted
    host, never the actor. No scenario state, resources or access masks change.
    The host remains responsible for time, observation privacy, dispatch,
    per-minute movement and return revalidation, and work-site authorization.
    """
    def __init__(self,catalog,road,candidate,*,expected_candidate_sha256,opt_in=False):
        if opt_in is not True or type(catalog) is not SourceCaseCatalogV1 or type(road) is not SourceRoadOnlyV1:
            raise ValueError('explicit opt-in and exact reviewed source components required')
        catalog._ensure();road._ensure()
        if (type(candidate) is not dict or type(expected_candidate_sha256) is not str
                or len(expected_candidate_sha256)!=64 or candidate.get('sha256')!=expected_candidate_sha256
                or _self_hash(candidate)!=expected_candidate_sha256 or candidate.get('version')!=PARENT_VERSION
                or candidate.get('status')!='private_sensitivity_not_host_integrated'):
            raise ValueError('independently pinned staging candidate required')
        if (road._identity['graph_sha256']!=catalog._identity['graph']
                or road._identity['source_sha256']!=catalog._identity['source']
                or road._identity['input_identity']!=catalog._identity['input_identity']):
            raise ValueError('road and source catalog identities differ')
        # The road object's identity is independently pinned, but its live
        # edge dictionaries are mutable. Recover the approved graph from its
        # original document and verify that document against the trusted pin
        # before trusting any speed, geometry, node or adjacency value.
        network=road._network;router=road._guard._router
        document=network.document
        if (router.network is not network or router.allowed_edge_ids!=road._allowed
                or type(document) is not dict or document.get('sha256')!=road._identity['graph_sha256']
                or _self_hash(document)!=road._identity['graph_sha256']):
            raise ValueError('live road network is not bound to the reviewed graph')
        document_edges={e['id']:e for e in document['edges']}
        document_nodes={n['id']:n['position'] for n in document['nodes']}
        if len(document_edges)!=len(document['edges']) or len(document_nodes)!=len(document['nodes']):
            raise ValueError('duplicate reviewed graph identity')
        approved_edges={eid:copy.deepcopy(document_edges[eid]) for eid in road._allowed if eid in document_edges}
        if set(approved_edges)!=set(road._allowed):raise ValueError('allowed edge missing from reviewed graph')
        endpoint_ids={e[k] for e in approved_edges.values() for k in ('source','target')}
        approved_nodes={node:copy.deepcopy(document_nodes[node]) for node in endpoint_ids if node in document_nodes}
        if set(approved_nodes)!=endpoint_ids:raise ValueError('allowed edge endpoint missing from reviewed graph')
        approved_adjacency={node:sorted(eid for eid,edge in approved_edges.items() if edge['source']==node)
                            for node in endpoint_ids}
        if (any(network.edges.get(eid)!=edge for eid,edge in approved_edges.items())
                or any(network.nodes.get(node)!=pos for node,pos in approved_nodes.items())
                or any(sorted(eid for eid in network.adjacency.get(node,[]) if eid in road._allowed)!=expected
                       for node,expected in approved_adjacency.items())):
            raise ValueError('live allowed road differs from reviewed graph')
        entries=candidate.get('edge_evidence');cases=candidate.get('cases')
        if type(entries) is not list or type(cases) is not list or len(cases)!=12:
            raise ValueError('invalid twelve-case evidence')
        screened={}
        for item in entries:
            if type(item) is not dict or type(item.get('edge_id')) is not str or item['edge_id'] in screened:
                raise ValueError('invalid edge evidence inventory')
            screened[item['edge_id']]=item
        permitted=set()
        for eid,item in screened.items():
            if item.get('status')!='assumed_eligible':continue
            edge=road._network.edges.get(eid)
            if (edge is None or item.get('reason')!='two_mapped_water_screens_and_land_corrob'
                    or api._hash(edge['coordinates'])!=item.get('geometry_sha256')
                    or item.get('source_node')!=edge['source'] or item.get('target_node')!=edge['target']
                    or item.get('source_way')!=edge.get('source_way')
                    or item.get('first_segment')!=edge.get('first_segment')
                    or item.get('last_segment')!=edge.get('last_segment')
                    or item.get('direction')!=edge.get('direction')
                    or item.get('allowed_modes')!=edge.get('allowed_modes')):
                raise ValueError('screened edge differs from reviewed source graph')
            permitted.add(eid)
        if not road._allowed<=permitted:
            raise ValueError('road whitelist exceeds screened edge evidence')
        self._catalog=catalog;self._road=road;self._candidate=copy.deepcopy(candidate)
        self._candidate_sha=expected_candidate_sha256;self._allowed=frozenset(road._allowed)
        self._network=network;self._approved_edges=approved_edges;self._approved_nodes=approved_nodes
        self._approved_adjacency=approved_adjacency
        self._graph_seal=api._hash(dict(edges=approved_edges,nodes=approved_nodes,adjacency=approved_adjacency))
        self._implementation=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        self._cases={}
        for case_id in catalog.case_ids():
            selected=catalog.private_case(case_id)
            matches=[c for c in cases if c.get('original_case_id')==selected['original']['id']]
            if len(matches)!=1:raise ValueError('candidate case alignment differs')
            case=matches[0]
            if (case.get('original_case_sha256')!=selected['original']['sha256']
                    or case.get('fire_draft_sha256')!=selected['expanded_fire']['sha256']
                    or type(case.get('variants')) is not list):
                raise ValueError('candidate case lineage differs')
            resources={u['id']:u for u in selected['anchored_ground_resources']}
            if len(resources)!=len(selected['anchored_ground_resources']):raise ValueError('duplicate resource')
            for variant in case['variants']:
                if (type(variant) is not dict or variant.get('sha256')!=_self_hash(variant)
                        or variant.get('version')!=PARENT_VERSION or variant.get('resource_id') not in resources
                        or variant.get('original_case_sha256')!=case['original_case_sha256']
                        or variant.get('fire_draft_sha256')!=case['fire_draft_sha256']
                        or variant.get('post_draft_sha256')!=case.get('post_draft_sha256')
                        or variant.get('public_handover_sha256')!=case.get('handover_public_observation_sha256')
                        or variant.get('work_authorization')!='none' or variant.get('handover_route') is not None):
                    raise ValueError('candidate resource lineage differs')
                unit=resources[variant['resource_id']]
                if (unit['kind']!=variant.get('resource_kind') or unit['kind'] not in ('crew','engine')
                        or unit['position']!=unit['base_position'] or unit['base_position']!=variant.get('original_base_position')
                        or variant.get('source_node_id') not in road._network.nodes
                        or road._network.nodes[variant['source_node_id']]!=variant.get('initial_position')
                        or variant.get('original_base_node_id') not in road._network.nodes
                        or road._network.nodes[variant['original_base_node_id']]!=unit['base_position']):
                    raise ValueError('candidate does not preserve exact source node and unit base')
            self._cases[case_id]=(copy.deepcopy(case),resources)
        if len(self._cases)!=12 or len({c['original_case_id'] for c in cases})!=12:
            raise ValueError('candidate does not cover the twelve distinct source cases')
        self._case_seal=api._hash(self._cases)

    def _ensure(self):
        self._catalog._ensure();self._road._ensure()
        network=self._road._network;router=self._road._guard._router
        if (network is not self._network or router.network is not network or router.allowed_edge_ids!=self._allowed
                or api._hash(dict(edges=self._approved_edges,nodes=self._approved_nodes,adjacency=self._approved_adjacency))!=self._graph_seal
                or any(network.edges.get(eid)!=edge for eid,edge in self._approved_edges.items())
                or any(network.nodes.get(node)!=pos for node,pos in self._approved_nodes.items())
                or any(sorted(eid for eid in network.adjacency.get(node,[]) if eid in self._allowed)!=expected
                       for node,expected in self._approved_adjacency.items())
                or hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=self._implementation
                or self._candidate.get('sha256')!=self._candidate_sha or _self_hash(self._candidate)!=self._candidate_sha
                or self._road._allowed!=self._allowed or api._hash(self._cases)!=self._case_seal):
            raise RuntimeError('source access adapter identity changed')

    def candidates(self,case_id,resource_id,*,current_position,burning,wind,excluded_cells=()):
        """Return private candidate journeys, or explicit unavailable reasons.

        A returned round trip is checked at the supplied snapshot only. The
        host must revalidate the actual swept segment every minute, and must
        replan/revalidate a return under then-current fire/wind/restrictions.
        """
        self._ensure()
        if case_id not in self._cases:raise ValueError('unknown source case')
        if not isinstance(burning,(list,tuple,set,frozenset)) or not isinstance(excluded_cells,(list,tuple,set,frozenset)):
            raise ValueError('current fire and exclusions required')
        point(wind);current_position=point(current_position)
        case,resources=self._cases[case_id]
        if resource_id not in resources:raise ValueError('unknown ground resource')
        unit=resources[resource_id]
        if current_position!=unit['base_position']:
            return {'status':'unavailable','reason':'current_position_not_original_base',
                    'needs':'exact current source-node position and separately supported route or recovery','candidates':[]}
        if unit.get('state')!='available' or unit.get('available_at')!=0:
            return {'status':'unavailable','reason':'resource_not_available_at_handover',
                    'needs':'current available resource plus a source-supported staging and return route','candidates':[]}
        variants=[v for v in case['variants'] if v['resource_id']==resource_id]
        if not variants:
            return {'status':'unavailable','reason':'no_retained_staging_candidate_for_resource',
                    'needs':'independently screened exact-node staging candidate for this resource','candidates':[]}
        options=[];reasons=[]
        for variant in variants:
            origin=unit['base_position'];stage=variant['initial_position']
            if origin==stage:
                reasons.append('staging_is_base_no_journey_or_work_site');continue
            forward=self._road.options(origin,stage,mode=unit['kind'],burning=burning,wind=wind,excluded_cells=excluded_cells)
            if forward.get('status')!='reachable' or not forward.get('options'):
                reasons.append('no_supported_outbound_route');continue
            back=self._road.options(stage,origin,mode=unit['kind'],burning=burning,wind=wind,excluded_cells=excluded_cells)
            if back.get('status')!='reachable' or not back.get('options'):
                reasons.append('no_supported_return_route');continue
            options.append({'candidate_sha256':variant['sha256'],
                'outbound':copy.deepcopy(forward['options'][0]['journey']),
                'return_at_snapshot':copy.deepcopy(back['options'][0]['journey']),
                'work_site':'unknown','work_authorization':'none',
                'return_requires_current_revalidation':True})
        needs=[]
        if 'no_supported_outbound_route' in reasons:
            needs.append('screened directed outbound source-road route compatible with current surface and hazards')
        if 'no_supported_return_route' in reasons:
            needs.append('screened directed return source-road route compatible with current surface and hazards')
        if 'staging_is_base_no_journey_or_work_site' in reasons:
            needs.append('separately supported work site and host support for stationary base staging')
        return {'status':'candidate' if options else 'unavailable','reason':None if options else 'no_supported_round_trip',
                'candidate_reasons':reasons,'needs':needs,'search_incomplete':bool(case.get('search_incomplete')),
                'candidates':options}

    def movement_safe(self,journey,minute,*,current_position,mode,burning,wind,excluded_cells=()):
        self._ensure()
        return self._road.movement_safe(journey,minute,current_position=current_position,mode=mode,
                                        burning=burning,wind=wind,excluded_cells=excluded_cells)
