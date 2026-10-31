# Private source-grounded fire diagnostic v1

`wildfire_expanded_incident_v1.source_converter_v1` and
`SourceGroundedDiagnosticV1` provide an explicitly opted-in, private,
source-grounded **passive fire projection** of the unqualified expanded drafts.
They do not run the full incident-day lifecycle, validate source-map use, produce
rewards, or establish operational access, calibration or historical accuracy.
The separately versioned synthetic host remains unchanged in purpose. No raw or
derived geography is included in the repository.

The converter independently checks the pinned strict resolution document,
parent input, original scenario catalog and compressed road candidate, then
uses the existing draft builder to preserve each remapped ignition and scenario
identity. The new input's `fuel` matrix contains **source classes only**;
estimated classes and their complete estimate metadata are retained separately
in the private evidence object and are null in the runtime input. Unknown fuel,
unknown or mixed surface, and missing elevation remain unsupported. The separate
terrain candidate is used as supplied, without inherited heights or flattening;
it is not approved or validated for slope physics. Mapped land/sea classifications
are used as development assumptions under explicit opt-in, not certified ground
truth. Sea is nonburnable. The road candidate is pinned as evidence but **all
access remains unknown**; the candidate is not converted to an operational
network. In particular, its 92,278 nodes and 168,948 arcs exceed the frozen
router's limits and use a different schema. It lacks verified operational access
and complete source coverage. No graph, speed, connector, access restriction or
route is invented.

The diagnostic simulates only the draft's unchanged initial ignition, its stated
warm-up duration, and its initial wind using the existing extended fire backend.
Every ignition and physical step is checked by the censor. It does not simulate
its original resources, population, communications, fire suppression, moisture,
precipitation, temperature or humidity. It stops immediately before the first
scheduled event or later weather change, rather than silently discarding one.
A result is a partial, unscored fire diagnostic, not a run of the original draft
scenario. Warm-up fire and containment clocks remain continuous internally;
this does not reproduce the full host's handover, reporting or clock-reset
lifecycle. The private checkpoint includes warm-up progress and both clocks.

## Strict-resolution run

On the separately pinned v1 strict resolution and original catalog, the run
completed all twelve **diagnostic attempts**, not twelve incident days. The
private report, converted source-only input, estimate-provenance sidecar and
remapped drafts are under `/tmp/wildfire-expanded-source-diagnostic-v1-20261030/`
with mode 0600 inside a mode 0700 directory. The report has a canonical hash and
records all input identities, model assumptions and statuses. Its hashes are
integrity checks, not signatures. The source candidate and outputs remain
private pending rights review. No source data or derived grids were committed.

Five attempts censored and seven stopped before unsupported scheduled changes.
Per-case identities, timings and evaluator evidence remain only in the private
report. The converter does not alter unknown cells or relocate ignition. These
partial diagnostics do not qualify any other time step or route. The run uses
explicit abstract containment configuration values, not
measured deployments, and no assignment or suppression work is performed.

## Mixed-cell follow-up

The v1 input contract continues to censor `mixed` as unsupported. A separate,
explicitly opted-in experimental implementation is described in
[expanded-incident-mixed-v2.md](expanded-incident-mixed-v2.md). Its fuel and
crossing factors are uncalibrated exercise assumptions, not measurements or
validated water geometry. They do not change this strict v1 diagnostic.

Run the synthetic regressions from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/test_expanded_incident_source_v1.py tests/test_expanded_incident_v1.py tests/test_extended_inputs_v1.py -q -p no:cacheprovider -k 'not full_day'
```

The full synthetic-day acceptance check remains separate and is described in
[expanded-incident-v1.md](expanded-incident-v1.md). The existing active-case and
policy matrix does not support this diagnostic host, and source runtime data,
access, weather integration, full operations and validation remain prerequisites
for any broader use. Interpreter-specific synthetic-test and GIS dependencies
are described in [expanded-incident-mixed-v2.md](expanded-incident-mixed-v2.md);
an unavailable dependency is not counted as a pass.
