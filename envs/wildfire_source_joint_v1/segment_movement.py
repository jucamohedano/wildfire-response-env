"""Per-minute validation of the pinned population segment exercise paths.

This is a trusted-driver seam, not a host, actor API, or ground access rule.
"""
import copy
import hashlib
import math
from pathlib import Path

import adapter_v1 as api
from eval import expanded_road_segment_exercise_v1 as segment
from wildfire_core import mobility_routing as mobility
from wildfire_expanded_guarded_v1 import host as guarded

VERSION = 'wildfire-source-population-segment-movement-v1'
_SEGMENT_PATH = Path(__file__).resolve().parents[2] / 'eval/expanded_road_segment_exercise_v1.py'
_ROUTE_CLASS = segment.SegmentExerciseRoutes
_OPTIONS = segment.SegmentExerciseRoutes.evacuation_options


def _hash_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PopulationSegmentMovementV1:
    """Revalidate exact original population paths against current hazards.

    Source geometry and plans are private. The independently supplied component
    digest is part of the trusted-driver boundary, not authentication.
    """
    def __init__(self, graph, source, anchors, overlay, *, expected_segment_source_sha256, opt_in=False):
        if (opt_in is not True or type(expected_segment_source_sha256) is not str
                or len(expected_segment_source_sha256) != 64
                or any(c not in '0123456789abcdef' for c in expected_segment_source_sha256)):
            raise ValueError('explicit opt-in and independent segment implementation pin required')
        if (Path(segment.__file__).resolve() != _SEGMENT_PATH.resolve()
                or segment.SegmentExerciseRoutes is not _ROUTE_CLASS
                or segment.SegmentExerciseRoutes.evacuation_options is not _OPTIONS
                or _hash_file(_SEGMENT_PATH) != expected_segment_source_sha256):
            raise ValueError('segment implementation origin or pin differs')
        self._route = _ROUTE_CLASS(copy.deepcopy(graph), copy.deepcopy(source),
                                   copy.deepcopy(anchors), copy.deepcopy(overlay), opt_in=True)
        self._core = guarded._sources()
        self._source = _hash_file(Path(__file__))
        self._route_source = expected_segment_source_sha256
        self._records = copy.deepcopy(self._route._records)
        self._records_hash = api._hash(sorted((list(k), v) for k, v in self._records.items()))
        self._network_hash = self._network_digest()
        self._identity = dict(version=VERSION, source=self._source, core=copy.deepcopy(self._core),
                              route=copy.deepcopy(self._route.identity), records=self._records_hash,
                              path_network=self._network_hash)

    def _network_digest(self):
        edges = sorted({edge for record in self._records.values() for edge in record.get('edge_ids', [])})
        nodes = sorted({node for record in self._records.values()
                        for node in (record.get('source_node'), record.get('target_node')) if node is not None})
        return api._hash(dict(edges={key:self._route._network.edges[key] for key in edges},
                              nodes={key:self._route._network.nodes[key] for key in nodes}))

    def _ensure(self):
        if (Path(segment.__file__).resolve() != _SEGMENT_PATH.resolve()
                or segment.SegmentExerciseRoutes is not _ROUTE_CLASS
                or segment.SegmentExerciseRoutes.evacuation_options is not _OPTIONS
                or _hash_file(_SEGMENT_PATH) != self._route_source
                or _hash_file(Path(__file__)) != self._source or guarded._sources() != self._core
                or self._route.identity != self._identity['route']
                or self._network_digest() != self._network_hash
                or api._hash(sorted((list(k), v) for k, v in self._route._records.items())) != self._records_hash):
            raise RuntimeError('population segment movement identity changed')

    def options(self, case_id, group_id, destination_id, origin, *, burning, wind, excluded_cells):
        self._ensure()
        return self._route.evacuation_options(case_id, group_id, destination_id, origin,
            burning=burning, wind=wind, excluded_cells=excluded_cells)

    def movement_safe(self, case_id, group_id, destination_id, journey, minute, *,
                      current_position, burning, wind, excluded_cells):
        """Validate the original full path and its actual next one-minute sweep."""
        self._ensure()
        if type(minute) is not int or not 1 <= minute <= 1440:
            raise ValueError('invalid movement minute')
        key = (case_id, group_id, destination_id)
        record = self._records.get(key)
        if record is None or record['status'] != 'assumed_exercise_path' or type(journey) is not dict:
            return False
        try:
            origin = self._route._network.nodes[record['source_node']]
            canonical = self._route.evacuation_options(case_id, group_id, destination_id, origin,
                burning=(), wind=(0, 0), excluded_cells=())['options'][0]['journey']
            for field in ('origin', 'destination', 'legs', 'duration_minutes', 'distance_m', 'resource_mode'):
                if journey.get(field) != canonical.get(field):
                    return False
            departed = journey['departed_at']
            if (type(departed) not in (int, float) or not math.isfinite(departed)
                    or departed > minute - 1 or minute - 1 >= departed + canonical['duration_minutes']
                    or journey.get('arrives_at') != departed + canonical['duration_minutes']):
                return False
            current = mobility.point(current_position)
            prior = mobility.journey_position(journey, minute - 1)[0]
            if math.dist(current, prior) >= .01:
                return False
            if not isinstance(burning, (list, tuple, set, frozenset)) or not isinstance(excluded_cells, (list, tuple, set, frozenset)):
                raise ValueError('hazards must be reusable collections')
            return self._route._guard._router.safe_journey_segment(journey, minute-1, burning, wind,
                mode='engine', excluded_cells=excluded_cells) is True
        except (KeyError, IndexError, TypeError, OverflowError):
            return False
