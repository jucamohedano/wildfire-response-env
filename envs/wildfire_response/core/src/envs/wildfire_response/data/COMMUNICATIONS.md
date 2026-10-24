# Communication evidence and realism gates

Version: `wildfire-communication-evidence-v1`, reviewed 24 October 2026.
This is a bounded **development** evidence pack: four official bulletins,
eight attributed summaries, three incidents, **zero authentic transcripts**.
It does not claim complete public-data coverage. Madrid communication evidence
is still a gap. The package contains paraphrased facts and citations, not copied
articles, emergency call recordings or personal contact details.

## What the evidence establishes

The [Castillejos bulletin](https://www.juntadeandalucia.es/presidencia/portavoz/emergencias112/216605/Emergencias/112/Andalucia/Huelva)
supports a large aggregate call load and multi-agency notification, not a known
per-call queue or service-time distribution. The two Los Gallardos sources
separate citizen claims from an official account of individualized warnings.
The [Pinos del Valle bulletin](https://www.juntadeandalucia.es/presidencia/portavoz/emergencias112/217749/Emergencias/112/Andalucia/Granada)
describes a shelter alert, but does not provide the original alert payload or
individual receipt data. See the JSON source IDs for the remaining URLs.

Full original HTTP response bodies were downloaded with verified TLS into ignored
`.runtime/communications-evidence-2026-10-24/`. Each source records its hash,
byte count, retrieval date and visible publication date. These hashes certify
our saved bytes, not the page's contents at incident time. No historical archive
or reuse licence has been verified. Raw pages are not shipped or committed;
preserve the local archive separately before cleanup. Re-fetches may have different
hashes because these pages contain mutable presentation material.

All source records remain **evaluator-only**. A publication date, aggregate call
window or retrospective event time is not an individual caller observation or
receipt time. Exact times remain unknown where the source does not supply them.
Reported possible causes are claims, not hidden simulator truth.

## Working bridge, without silent benchmark changes

```python
from wildfire_core.communication_data import reconstruct_report

item = reconstruct_report(
    "castillejos-call-load", report_id="exercise-1",
    observed_at=1, received_at=4, location=[10, 10],
    mapping_note="Hypothetical exercise cell, not A-495 geography",
)
public_report = item["report"]
private_annotation = item["evaluator_provenance"]
```

Only the synthetic `report` enters a new development scenario's `reports` list.
The caller must validate exercise coordinates against that scenario's terrain;
nonnegative coordinates alone do not establish valid terrain. The engine gates
visibility by receipt time. The separate evaluator annotation preserves the
source link, incident grouping, assumed timing and geography. Its short Spanish
exercise template is not a quote, an authentic caller or ASD-STE100 certification.

This bridge does not expand aggregate totals into purported individual calls,
assign correctness from source role, or import later casualties/containment into
early observations. It cannot turn mobilization or shelter instructions into
citizen fire reports. Counts, timings, location errors, identities, duplicates
and language variants require explicit **synthetic** assumptions. All variants
tied to an incident must remain in one future dataset split.

The locked 360-case generator, test configurations, existing rewards, interfaces
and recorded baseline results are unchanged. This pack is not enabled by default
in those tasks. Calibrated communication tasks require a new version and split
review, not overwriting the old lock.

## What still needs implementation or evidence

- **Caller intake:** location clarification, uncertain claims, duplicates and
  queue/load models. Aggregate counts do not calibrate individual delay.
- **Bases and units:** requests, offers, readback/correction and execution updates
  exist synthetically. Actual availability, radio recordings, dispatch times and
  capacity constraints remain unverified. Derive new updates from simulator state,
  not fixed historical outcomes after the agent changes the response.
- **Population protection:** current actions support inform/evacuate only. They
  lack shelter dynamics, household targeting, safe-route instructions and in-person
  warning teams. Do not relabel shelter as evacuation. Sending an alert does not
  establish household receipt, understanding, compliance or safety.
- **Transport:** existing delayed delivery, failure acknowledgements and retries
  are synthetic. Coverage, service rates and correlated failures remain unmeasured.
  A channel label alone does not establish WhatsApp/Telegram/voice fidelity.
- **Weather and satellite:** obtain incident-linked products with observation and
  processing/availability times. No such message payloads are admitted here.
- **Operational ground truth:** initial rosters, geography, road access, protection
  zones and outcomes need corroboration before historical replay. Simulator
  verifiability is not independent validation of reality.

## Next gated increment

1. Build a separately versioned development set from the three incident groups.
   Link factual constraints to sources; keep unknowns and synthetic assumptions
   separate. Do not fit on the existing held-out set.
2. Define structured envelopes with sender/recipient, location, purpose, reference
   ID, observation/receipt/issue time and status. Generate text from these fields;
   do not grade free-form persuasion.
3. Implement shelter/location-specific protection and warning-team fallback with
   state effects and legal targets. Test these before enabling incidents requiring
   them. This is simulator design, not real emergency advice.
4. Keep delivery, readback, execution and actual population effect distinct.
   Stress conflicting instructions, stale updates and failed delivery; require
   fresh confirmation after correction. Log assumptions, not invented truth.
5. Compare response constraints to evidence where measurable; report unmeasured
   latencies as assumptions. Verify replay, privacy, timing, duplicate handling
   and counterfactual consistency on CPU first.
6. Review scenarios with a domain specialist, then freeze incident-grouped splits
   and report component outcomes. Do not claim historical dispatcher
   demonstrations or safety-calibrated rewards without that additional evidence.

## Check locally

From the repository root, with no API key or GPU:

```sh
PYTHONPATH=envs/wildfire_response/core/src python -m envs.wildfire_response.data.communications
PYTHONPATH=envs/wildfire_response/core/src python -m envs.wildfire_response.data.communications \
  --verify-snapshots .
PYTHONPATH=envs/wildfire_response/core/src python -m unittest \
  envs.wildfire_response.tests.test_communication_data -v
```

Byte verification requires the original ignored snapshots and fails on missing
or changed bytes. Loading the catalog and using synthetic exercises work offline
from an installed core wheel, without those raw snapshots.
