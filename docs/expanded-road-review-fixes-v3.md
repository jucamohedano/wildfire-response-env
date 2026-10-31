# Expanded road re-review fixes

The current conversion, `expanded-road-runtime-v3`, rejects a graph if any
retained node lies within the origin-matching tolerance of a source segment
whose endpoints have different source IDs. This includes source arcs currently
unavailable to exercise modes, so a later mode policy cannot reintroduce the
ambiguity. A source vertex at such a crossing is first protected from
compaction, then the resulting ambiguous graph is rejected. This is deliberately
conservative: the existing `HazardRouter` receives a coordinate, not the
identity of the road on which a resource is located. Retaining the node alone
would let a resource on the other road attach to that node. No connector or
junction is inferred.

The source comparison uses Shapely/GEOS and the router's 0.000001 m tolerance,
with a small numerical margin. It runs during conversion and validation. The
post-anchor splitter and validator repeat it for all output nodes, including
new post nodes. The pinned full-map candidate has **zero** retained nodes near
an unrelated source segment under this check. This is a source-geometry audit,
not proof that all physical road crossings are represented.

The split validator continues to require each output edge to be a contiguous
subdivision of exactly one original edge. It now also rejects any output edge
that hides **any** retained output node, including a newly added post node.
That prevents a merge within one original edge from preserving the arc set
while making the new post unavailable as an endpoint.

Versioned output publication is write-once. A completed temporary file is
linked into place without replacing any existing entry, including symlinks and
hard links. Post drafts are published only into a newly created directory;
existing directories are refused. A failed or interrupted run may leave a
partial new directory; choose a fresh directory for a retry. Historical v1 and
v2 artifacts are not overwritten.

The new private artifacts, mode 0600 under mode-0700 directories, are:

- `/tmp/expanded-road-runtime-v3/network.json`, digest
  `80b629ccb398e98ff5c65e866a7f6e397cce68bd53523f5357a21caaa7c499d9`.
- `/tmp/expanded-road-exercise-v3/network.json`, digest
  `e092084313e3e056cf695310157ca43355fa65a7a73fdea21771636c2a557695`.
- `/tmp/expanded-post-anchors-v4/network.json`, digest
  `97998cd2dbda86142d6f66d189c05e6b67d814f244c7fd0f37d729a15d98c525`,
  with separate draft scenarios and diagnostic in the same directory.

The full source still has 15,325 runtime nodes and 27,284 edges. The post graph
has 15,326 nodes and 27,286 edges. The 149.464 m paths between original post
nodes remain exercise topology diagnostics, with real access, vehicle
clearances and legal status unknown. The drafts remain unqualified and their
road whitelist is empty. `RoadNetwork.path` does not enforce modes.

## Reproduction

The existing pinned v3 source-only and exercise graphs are inputs to the
subsequent stages. With the private candidate and catalog available, use fresh
paths for repeat checks; none of these commands replaces an existing output:

```sh
PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF python eval/expanded_road_runtime_v1.py \
  --output /tmp/expanded-road-runtime-v3-rebuild/network.json \
  --catalog /tmp/wildfire-eval-v1-final2-20261030/catalog.json
PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF python eval/expanded_road_exercise_v1.py \
  --opt-in --output /tmp/expanded-road-exercise-v3/rebuild-network.json \
  --catalog /tmp/wildfire-eval-v1-final2-20261030/catalog.json
PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF python eval/expanded_post_anchors_v2.py \
  --opt-in --output-dir /tmp/expanded-post-anchors-v4-rebuild
PYTHONPATH=envs/wildfire_response/core/src:. PYTHONDONTWRITEBYTECODE=1 \
  python -m pytest tests/test_expanded_road_runtime_v1.py \
  tests/test_expanded_road_exercise_v1.py tests/test_expanded_post_anchors_v2.py \
  -q -p no:cacheprovider
```

For a first build, use `/tmp/expanded-road-runtime-v3/network.json` for the
first output and the exercise command's default output; these must not already
exist. The independent raw-acquisition audit in [extended access](extended-access-v1.md)
remains separate. Shapely 2 and GEOS with `dwithin` support are required; a
missing dependency causes conversion to fail rather than skip the check.
