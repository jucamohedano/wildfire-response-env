"""Consume the reviewed population anchor candidate without granting access."""
import collections
import copy
import gzip
import hashlib
import math
from pathlib import Path

import adapter_v1 as api
from eval import expanded_guarded_routes_v1 as routes
from eval import expanded_post_anchors_v2 as post_anchors
from eval import expanded_road_exercise_v1 as road_policy
from eval import prepare_coastal_roads as coastal_policy
from eval.expanded_road_runtime_v1 import strict_json
from wildfire_core.mobility_routing import RoadNetwork
from wildfire_expanded_guarded_v1 import host as guarded_host
from wildfire_source_road_v1.catalog import SourceCaseCatalogV1

VERSION='wildfire-source-population-anchor-adapter-v1'
CANDIDATE_PIN='5ea3fcb157146a61072dba4818c7b6d224a5242d50ed5efbd5a28fc822ce39d4'
WATER_V2_PIN='a44d432066d6c3039f49032ca21b8a82496dff47c855fee1e07f8e854ab15b87'
V4_PIN='c8972227b95e90a94f85d07ff6bd9df853fb0d5a046d938b8036ae48931e1afa'
SOURCE_INPUT_PIN='7d988bb33986e8e6761a8b8f31d93fa22fac0c60567091071948cf152c6d5096'
RESOLUTION_PIN='a63ba37b281a0f3317465a6bbac1f7d1b71793e37908a0b9be5579cd9336e86a'
_TOL=.15
_EVALUATE=road_policy.evaluate_edge
_RESTRICTIONS=road_policy.restriction_sets


def _source():return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _policy_sources():
    root=Path(__file__).resolve().parents[2]
    expected=(root/'eval/expanded_road_exercise_v1.py',root/'eval/prepare_coastal_roads.py')
    actual=(Path(road_policy.__file__),Path(coastal_policy.__file__))
    if (any(a.resolve()!=e.resolve() for a,e in zip(actual,expected))
        or road_policy.evaluate_edge is not _EVALUATE or road_policy.restriction_sets is not _RESTRICTIONS):
        raise RuntimeError('exercise road policy binding or origin differs')
    return [hashlib.sha256(p.read_bytes()).hexdigest() for p in actual]


def _index(items,position):
    out=collections.defaultdict(list)
    for item in items:
        p=position(item);out[(math.floor(p[0]/_TOL),math.floor(p[1]/_TOL))].append(item)
    return out


def _near(index,position,extract):
    x,y=(math.floor(position[i]/_TOL) for i in (0,1))
    return [p for dx in (-1,0,1) for dy in (-1,0,1) for p in index.get((x+dx,y+dy),())
            if math.dist(position,extract(p))<=_TOL]


