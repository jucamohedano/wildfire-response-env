# Private mixed-surface fire experiments

`mixed_v2.py` provides a separately versioned, opt-in fire experiment. It does
not change the extended-input v1 contract, which still censors mixed cells.
`exercise_v3.py` and `exercise_v4.py` are separate source-grounded converters;
`mixed_host_v3.py` supplies private passive diagnostic hosts. These are not
calibrated fire predictions, approved source-map releases or incident-day
benchmarks. No reward, route, operational deployment, population effect or
weather evolution is computed.

The converters independently check caller-supplied document and source hashes
and reconstruct their overlay using the source validation code and pinned raw
geographic evidence. The v4 converter additionally reconstructs mapped water-area
geometry and DERA watercourse conflicts. A code-owned, versioned source gate
admits only the strict and water-v2 document identities that passed full source
reconstruction; a caller cannot authorize a rehashed replacement by supplying
its own expected hash. New map identities require a new independently reviewed
gate. An unrecognized or mismatched overlay
is rejected: v3 cannot enable v4 cells. Only cells in the exactly reconstructed
and pinned overlay are represented as mixed; all other unsupported v2 cells,
including additional v4 conflict masks, remain unknown and censor. Sea remains
nonburnable. Source fuel classes remain separate from exercise factors;
estimated fuel is not promoted. All access remains unknown, and the pinned
road candidate is not routed. Source-derived inputs and private evaluator
artifacts remain outside the repository pending rights review.

The two-cell mixed-v2 path also requires a separate private audit-trust
manifest via `trusted_audit_manifest` (`--audit-trust-manifest` for the screen).
Its fingerprint is fixed by the source gate and binds the audit identity to the
admitted strict and water-v2 sources. The corresponding audit was separately
reconstructed from the pinned geographic evidence; a caller cannot authorize a
modified audit by rehashing it and its hypotheses. The manifest remains private
and must be a mode-0600 file in a caller-owned mode-0700 directory outside Git.
The screening command prints only a generic completion message; its result
counts and case-level records remain in the private report. Adding this gate
changes the mixed backend's source identity: an older checkpoint cannot be
resumed under the changed source as if its code pin were unchanged.

A mixed input requires an explicit low, nominal or high fuel scenario. V3/v4
use the factors declared by their pinned overlays. Each factor multiplies the
existing mapped initial-fuel model for that cell; it is not a measured fuel or
water fraction. The two-cell experiment uses its declared sample share as a
crossing proxy; the v3/v4 converters require a separately supplied **crossing
proxy** strictly between zero and one. For each candidate spread, the copied, versioned
containment step multiplies ignition probability by the proxy for each mixed
source or target cell and, on a diagonal, each mixed corner cell in the stencil.
For the private diagnostic the proxy is 0.9. This is an explicit synthetic
exercise rule, not an observed probability, surveyed geometry, confidence bound
or guarantee of passage. Even the high-fuel setting retains this crossing
attenuation; mixed cells are neither labeled dry nor assigned zero fuel as an
artificial firebreak. The rule does not model the location, width, flow or
continuity of water within a cell, and it does not calibrate containment-line
pressure or real fire behavior. Real-world water may block a crossing entirely.

The original v1 containment step remains unchanged. The new copied step keeps
its eight-neighbour stencil, candidate ordering and random draws, with the
explicit probability multiplier above. It censors before and after every
physical step and after ignition. Unsupported or ambiguous cells never become
barriers: proximity stops the episode. Synthetic tests cover whole-land parity
with the original fire step, actual spread into a mixed cell, nonzero crossing,
water attenuation, coastal and water-area eligibility, unknown censoring, and
JSON checkpoint continuation. This tests implementation consistency, not
physical accuracy.

The v4 converter accepts its overlay only after independently checking its
separate pin, parent identities and a complete reconstruction from pinned
sources, including area geometry and watercourses. Extra mapped conflicts that
are not admitted by that reconstruction remain censored even if an earlier map
classified the cell as land or sea. A missing, mismatched or unvalidated v4
artifact is rejected; it is never inferred from v3. The v4 host accepts only a
validated v4 input identity. All scenario and evaluator detail, including
per-case results and future schedules, stays in the private report.
Diagnostic outputs require a pre-existing caller-owned mode-0700 directory
outside every Git worktree, and are created as fresh mode-0600 files.

The hardened nominal v4 diagnostic attempted all twelve drafts using their
pinned draft seeds. All twelve stopped before an unsupported scheduled change;
none encountered a fire-support censor before that stop. These are partial
fire-only attempts, not completed incident days, and no scores were produced.
Case-level results and runtime identities are in the private report. Full source
reconstruction reproduced the pinned inputs and overlay, although the GIS
runtime emitted a PROJ database-open warning.

Run the synthetic checks from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/test_expanded_mixed_v2.py tests/test_expanded_exercise_v3.py tests/test_expanded_incident_source_v1.py -q -p no:cacheprovider
```

These tests include v4 polygon checks in child processes using the invoking
Python interpreter. They require `pytest`, Shapely and pyproj in that same
environment; the child resets `PYTHONPATH`, so it cannot borrow packages from
another interpreter. Missing dependencies are failures, not successful or
silently skipped checks. In the local environment inspected for this work, the
base Python has pytest, Shapely and pyproj but not rasterio; the incident-day
QA virtual environment has pytest, pyproj and rasterio but not Shapely; the
Detectree GIS interpreter has Shapely, pyproj and rasterio but not pytest.
The observed versions were respectively: Python 3.12.9 / pytest 9.1.1 /
Shapely 2.1.2 / pyproj 3.8.0; Python 3.14.4 / pytest 9.2.0 / pyproj 3.8.0 /
rasterio 1.5.0; and Python 3.12.7 / Shapely 2.0.6 / pyproj 3.7.0 /
rasterio 1.4.2. These are observations of the local interpreters, not a
dependency lock or a claim that other environments behave the same way.
Consequently, the base Python can run these synthetic tests, while independent
raw-source GIS reconstruction uses the Detectree interpreter separately.
Neither result substitutes for the other. Provision all dependencies in a
single environment to run both there; do not count an unavailable check as a
pass. In particular, the v4 geometry test was confirmed to fail in the QA
virtual environment with `ModuleNotFoundError: No module named 'shapely'`;
the combined extended-input import checks pass there independently of that
missing GIS dependency.
