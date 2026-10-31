"""Guarded, road-only routing seam; not an integrated incident host.

The approved source/graph pair is anchored independently of caller-supplied
hashes. The source-unknown role cannot certify an accessible map cell.
"""
import copy
import hashlib
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'envs/wildfire_response/core/src'))
from eval import expanded_origin_guard_v1 as origin_guard_module
from eval.expanded_origin_guard_v1 import GuardedOriginRouter
from envs.wildfire_response.incident_day import routing as routing_module
from envs.wildfire_response.regional.ops.generator import digest
from wildfire_core.mobility_routing import point

VERSION='expanded-guarded-road-routes-v2'
ACCESS_VERSION='expanded-route-cell-evidence-v1'
DRAFT_VERSION='expanded-synthetic-post-anchors-v4'

# This manifest is an independently reviewed trust anchor, not an input that a
# caller may regenerate from a graph or source. The canonical source hash pins
# the complete candidate including disabled/omitted runtime roads.
REVIEWED_PAIR_MANIFEST={
    'version':'expanded-reviewed-road-pair-v1',
    'source_kind':'complete-pinned-expanded-candidate-v2',
    'source_sha256':'e9fb0ba0488a05f74947373872cae7ac83a2ac40a8465209e97910c7c26c35d1',
    'source_compressed_sha256':'976875d58d2d134ba1f5d2aa62030f23b567f56d0bf98dd9820f88a2cb3ae343',
    'source_manifest_sha256':'d331a7fd511ba9ebbd767a85d78dccb7c7e68eaef6a4d4294ab183949e6586e9',
    'graph_sha256':'97998cd2dbda86142d6f66d189c05e6b67d814f244c7fd0f37d729a15d98c525',
    'source_counts':{'nodes':92278,'ways':8665,'arcs':168948,'restriction_relations':262,
        'boundary_evidence':100,'restriction_way_evidence':423,'restriction_node_evidence':192},
}
TRUSTED_PAIR_SHA256='bbfcf2f257bca8e793f9fb8d864fb37de9bc3609c998dd94ed744e7734a8c548'


def _verify_pair_against_anchor(graph, graph_sha256, source, source_sha256, pair, anchor):
    """Validate both artifacts against an already trusted manifest digest."""
    if digest(pair)!=anchor:
        raise ValueError('reviewed pair manifest pin mismatch')
    if (not isinstance(source,dict) or source_sha256!=pair['source_sha256']
        or digest(source)!=pair['source_sha256'] or not isinstance(graph,dict)
        or graph_sha256!=pair['graph_sha256'] or graph.get('sha256')!=pair['graph_sha256']
        or digest({k:v for k,v in graph.items() if k!='sha256'})!=pair['graph_sha256']):
        raise ValueError('graph and complete source differ from reviewed pair')
    if (source.get('version')!='extended-access-directed-road-candidate-v2'
        or source.get('status')!='source_candidate_not_operational_access'
        or source.get('crs')!='EPSG:25830' or source.get('bounds')!=[290000,4035000,332000,4064500]
        or source.get('source_manifest_sha256')!=pair['source_manifest_sha256']
        or graph.get('source_manifest_sha256')!=pair['source_manifest_sha256']
        or graph.get('source_candidate_sha256')!=pair['source_compressed_sha256']):
        raise ValueError('reviewed source provenance mismatch')
    for name,count in pair['source_counts'].items():
        if not isinstance(source.get(name),list) or len(source[name])!=count:
            raise ValueError('reviewed complete-source counts mismatch')
    return anchor


def _verify_reviewed_pair(graph, graph_sha256, source, source_sha256, _anchor=TRUSTED_PAIR_SHA256):
    # The constructor never accepts an anchor from the caller. Capturing the
    # fixed digest also prevents rebinding the public constant at call time.
    return _verify_pair_against_anchor(graph,graph_sha256,source,source_sha256,
                                       REVIEWED_PAIR_MANIFEST,_anchor)


def check_evidence(document, expected):
    """Check independently supplied cells; a road never supplies cell access."""
    if (not isinstance(document,dict) or set(document)!={'version','role','grid','surface','access'}
        or document.get('version')!=ACCESS_VERSION
        or document.get('role') not in ('source_unknown','synthetic_fixture_only')
        or not isinstance(expected,str) or len(expected)!=64 or digest(document)!=expected):
        raise ValueError('cell evidence identity or role mismatch')
    grid=document['grid']
    if (not isinstance(grid,dict) or set(grid)!={'width','height','crs','transform'}
        or type(grid['width']) is not int or type(grid['height']) is not int
        or not 0<grid['width']<=128 or not 0<grid['height']<=128
        or grid['crs']!='EPSG:25830' or not isinstance(grid['transform'],list)
        or len(grid['transform'])!=6 or any(type(x) not in (int,float) or not math.isfinite(x) for x in grid['transform'])
        or grid['transform'][1]!=0 or grid['transform'][3]!=0
        or grid['transform'][0]<=0 or grid['transform'][4]>=0):
        raise ValueError('invalid evidence grid')
    width,height=grid['width'],grid['height']
    for key,allowed in (('surface',{'land','unknown','mixed','water'}),
                        ('access',{'accessible','unknown','blocked'})):
        rows=document[key]
        if (not isinstance(rows,list) or len(rows)!=height or
            any(not isinstance(row,list) or len(row)!=width or any(v not in allowed for v in row) for row in rows)):
            raise ValueError('invalid evidence raster')
    if document['role']=='source_unknown' and any(v!='unknown' for row in document['access'] for v in row):
        raise ValueError('source access must remain unknown')
    return copy.deepcopy(document)


