"""Private, pinned lineage selector for the 12 source-adapted drafts."""
import copy
import hashlib
import gzip
from pathlib import Path

import adapter_v1 as api
from eval import expanded_guarded_routes_v1 as routes
from eval import expanded_post_anchors_v2 as anchors
from eval import extended_scenario_drafts_v1 as fire_drafts_module
from wildfire_expanded_incident_v1.mixed_v2 import MixedInputsV2, _VERIFIED
from wildfire_expanded_guarded_v1 import host as host_module

VERSION='wildfire-source-case-lineage-v1'
_ANCHOR=anchors.anchors
_BUILD_ANCHORED=anchors.draft_catalog
_BUILD_FIRE=fire_drafts_module.build_drafts


def _sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _sources():
    root=Path(__file__).resolve().parents[2]
    paths=dict(catalog=Path(__file__),anchors=Path(anchors.__file__),fire_drafts=Path(fire_drafts_module.__file__))
    expected=dict(catalog=root/'envs/wildfire_source_road_v1/catalog.py',anchors=root/'eval/expanded_post_anchors_v2.py',
                  fire_drafts=root/'eval/extended_scenario_drafts_v1.py')
    if any(paths[k].resolve()!=expected[k].resolve() for k in paths):
        raise RuntimeError('source case compiler loaded from unexpected path')
    for loaded,captured,name,path in ((anchors.anchors,_ANCHOR,'anchors',expected['anchors']),
        (anchors.draft_catalog,_BUILD_ANCHORED,'draft_catalog',expected['anchors']),
        (fire_drafts_module.build_drafts,_BUILD_FIRE,'build_drafts',expected['fire_drafts'])):
        if (loaded is not captured or getattr(captured,'__qualname__',None)!=name
            or not getattr(captured,'__code__',None) or Path(captured.__code__.co_filename).resolve()!=path.resolve()):
            raise RuntimeError('source case compiler binding differs')
    return {k:_sha(v) for k,v in paths.items()}


