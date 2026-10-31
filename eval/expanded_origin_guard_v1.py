"""Fail-closed origin guard for source-grounded expanded exercise routing.

The frozen HazardRouter is unchanged. This wrapper guards its public options
entry point, including emergency egress. It does not authorize terrain, access,
vehicle suitability, or legal compliance. Callers must use this wrapper for
all route requests; it does not retrofit existing hosts.
"""
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'envs/wildfire_response/core/src'))
from shapely.geometry import LineString, Point
from shapely.strtree import STRtree
from eval.expanded_road_runtime_v1 import indexes
from envs.wildfire_response.incident_day.routing import HazardRouter, ORIGIN_TOLERANCE_M
from envs.wildfire_response.regional.ops.generator import digest
from wildfire_core.mobility_routing import RoadNetwork, point

VERSION = 'expanded-origin-guard-v1'


class GuardedOriginRouter:
    """Own a pinned road router and screen each origin against source topology.

    Source and graph hashes must be supplied from independently trusted pins.
    Python inputs and this wrapper are trusted process-local objects; this is
    not a sandbox against callers accessing or mutating private attributes.
    """
    def __init__(self, graph, expected_graph_sha256, source, expected_source_sha256,
                 inputs, heights, *, walk_mask=None, allowed_edge_ids=None):
        if (not isinstance(expected_source_sha256, str) or len(expected_source_sha256)!=64
                or digest(source) != expected_source_sha256):
            raise ValueError('source pin mismatch')
        nodes, ways, arcs, _ = indexes(source)
        network=RoadNetwork(graph,expected_graph_sha256)
        # Validate each runtime edge's exact directed source lineage; never
        # infer node identity from coordinate equality.
        seen=set()
        for identifier, position in network.nodes.items():
            if not identifier.isdigit() or int(identifier) not in nodes or nodes[int(identifier)] != position:
                raise ValueError('network node does not match source identity')
        for edge in network.edges.values():
            try:
                way=edge['source_way']; direction=edge['direction']
                first=edge['first_segment']; last=edge['last_segment']
                if (direction not in ('f','r') or type(first) is not int or type(last) is not int
                        or way not in ways or (last-first)*(1 if direction=='f' else -1)<0
                        or not isinstance(edge.get('allowed_modes'),list)):
                    raise ValueError
                step=1 if direction=='f' else -1
                seq=range(first,last+step,step)
                pairs=[arcs[way,i,direction] for i in seq]
                if not pairs or any(a[1]!=b[0] for a,b in zip(pairs,pairs[1:])):
                    raise ValueError
                path=[pairs[0][0]]+[b for _,b in pairs]
                if (edge['source']!=str(path[0]) or edge['target']!=str(path[-1])
                        or edge['coordinates'] != [nodes[n] for n in path]):
                    raise ValueError
                for i in seq:
                    key=(way,i,direction)
                    if key in seen: raise ValueError
                    seen.add(key)
            except (KeyError,IndexError,TypeError,ValueError) as exc:
                raise ValueError('network edge does not match directed source lineage') from exc
        # Index all candidate source segments, including disabled or omitted
        # runtime edges. An unknown road can otherwise be mistaken for an
        # allowed one when both cross at the same coordinate.
        segments={}
        for (way,index,_),(a,b) in arcs.items():
            segments[way,index]=(a,b)
        self._keys=sorted(segments)
        self._segments=[segments[key] for key in self._keys]
        self._positions={identifier: position[:] for identifier,position in nodes.items()}
        self._tree=STRtree([LineString([nodes[a],nodes[b]]) for a,b in self._segments])
        self._router=HazardRouter(network, inputs, heights, walk_mask=walk_mask,
                                  allowed_edge_ids=allowed_edge_ids)
        self.graph_sha256=expected_graph_sha256
        self.source_sha256=expected_source_sha256

    def assess_origin(self, origin_metric):
        """Return local component count, without disclosing source geometry."""
        origin=point(origin_metric)
        # Include a numerical margin so a tolerance-boundary case fails closed.
        radius=ORIGIN_TOLERANCE_M*1.01+1e-9
        hits=[int(i) for i in self._tree.query(Point(origin),predicate='dwithin',distance=radius)]
        # Distinct source segments may be connected at the origin only if they
        # share the same original node there. Intersections or overlaps alone
        # do not make a junction, even if roads connect elsewhere.
        parents=list(range(len(hits)))
        def find(i):
            while parents[i]!=i:
                parents[i]=parents[parents[i]];i=parents[i]
            return i
        by_node={}
        for i,segment_index in enumerate(hits):
            for node in self._segments[segment_index]:
                if math.dist(origin,self._positions[node]) <= radius:
                    if node in by_node:
                        a,b=find(i),find(by_node[node])
                        if a!=b: parents[a]=b
                    else:
                        by_node[node]=i
        components=len({find(i) for i in range(len(hits))})
        return {'version':VERSION,'status':'ambiguous' if components>1 else 'unambiguous',
                'source_segment_matches':len(hits),'local_source_components':components}

    def options(self, origin_metric, target_grid, *, mode='engine', burning, wind,
                revision=0, limit=3, excluded_cells=(), target_metric=None,
                emergency_egress=False):
        if mode not in ('crew','engine'):
            raise ValueError('Invalid mode')
        assessment=self.assess_origin(origin_metric)
        if assessment['status']=='ambiguous':
            return {'status':'unmapped','options':[],
                    'origin_guard':{'version':VERSION,'reason':'ambiguous_source_segments'}}
        return self._router.options(origin_metric,target_grid,mode=mode,burning=burning,wind=wind,
            revision=revision,limit=limit,excluded_cells=excluded_cells,target_metric=target_metric,
            emergency_egress=emergency_egress)