class PopulationAnchorAdapterV1:
    """Pinned source selector plus independently verified exact-node candidate.

    This returns private, explicitly adapted rosters, not a live host or route.
    Original incidents, reports, events, aircraft and original cases are untouched.
    """
    def __init__(self, selector, candidate, graph, source, old_network, old_snapshot_raw,
                 source_inputs, water_v2, v4_overlay, *, expected_candidate_sha256, opt_in=False):
        if opt_in is not True or type(selector) is not SourceCaseCatalogV1:
            raise ValueError('explicit opt-in and exact pinned source selector required')
        selector._ensure();deps=guarded_host._sources();code=_source();policy_pins=_policy_sources()
        value=copy.deepcopy(candidate)
        if (type(value) is not dict or value.get('version')!='expanded-population-anchors-v1'
            or value.get('status')!='private-unqualified-not-runnable'
            or expected_candidate_sha256!=CANDIDATE_PIN or value.get('sha256')!=CANDIDATE_PIN
            or api._hash({k:v for k,v in value.items() if k!='sha256'})!=CANDIDATE_PIN
            or value.get('original_catalog_sha256')!=selector._identity['original']
            or value.get('exercise_graph_sha256')!=selector._identity['graph']
            or value.get('source_sha256')!=selector._identity['source']
            or value.get('reviewed_pair_sha256')!=guarded_host._REVIEWED_PAIR_SHA256
            or value.get('original_graph_sha256')!=selector._original['network_sha256']
            or value.get('source_snapshot_compressed_sha256')!=post_anchors.SOURCE_SHA
            or type(value.get('cases')) is not list or len(value['cases'])!=12):
            raise ValueError('population anchor candidate or independently trusted pin differs')
        pair=routes._verify_reviewed_pair(graph,selector._identity['graph'],source,selector._identity['source'])
        if pair!=guarded_host._REVIEWED_PAIR_SHA256:raise ValueError('reviewed source pair differs')
        if hashlib.sha256(old_snapshot_raw).hexdigest()!=post_anchors.SOURCE_SHA:
            raise ValueError('original snapshot pin differs')
        old_source=strict_json(gzip.decompress(old_snapshot_raw))
        oldnodes=[n for n in old_source['elements'] if n.get('type')=='node']
        old_by_id={n['id']:n for n in oldnodes}
        if len(old_by_id)!=len(oldnodes):raise ValueError('duplicate original node ID')
        old=RoadNetwork(old_network,value['original_graph_sha256'])
        old_positions=collections.defaultdict(list)
        for identifier,position in old.nodes.items():old_positions[tuple(position)].append(identifier)
        pins=dict(surface=RESOLUTION_PIN,fuel=RESOLUTION_PIN,elevation=RESOLUTION_PIN,
                  access=routes.REVIEWED_PAIR_MANIFEST['source_compressed_sha256'])
        contract=api.load_inputs(source_inputs,SOURCE_INPUT_PIN,expected_source_pins=pins,opt_in=True)
        if (type(water_v2) is not dict or water_v2.get('version')!='extended-map-resolution-evidence-v2'
            or water_v2.get('status')!='private_evidence_not_simulator_input' or water_v2.get('sha256')!=WATER_V2_PIN
            or api._hash({k:v for k,v in water_v2.items() if k!='sha256'})!=WATER_V2_PIN
            or type(v4_overlay) is not dict or v4_overlay.get('version')!='extended-map-small-water-exercise-v4'
            or v4_overlay.get('status')!='private_exercise_hypothesis_not_v1_input' or v4_overlay.get('sha256')!=V4_PIN
            or api._hash({k:v for k,v in v4_overlay.items() if k!='sha256'})!=V4_PIN
            or v4_overlay.get('parent_v2_sha256')!=WATER_V2_PIN
            or v4_overlay.get('source_catalog_sha256')!=selector._identity['original']):
            raise ValueError('updated water evidence or exercise overlay pin differs')
        grid=dict(width=contract.width,height=contract.height,crs=contract.crs,transform=list(contract.transform))
        for item in (water_v2['grid'],v4_overlay['grid']):
            if any(item[k]!=grid[k] for k in grid):raise ValueError('water evidence grid differs')
        evidence=dict(version=routes.ACCESS_VERSION,role='source_unknown',grid=grid,
            surface=[['water' if x=='sea' else x for x in row] for row in contract.surface],
            access=[list(row) for row in contract.access])
        guard=routes.GuardedRoadRoutes(graph,selector._identity['graph'],source,selector._identity['source'],
            evidence,api._hash(evidence),[list(row) for row in contract.elevation],allowed_edge_ids=[],opt_in=True)
        graph_index=_index(graph['nodes'],lambda n:n['position'])
        source_index=_index(source['nodes'],lambda n:n['position_m'])
        current_nodes={n['id']:n for n in source['nodes']}
        ways={w['id']:w for w in source['ways']}
        restricted_ways,restricted_nodes=road_policy.restriction_sets(source)
        incident=collections.defaultdict(list)
        for edge in graph['edges']:
            incident[edge['source']].append(('out',edge));incident[edge['target']].append(('in',edge))
        mixed={(c['column'],c['row']) for c in v4_overlay['cells']}
        censored={(c['column'],c['row']) for c in v4_overlay['additional_dera_censors']}
        if mixed & censored:raise ValueError('conflicting updated surface evidence')
        self._anchor_evidence={}
        self._cases={}
        topology_counts=collections.Counter()
        reached={}
        def reachable(start):
            if start not in reached:
                seen={start};pending=[start]
                while pending:
                    node=pending.pop()
                    for edge_id in guard._network.adjacency[node]:
                        edge=guard._network.edges[edge_id]
                        if 'engine' in edge['allowed_modes'] and edge['target'] not in seen:
                            seen.add(edge['target']);pending.append(edge['target'])
                reached[start]=seen
            return reached[start]
        original_cases=selector._original['cases']
        anchored_cases=selector._anchored['cases']
        for index,(original,anchored,entry) in enumerate(zip(original_cases,anchored_cases,value['cases'])):
            if (entry.get('original_case_id')!=original['id'] or entry.get('original_case_sha256')!=original['sha256']
                or anchored['parent_scenario_sha256']!=original['sha256']):raise ValueError('candidate case lineage differs')
            groups={g['id']:g for g in entry.get('groups',[])}
            destinations={d['id']:d for d in entry.get('destinations',[])}
            if (len(groups)!=len(original['groups']) or len(destinations)!=len(original['destinations'])
                or set(groups)!={g['id'] for g in original['groups']}
                or set(destinations)!={d['id'] for d in original['destinations']}):
                raise ValueError('population or destination roster differs')
            adapted_groups=copy.deepcopy(original['groups']);adapted_dest=copy.deepcopy(original['destinations'])
            for group in adapted_groups:
                declared=groups[group['id']]
                if declared.get('original_status')!=group['status'] or declared.get('original_destination_id')!=group.get('destination_id'):
                    raise ValueError('population status or destination changed')
                for field in ('position','home'):
                    group[field]=self._validate_anchor(declared[field],group[field],old_positions,old_by_id,current_nodes,
                        graph_index,source_index,incident,guard,contract,water_v2,mixed,censored,ways,restricted_ways,restricted_nodes)
            for destination in adapted_dest:
                destination['position']=self._validate_anchor(destinations[destination['id']]['position'],destination['position'],
                    old_positions,old_by_id,current_nodes,graph_index,source_index,incident,guard,contract,water_v2,mixed,censored,ways,restricted_ways,restricted_nodes)
            # Ensure a cohort originally occupying a destination remains at the
            # same destination, without merging its distinct home location.
            by_id={d['id']:d for d in adapted_dest}
            for old_group,new_group in zip(original['groups'],adapted_groups):
                did=old_group.get('destination_id')
                if did is not None:
                    old_dest=next(d for d in original['destinations'] if d['id']==did)
                    if old_group['position']==old_dest['position'] and new_group['position']!=by_id[did]['position']:
                        raise ValueError('occupied destination identity differs')
            # Recompute directed engine-mode topology independently of the
            # candidate's recorded result. This is graph connectivity only.
            topology=[]
            for group in entry['groups']:
                for destination in entry['destinations']:
                    start=group['position']['graph_node_id'];end=destination['position']['graph_node_id']
                    status=('same_source_node' if start==end else
                            'directed_path_in_exercise_graph' if end in reachable(start) else
                            'no_directed_path_in_exercise_graph')
                    topology.append(dict(group_id=group['id'],destination_id=destination['id'],
                                         engine_graph_topology=status))
                    topology_counts[status]+=1
            if entry.get('topology')!=topology:raise ValueError('candidate topology differs')
            self._cases[anchored['id']]=dict(case_id=anchored['id'],original_case_id=original['id'],
                original_case_sha256=original['sha256'],adapted_groups=adapted_groups,adapted_destinations=adapted_dest,
                role='synthetic_same_osm_node_coordinate_correction',
                status='private-unqualified-not-runnable',route_access='unknown_no_connector_or_whitelist')
        if len(self._cases)!=12 or code!=_source() or deps!=guarded_host._sources() or policy_pins!=_policy_sources():
            raise RuntimeError('incomplete candidate or implementation changed')
        self._selector=selector;self._candidate=value;self._source=code
        self._topology_counts=dict(sorted(topology_counts.items()))
        self._identity=dict(version=VERSION,source=code,dependencies=deps,selector=copy.deepcopy(selector._identity),
            candidate=CANDIDATE_PIN,reviewed_pair=pair,old_snapshot=post_anchors.SOURCE_SHA,road_policy=policy_pins,
            source_input=SOURCE_INPUT_PIN,water_v2=WATER_V2_PIN,v4_overlay=V4_PIN,
            adapted_cases_sha256=api._hash(self._cases),evidence_sha256=api._hash(self._anchor_evidence),
            topology_sha256=api._hash(self._topology_counts))

    def _validate_anchor(self, record, original, old_positions, oldnodes, source_nodes, graph_index, source_index,
                         incident, guard, contract, water, mixed, censored, ways, restricted_ways, restricted_nodes):
        if type(record) is not dict or record.get('status')!='source_supported_exercise_anchor':
            raise ValueError('population anchor is unresolved')
        if record.get('original_position')!=original or record.get('cell_access')!='unknown':
            raise ValueError('population anchor original or access differs')
        oldids=old_positions.get(tuple(original),[])
        graphs=_near(graph_index,original,lambda n:n['position'])
        sources=_near(source_index,original,lambda n:n['position_m'])
        if (len(oldids)!=1 or not oldids[0].startswith('osm:n') or len(graphs)!=1 or len(sources)!=1
            or record.get('original_node_id')!=oldids[0] or record.get('graph_node_id')!=oldids[0][5:]
            or graphs[0]['id']!=oldids[0][5:] or sources[0]['id']!=int(oldids[0][5:])):
            raise ValueError('population original or nearby source node ambiguous')
        identifier=int(oldids[0][5:]);oldnode=oldnodes.get(identifier);newnode=source_nodes.get(identifier)
        exact=graphs[0]['position'];gap=math.dist(original,exact)
        if (oldnode is None or newnode is None or (oldnode['lat'],oldnode['lon'])!=(newnode['lat'],newnode['lon'])
            or exact!=newnode['position_m'] or record.get('source_position')!=exact
            or [round(v,1) for v in exact]!=original or gap>math.sqrt(2)*.05+1e-8
            or not math.isclose(record.get('displacement_m',-1),gap,abs_tol=1e-9)):
            raise ValueError('population OSM identity or rounding differs')
        assessment=guard._guard.assess_origin(exact)
        if (assessment['status']!='unambiguous' or assessment['source_segment_matches']<1
            or record.get('source_component_count')!=1):raise ValueError('ambiguous source crossing')
        edges=incident[str(identifier)]
        for _,edge in edges:
            modes,basis=road_policy.evaluate_edge(edge,ways,source_nodes,restricted_ways,restricted_nodes)
            if modes!=edge['allowed_modes'] or basis!=edge['exercise_access_basis']:
                raise ValueError('mapped edge policy differs from source tags or restrictions')
        incoming=[e for direction,e in edges if direction=='in' and 'engine' in e['allowed_modes']]
        outgoing=[e for direction,e in edges if direction=='out' and 'engine' in e['allowed_modes']]
        bases=collections.Counter(e['exercise_access_basis'] for e in incoming+outgoing)
        if (not incoming or not outgoing or record.get('engine_incoming_edges')!=len(incoming)
            or record.get('engine_outgoing_edges')!=len(outgoing) or record.get('engine_access_bases')!=dict(bases)):
            raise ValueError('exercise engine-mode evidence differs')
        modes={m:{direction:sum(1 for d,e in edges if d==direction and m in e['allowed_modes'])
                  for direction in ('in','out')} for m in ('crew','engine')}
        x,y=guard._guard._router._cell(exact)
        if not (0<=x<contract.width and 0<=y<contract.height):raise ValueError('anchor outside source grid')
        surface=water['cells'][y][x]['surface']
        self._anchor_evidence[str(identifier)]=dict(node_id=str(identifier),original_node_id=oldids[0],
            displacement_m=gap,source_components=1,exercise_modes=modes,grid_cell=[x,y],
            strict_surface=contract.surface[y][x],water_v2_surface=surface,
            v4_exercise_surface='mixed_assumption' if (x,y) in mixed else
                'unknown_dera_censor' if (x,y) in censored else surface,
            access=contract.access[y][x],routeability='unqualified_unknown_surface_or_access')
        return copy.deepcopy(exact)

    def _ensure(self):
        self._selector._ensure()
        if (self._source!=_source() or self._identity['dependencies']!=guarded_host._sources()
            or self._identity['road_policy']!=_policy_sources()
            or self._identity['selector']!=self._selector._identity
            or api._hash({k:v for k,v in self._candidate.items() if k!='sha256'})!=CANDIDATE_PIN
            or api._hash(self._cases)!=self._identity['adapted_cases_sha256']
            or api._hash(self._anchor_evidence)!=self._identity['evidence_sha256']
            or api._hash(self._topology_counts)!=self._identity['topology_sha256']):
            raise RuntimeError('population anchor adapter identity changed')

    def private_case(self,case_id):
        self._ensure()
        if case_id not in self._cases:raise ValueError('unknown anchored source case')
        return copy.deepcopy(self._cases[case_id])

    def private_host_input(self,case_id):
        """Bundle pinned source drafts with an explicitly separate adapted roster."""
        self._ensure()
        if case_id not in self._cases:raise ValueError('unknown anchored source case')
        return dict(version=VERSION,identity=copy.deepcopy(self._identity),
                    source_case=self._selector.private_case(case_id),
                    population_adaptation=copy.deepcopy(self._cases[case_id]),
                    role='private_unqualified_synthetic_host_input')

    def private_evidence(self):
        self._ensure();return copy.deepcopy(self._anchor_evidence)

    def private_topology_summary(self):
        """Return graph-only counts, never an operational access decision."""
        self._ensure();return copy.deepcopy(self._topology_counts)
