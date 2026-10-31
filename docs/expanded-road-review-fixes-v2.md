# Expanded road review fixes

> **Superseded:** A later review found additional origin, retained-post and output-overwrite issues. See [road re-review fixes](expanded-road-review-fixes-v3.md) for the current artifacts.

The corrected conversion is `expanded-road-runtime-v2`. It retains any
otherwise-contractible source node that lies on, or within the origin-matching
tolerance of, a source segment with different endpoint identities. The test
uses the router's 0.000001 m tolerance plus a small numerical margin. It checks
source-node/segment proximity with Shapely and GEOS; it does not turn geometric
crossings into junctions. If two different retained nodes occupy the same
position, the existing `HazardRouter` refuses to choose between them. If one
retained source node lies on another disconnected segment, it uses the retained
node rather than attaching to that segment. The converter's validator repeats
this check and rejects a graph that hides the protected vertex.

The post-anchor split validator also requires every output edge to be a
contiguous subdivision of exactly one original edge and rejects an edge that
hides any originally retained graph node. Comparing the expanded arc set alone
was insufficient: it could accept a merge across a junction that changed
available turns while retaining the same source arcs.

For the pinned source, the full check found **zero** contractible nodes meeting
the crossing protection criterion. The corrected no-mode graph therefore has
the same 15,325 nodes and 27,284 edges as the earlier graph, but a new policy
version and digest. The `expanded-road-exercise-policy-v2` graph keeps the
prior exercise permissions and source-policy exclusions. The separately
versioned `expanded-synthetic-post-anchors-v3` graph and twelve draft cases
retain the original post identities and no operational whitelist. The two
post-to-post exercise topology paths remain 149.464 m each way. These are not
approved operations: access cells, vehicle clearances, legal restrictions and
other limitations described in the earlier documentation remain unresolved.

The private output files, all mode 0600 in mode-0700 directories, are:

- `/tmp/expanded-road-runtime-v2/network.json`, digest
  `90af8581fc4181def4b31fad91288e2120bd935bde6f8cf24f31a1cd83119694`.
- `/tmp/expanded-road-exercise-v2/network.json`, digest
  `eee5e2357818dd75117d9da3a2f5371363a20563e0a2e5c7c5c370d122ddee84`.
- `/tmp/expanded-post-anchors-v3/network.json`, digest
  `007b80c58d0e4f7312b07455578b1556101337f2fffa4a4ee037e4a122a849d4`;
  its companion scenario drafts and diagnostic remain in the same directory.

The earlier private artifacts are preserved for traceability. Do not use their
conversion or split-validation implementation as evidence that an arbitrary
candidate is safe. These improvements do not establish OSM completeness,
legal access, vehicle suitability, ground-cell access, historical conditions
or tactical safety. `RoadNetwork.path` still does not enforce `allowed_modes`.

## Reproduction

The historical v2 paths are preserved and must not be overwritten. Use the
write-once commands in [road re-review fixes](expanded-road-review-fixes-v3.md).
