"""Reviewed source graph, exact road edges and conservative surface exclusions.

An admissible road edge is an exercise assumption, not full-cell access. The
object and returned geometries are trusted-driver data, not an actor facade.
"""
import copy
import hashlib
import math
from pathlib import Path

import adapter_v1 as api
from eval import expanded_guarded_routes_v1 as route_module
from eval.expanded_origin_guard_v1 import GuardedOriginRouter
from wildfire_expanded_guarded_v1 import host as host_module
from wildfire_expanded_incident_v1.mixed_v2 import MixedInputsV2, _VERIFIED

VERSION='wildfire-source-road-only-v1'
BASES=frozenset(('assumed_public_road','explicit_access_tag_public_road','explicit_access_tag_service'))


def _source():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


class SourceRoadOnlyV1:
    """Exact-node, full-edge vehicle plans; no free cell travel or walking."""
    def __init__(self, inputs, graph, source, *, expected_graph_sha256, expected_source_sha256,
                 allowed_edge_ids, expected_whitelist_sha256, opt_in=False):
        if opt_in is not True or not isinstance(inputs,MixedInputsV2) or inputs._verified is not _VERIFIED:
            raise ValueError('explicit opt-in and validated mixed inputs required')
        input_identity=dict(inputs.identity)
        if (input_identity.get('version')!='wildfire-small-water-mixed-exercise-input-v4'
                or input_identity.get('input_sha256')!=inputs.contract.document_sha256
                or input_identity.get('road')!=route_module.REVIEWED_PAIR_MANIFEST['source_compressed_sha256']):
            raise ValueError('mixed input is not bound to the reviewed source road')
        host_sources=host_module._sources()
        pair=route_module._verify_reviewed_pair(graph,expected_graph_sha256,source,expected_source_sha256)
        if pair != host_module._REVIEWED_PAIR_SHA256:
            raise ValueError('road source/graph pair is not independently reviewed')
        if (type(allowed_edge_ids) is not list or any(type(x) is not str for x in allowed_edge_ids)
                or len(set(allowed_edge_ids))!=len(allowed_edge_ids)
                or api._hash(sorted(allowed_edge_ids))!=expected_whitelist_sha256):
            raise ValueError('explicit independently pinned whitelist required')
        contract=inputs.contract
        if contract.crs!='EPSG:25830' or contract.transform[0]!=500:
            raise ValueError('expected 500-metre projected fire grid')
        edges={e['id']:e for e in graph['edges']}
        for identifier in allowed_edge_ids:
            e=edges.get(identifier)
            if (e is None or e.get('exercise_access_basis') not in BASES
                    or 'engine' not in e.get('allowed_modes',[])):
                raise ValueError('whitelist requires an explicitly permitted mapped public-road edge')
        # The raster is an exclusion mask, not an access assertion. Confirmed
        # sea, mixed water, unknown surface/fuel/elevation and outside-domain
        # remain excluded. Original access stays unchanged and is never used to
        # authorize off-road travel.
        excluded=frozenset((x,y) for y in range(contract.height) for x in range(contract.width)
            if not (contract.domain[y][x] and contract.surface[y][x]=='land' and contract.fire_support[y][x]))
        walk=[[0]*contract.width for _ in range(contract.height)]
        raster=dict(grid=dict(width=contract.width,height=contract.height,crs=contract.crs,
                             transform=list(contract.transform)),
                    study_mask=[[int((x,y) not in excluded) for x in range(contract.width)]
                                for y in range(contract.height)])
        self._guard=GuardedOriginRouter(graph,expected_graph_sha256,source,expected_source_sha256,
            raster,[list(row) for row in contract.elevation],walk_mask=walk,allowed_edge_ids=allowed_edge_ids)
        self._network=self._guard._router.network
        self._positions={}
        for identifier,position in self._network.nodes.items():
            self._positions.setdefault(tuple(position),[]).append(identifier)
        self._excluded=excluded
        self._allowed=frozenset(allowed_edge_ids)
        self._inputs=inputs
        self._identity=dict(version=VERSION,source=_source(),dependencies=host_sources,
            reviewed_pair_sha256=pair,graph_sha256=expected_graph_sha256,source_sha256=expected_source_sha256,
            input_identity=input_identity,input_sha256=contract.document_sha256,
            whitelist_sha256=expected_whitelist_sha256,
            access='synthetic-assumed-public-road-edges-only; no off-road cell access')
        self._ensure()

    def _ensure(self):
        if (self._identity['source']!=_source() or self._identity['dependencies']!=host_module._sources()
                or self._identity['reviewed_pair_sha256']!=host_module._REVIEWED_PAIR_SHA256
                or self._identity['input_identity']!=dict(self._inputs.identity)
                or self._identity['input_sha256']!=self._inputs.contract.document_sha256):
            raise RuntimeError('source-road identity changed')

    def _valid(self,journey,mode):
        try:
            if mode not in ('crew','engine') or type(journey) is not dict or not journey['legs']:
                return False
            origin,destination=journey['origin'],journey['destination']
            if (len(self._positions.get(tuple(origin),[]))!=1 or len(self._positions.get(tuple(destination),[]))!=1
                or self._guard.assess_origin(origin)['status']=='ambiguous'
                or self._guard.assess_origin(destination)['status']=='ambiguous'):
                return False
            cursor=origin
            canonical_legs=[]
            for leg in journey['legs']:
                edge=self._network.edges.get(leg.get('edge_id'))
                if (edge is None or edge['id'] not in self._allowed or leg.get('mode')!='road'
                    or mode not in edge['allowed_modes'] or 'engine' not in edge['allowed_modes']
                    or edge.get('exercise_access_basis') not in BASES
                    or leg.get('coordinates')!=edge['coordinates'] or leg['coordinates'][0]!=cursor
                    or leg.get('destination_node')!=edge['target']):
                    return False
                canonical_legs.append(host_module.mobility_module.make_leg(
                    'road',edge['coordinates'],edge['speed_kph'],edge['id'],edge['target']))
                cursor=leg['coordinates'][-1]
            if cursor!=destination:return False
            canonical=host_module.mobility_module.make_journey(canonical_legs,origin,destination,'')
            if (journey.get('duration_minutes')!=canonical['duration_minutes']
                or not math.isclose(journey.get('distance_m',-1),canonical['distance_m'],rel_tol=1e-12,abs_tol=1e-7)):
                return False
            return all(math.isclose(got.get('distance_m',-1),expected['distance_m'],rel_tol=1e-12,abs_tol=1e-7)
                and math.isclose(got.get('duration_minutes',-1),expected['duration_minutes'],rel_tol=1e-12,abs_tol=1e-9)
                for got,expected in zip(journey['legs'],canonical['legs']))
        except (TypeError,ValueError,KeyError,IndexError):
            return False

    def options(self, origin, destination, *, mode, burning, wind, excluded_cells=()):
        """Private plans only; unknown, blocked and ambiguous endpoints fail closed."""
        self._ensure()
        if mode not in ('crew','engine'): raise ValueError('invalid vehicle mode')
        router=self._guard._router
        origin=host_module.mobility_module.point(origin)
        destination=host_module.mobility_module.point(destination)
        if not isinstance(burning,(list,tuple,set,frozenset)) or not isinstance(excluded_cells,(list,tuple,set,frozenset)):
            raise ValueError('hazard and restriction collections must be reusable')
        burning=router._excluded(burning); dynamic=router._excluded(excluded_cells)
        for point in (origin,destination):
            if self._guard.assess_origin(point)['status']=='ambiguous':
                return dict(status='unmapped',options=[],reason='ambiguous_source_segments')
            if len(self._positions.get(tuple(point),[]))!=1:
                return dict(status='unmapped',options=[],reason='endpoint_not_exact_source_node')
        if not self._allowed: return dict(status='unmapped',options=[],reason='empty_whitelist')
        blocked=self._excluded | dynamic
        result=self._guard.options(origin,list(router._cell(destination)),mode='engine',burning=burning,
            wind=wind,excluded_cells=blocked,target_metric=destination,limit=32)
        hazards=router._hazards(burning,wind)|blocked
        options=[]
        for option in result['options']:
            journey=option['journey']
            if (self._valid(journey,mode) and all(router._safe_line(leg['coordinates'],hazards,'engine')
                                                   for leg in journey['legs'])):
                item=copy.deepcopy(option)
                item['journey']['resource_mode']=mode
                item['id']=api._hash(dict(version=VERSION,identity=self._identity,base=item['id'],mode=mode))[:20]
                options.append(item)
        if not options: return dict(status='unmapped',options=[],reason='no_supported_road_only_route')
        return dict(status='reachable',options=options[:3],
                    scope='assumed-public-road exercise; legal access and physical safety unverified')

    def movement_safe(self,journey,minute,*,current_position,mode,burning,wind,excluded_cells=()):
        """Recheck a previously validated full-edge journey's current swept segment."""
        self._ensure()
        if type(minute) is not int or not 1<=minute<=1440:raise ValueError('invalid movement minute')
        if not self._valid(journey,mode):return False
        try:
            if type(journey.get('departed_at')) not in (int,float) or not math.isfinite(journey['departed_at']):
                return False
            current=host_module.mobility_module.point(current_position)
            previous=host_module.mobility_module.journey_position(journey,minute-1)[0]
            if math.dist(current,previous)>=.01:return False
        except (KeyError,TypeError,ValueError,OverflowError):
            return False
        if not isinstance(burning,(list,tuple,set,frozenset)) or not isinstance(excluded_cells,(list,tuple,set,frozenset)):
            raise ValueError('hazard and restriction collections must be reusable')
        return self._guard._router.safe_journey_segment(journey,minute-1,burning,wind,mode='engine',
                    excluded_cells=self._excluded | self._guard._router._excluded(excluded_cells))