class SourceCaseCatalogV1:
    """Bind source cases without mutating locations or claiming runnable hosts.

    Selected data are private; the original population/destination coordinates
    are preserved and are not assumed to be reachable graph nodes.
    """
    def __init__(self, inputs, original_catalog, parent_inputs, anchored_catalog, fire_drafts, graph, source, *,
                 expected_original_sha256, expected_parent_sha256, expected_anchored_sha256,
                 expected_fire_drafts_sha256, expected_graph_sha256, expected_source_sha256, opt_in=False):
        if opt_in is not True or not isinstance(inputs,MixedInputsV2) or inputs._verified is not _VERIFIED:
            raise ValueError('explicit opt-in and validated mixed inputs required')
        source_pins=_sources();dependencies=host_module._sources()
        identity=dict(inputs.identity)
        if (identity.get('version')!='wildfire-small-water-mixed-exercise-input-v4'
            or identity.get('input_sha256')!=inputs.contract.document_sha256
            or identity.get('road')!=routes.REVIEWED_PAIR_MANIFEST['source_compressed_sha256']
            or identity.get('catalog')!=expected_original_sha256 or identity.get('parent')!=expected_parent_sha256
            or type(parent_inputs) is not dict or parent_inputs.get('sha256')!=expected_parent_sha256
            or api._hash({k:v for k,v in parent_inputs.items() if k!='sha256'})!=expected_parent_sha256
            or type(original_catalog) is not dict or original_catalog.get('sha256')!=expected_original_sha256
            or api._hash({k:v for k,v in original_catalog.items() if k!='sha256'})!=expected_original_sha256
            or original_catalog.get('inputs_sha256')!=expected_parent_sha256):
            raise ValueError('source case input or catalog identity differs')
        pair=routes._verify_reviewed_pair(graph,expected_graph_sha256,source,expected_source_sha256)
        if pair!=host_module._REVIEWED_PAIR_SHA256:raise ValueError('source/graph pair differs')
        anchors.check_catalog(original_catalog)
        routes.check_anchored_draft(anchored_catalog,expected_anchored_sha256,expected_graph_sha256,graph)
        if (len(anchored_catalog['cases'])!=12 or anchored_catalog['parent_catalog_sha256']!=expected_original_sha256
                or type(fire_drafts) is not dict or fire_drafts.get('sha256')!=expected_fire_drafts_sha256
                or api._hash({k:v for k,v in fire_drafts.items() if k!='sha256'})!=expected_fire_drafts_sha256):
            raise ValueError('source draft identity differs')
        grid=dict(width=inputs.contract.width,height=inputs.contract.height,crs=inputs.contract.crs,
                  transform=list(inputs.contract.transform))
        rebuilt_fire=fire_drafts_module.build_drafts(original_catalog,parent_inputs['grid'],grid)
        if api._encoded(rebuilt_fire)!=api._encoded(fire_drafts):
            raise ValueError('expanded fire drafts differ from original catalog')
        # Recheck the same source-node identities and exact rounding against
        # the independently pinned original coastal source snapshot.
        old_path=anchors.OLD_SOURCE_PATH
        if old_path.is_symlink():raise ValueError('original source snapshot symlink unsupported')
        old_raw=old_path.read_bytes()
        if hashlib.sha256(old_raw).hexdigest()!=anchors.SOURCE_SHA:
            raise ValueError('original source snapshot pin differs')
        old_snapshot=anchors.strict_json(gzip.decompress(old_raw))
        mapping=anchors.anchors(original_catalog,source,old_snapshot)
        if api._encoded(mapping)!=api._encoded(anchored_catalog['cases'][0]['post_anchor_provenance']['posts']):
            raise ValueError('post identities or rounding provenance differ')
        rebuilt_anchored=anchors.draft_catalog(original_catalog,mapping,expected_graph_sha256)
        if api._encoded(rebuilt_anchored)!=api._encoded(anchored_catalog):
            raise ValueError('anchored drafts differ from reconstructed post adaptation')
        if _sources()!=source_pins or host_module._sources()!=dependencies:
            raise RuntimeError('source case compiler changed during validation')
        self._original=copy.deepcopy(original_catalog)
        self._anchored=copy.deepcopy(anchored_catalog)
        self._fire=copy.deepcopy(fire_drafts)
        self._identity=dict(version=VERSION,implementation=source_pins,dependencies=dependencies,
            input_identity=identity,original=expected_original_sha256,parent=expected_parent_sha256,
            anchored=expected_anchored_sha256,fire=expected_fire_drafts_sha256,
            graph=expected_graph_sha256,source=expected_source_sha256,reviewed_pair_sha256=pair,
            original_source_snapshot_sha256=anchors.SOURCE_SHA)

    def _ensure(self):
        if (_sources()!=self._identity['implementation'] or host_module._sources()!=self._identity['dependencies']
            or self._identity['reviewed_pair_sha256']!=host_module._REVIEWED_PAIR_SHA256
            or self._identity['original_source_snapshot_sha256']!=anchors.SOURCE_SHA
            or anchors.OLD_SOURCE_PATH.is_symlink() or _sha(anchors.OLD_SOURCE_PATH)!=anchors.SOURCE_SHA
            or api._hash({k:v for k,v in self._original.items() if k!='sha256'})!=self._identity['original']
            or api._hash({k:v for k,v in self._anchored.items() if k!='sha256'})!=self._identity['anchored']
            or api._hash({k:v for k,v in self._fire.items() if k!='sha256'})!=self._identity['fire']):
            raise RuntimeError('source case identity changed')

    def case_ids(self):
        self._ensure();return [c['id'] for c in self._anchored['cases']]

    def private_case(self,case_id):
        """Return matched private drafts without moving population or incidents."""
        self._ensure()
        matches=[i for i,c in enumerate(self._anchored['cases']) if c['id']==case_id]
        if len(matches)!=1:raise ValueError('unknown source case')
        i=matches[0]
        original=copy.deepcopy(self._original['cases'][i])
        anchored=copy.deepcopy(self._anchored['cases'][i])
        fire=copy.deepcopy(self._fire['cases'][i])
        if anchored['parent_scenario_sha256']!=original['sha256'] or fire['parent_scenario_sha256']!=original['sha256']:
            raise ValueError('case lineage differs')
        # Coordinate-only coincidences do not establish population/destination
        # node identity. Return only the verified ground-post resource mapping.
        return dict(version=VERSION,case_id=case_id,original=original,
                    anchored_ground_resources=[r for r in anchored['resources'] if r['kind'] in ('crew','engine')],
                    post_anchor_provenance=anchored['post_anchor_provenance'],expanded_fire=fire,
                    role='private_unqualified_synthetic_exercise',
                    location_policy='original population and destinations unchanged; no connector assumed')