def check_anchored_draft(catalog, expected_catalog_sha256, graph_sha256, graph):
    """Verify draft and exact same-source-node post mapping; no scenario is run."""
    if (not isinstance(catalog,dict) or catalog.get('version')!=DRAFT_VERSION
        or catalog.get('status')!='private-unqualified-not-runnable'
        or catalog.get('exercise_network_sha256')!=graph_sha256
        or catalog.get('sha256')!=expected_catalog_sha256
        or digest({k:v for k,v in catalog.items() if k!='sha256'})!=expected_catalog_sha256
        or not isinstance(catalog.get('cases'),list) or not catalog['cases']):
        raise ValueError('untrusted anchored draft catalog')
    if (not isinstance(graph,dict) or graph.get('sha256')!=graph_sha256 or
        digest({k:v for k,v in graph.items() if k!='sha256'})!=graph_sha256):
        raise ValueError('untrusted anchored graph')
    nodes={n['id']:n['position'] for n in graph['nodes']}
    anchors={}
    for case in catalog['cases']:
        if (case.get('version')!=DRAFT_VERSION or case.get('sha256')!=digest({k:v for k,v in case.items() if k!='sha256'})
            or case.get('profile',{}).get('allowed_road_edge_ids')!=[]):
            raise ValueError('invalid draft case or preexisting whitelist')
        prov=case.get('post_anchor_provenance',{})
        if prov.get('source_graph_sha256')!=graph_sha256:
            raise ValueError('draft graph pin mismatch')
        posts=prov.get('posts',{})
        if set(posts)!={'center-0','center-1'}:
            raise ValueError('draft post mapping missing')
        declared={(p.get('center_id'),p.get('node_id')) for p in case.get('temporary_posts',[])}
        if declared != {(center,'osm:n'+str(entry['node_id'])) for center,entry in posts.items()}:
            raise ValueError('temporary post source identity mismatch')
        for center,entry in posts.items():
            ident=str(entry['node_id']); position=entry['source_position']
            if ident not in nodes or position!=nodes[ident] or case['profile']['facilities'][center]['position']!=position:
                raise ValueError('post identity or exact position mismatch')
            if center in anchors and anchors[center]!=(ident,position):
                raise ValueError('inconsistent draft posts')
            anchors[center]=(ident,copy.deepcopy(position))
        for unit in case['resources']:
            if unit['kind'] in ('crew','engine'):
                if unit['home_base'] not in anchors or unit['position']!=anchors[unit['home_base']][1] or unit['base_position']!=unit['position']:
                    raise ValueError('ground unit is not at its declared post')
    return anchors


class GuardedRoadRoutes:
    """Road-only route provider to be wired into host seams, not a host itself.

    All entry points use the origin guard. Exact graph-node endpoints and full
    directed edges are required; mid-edge recovery and off-road legs are unknown.
    """
    def __init__(self, graph, graph_sha256, source, source_sha256, cell_evidence,
                 evidence_sha256, heights, *, allowed_edge_ids, opt_in=False):
        if opt_in is not True:
            raise ValueError('explicit exercise opt-in required')
        pair_sha256=_verify_reviewed_pair(graph,graph_sha256,source,source_sha256)
        evidence=check_evidence(cell_evidence,evidence_sha256)
        grid=evidence['grid']; h,w=grid['height'],grid['width']
        if (not isinstance(allowed_edge_ids,(list,tuple,set,frozenset)) or
            any(not isinstance(x,str) for x in allowed_edge_ids)):
            raise ValueError('explicit road-edge whitelist required')
        self._allowed=frozenset(allowed_edge_ids)
        self._excluded=frozenset((x,y) for y in range(h) for x in range(w)
            if evidence['surface'][y][x]!='land' or evidence['access'][y][x]!='accessible')
        # Zero walk mask is deliberate: a road is not full-cell walking access.
        cells=[[0]*w for _ in range(h)]
        router_inputs={'grid':grid,'study_mask':[[int((x,y) not in self._excluded) for x in range(w)] for y in range(h)]}
        self._guard=GuardedOriginRouter(graph,graph_sha256,source,source_sha256,
            router_inputs,heights,walk_mask=cells,allowed_edge_ids=self._allowed)
        self._network=self._guard._router.network
        self._source_role=evidence['role']
        self._positions={}
        for ident,position in self._network.nodes.items():
            self._positions.setdefault(tuple(position),[]).append(ident)
        self.identity={'version':VERSION,
                       'component_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                       'guard_sha256':hashlib.sha256(Path(origin_guard_module.__file__).read_bytes()).hexdigest(),
                       'router_sha256':hashlib.sha256(Path(routing_module.__file__).read_bytes()).hexdigest(),
                       'reviewed_pair_sha256':pair_sha256,
                       'graph_sha256':graph_sha256,'source_sha256':source_sha256,
                       'cell_evidence_sha256':evidence_sha256,'cell_evidence_role':evidence['role'],
                       'heights_sha256':digest(heights),
                       'whitelist_sha256':digest(sorted(self._allowed))}

    def _route(self, origin, destination, *, mode, burning, wind, revision=0,
               emergency_egress=False, excluded_cells=()):
        if mode not in ('crew','engine'):
            raise ValueError('unsupported vehicle mode')
        origin=point(origin); destination=point(destination); wind=point(wind)
        if not isinstance(burning,(list,tuple,set,frozenset)):
            raise ValueError('burning cells must be a reusable collection')
        router=self._guard._router
        burning=router._excluded(burning)
        excluded=self._excluded | router._excluded(excluded_cells)
        # Guard both ends, before node checks or delegation. The destination can
        # become the origin of a later return or evacuation leg.
        for value in (origin,destination):
            if self._guard.assess_origin(value)['status']=='ambiguous':
                return {'status':'unmapped','options':[],'reason':'ambiguous_source_segments'}
        starts=self._positions.get(tuple(origin),[]); ends=self._positions.get(tuple(destination),[])
        if len(starts)!=1 or len(ends)!=1:
            return {'status':'unmapped','options':[],'reason':'endpoint_not_exact_source_node'}
        if not self._allowed:
            return {'status':'unmapped','options':[],'reason':'empty_road_whitelist'}
        oc,dc=router._cell(origin),router._cell(destination)
        if not router._inside(oc) or not router._inside(dc) or oc in excluded or dc in excluded:
            return {'status':'unmapped','options':[],'reason':'unknown_or_unavailable_cell_access'}
        # Use engine vehicle routing to prevent the frozen crew walking fallback.
        # Crew requests are additionally checked against crew permissions below.
        result=self._guard.options(origin,list(dc),mode='engine',burning=burning,wind=wind,
            revision=revision,limit=32,excluded_cells=excluded,target_metric=destination,
            emergency_egress=emergency_egress)
        valid=[]
        hazards=router._hazards(burning,wind) | excluded
        hard=burning | excluded
        for option in result['options']:
            journey=option.get('journey',{})
            if journey.get('origin')!=origin or journey.get('destination')!=destination:
                continue
            legs=journey.get('legs')
            if not isinstance(legs,list) or not legs:
                continue
            cursor=origin; safe=True
            egress=emergency_egress and isinstance(journey.get('emergency_egress'),dict)
            if egress and not router._egress_path_valid(journey,hazards,hard):
                continue
            for leg in legs:
                edge=self._network.edges.get(leg.get('edge_id'))
                if (leg.get('mode')!='road' or edge is None or edge['id'] not in self._allowed
                    or mode not in edge['allowed_modes'] or 'engine' not in edge['allowed_modes']
                    or leg.get('coordinates')!=edge['coordinates'] or leg['coordinates'][0]!=cursor
                    or leg.get('destination_node')!=edge['target']
                    or not router._safe_line(edge['coordinates'],hard if egress else hazards,'engine')):
                    safe=False; break
                cursor=leg['coordinates'][-1]
            if safe and cursor==destination:
                item=copy.deepcopy(option)
                item['journey']['resource_mode']=mode
                item['id']=digest({'version':VERSION,'route':item['id'],'resource_mode':mode,
                                   'identity':self.identity})[:20]
                valid.append(item)
        if not valid:
            return {'status':'unmapped','options':[],'reason':'no_confirmed_road_only_route'}
        return {'status':'reachable','options':valid,'scope':'synthetic exercise; legal and operational access unverified',
                'cell_evidence_role':self._source_role}

    def dispatch_options(self, origin, destination, *, mode, burning, wind, revision=0, excluded_cells=()):
        return self._route(origin,destination,mode=mode,burning=burning,wind=wind,revision=revision,
                           excluded_cells=excluded_cells)

    def return_options(self, origin, home, *, mode, burning, wind, revision=0, emergency_egress=False, excluded_cells=()):
        if type(emergency_egress) is not bool: raise ValueError('invalid emergency flag')
        return self._route(origin,home,mode=mode,burning=burning,wind=wind,revision=revision,
                           emergency_egress=emergency_egress,excluded_cells=excluded_cells)

    def evacuation_options(self, origin, destination, *, burning, wind, revision=0, excluded_cells=()):
        # Synthetic vehicle evacuation only; no walking or off-network leg.
        return self._route(origin,destination,mode='engine',burning=burning,wind=wind,revision=revision,
                           excluded_cells=excluded_cells)
