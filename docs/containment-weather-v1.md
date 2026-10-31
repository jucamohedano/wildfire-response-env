# Experimental containment and weather incident day

`wildfire_containment_weather_v1.incident_day.ContainmentWeatherIncidentDayEnv`
is a separately versioned, opt-in incident-day adapter. It combines the existing
containment adapter, fire and ledger with the v1 synthetic weather proxy and a
single incident event bus. It does not change the original containment package
or its v10 source digest, checkpoint or transcript identity. It must not run
inside `IncidentWeatherRuntimeV1` or `IncidentEvalBridgeV1`: their global
hooks collide with the pinned core lifecycle, and the adapter rejects them.

The package requires the incident-day core and containment packages. It currently
also imports `eval.incident_weather_v1` and `eval.incident_eval_bridge_v1` from
this repository; those modules must be importable at runtime. This dependency
is not yet packaged for distribution as a standalone wheel.

Construct it with the ordinary containment arguments, plus `weather_catalog`
and an independently trusted `expected_catalog_sha256`. The catalog uses
`wildfire-incident-eval-catalog-v1`, includes matching input and network pins,
and contains compiled scenarios with weather schedules. The existing bridge
catalog validator checks scenario identity and structure without installing its
process-wide hooks. Every schedule must start at minute zero, its initial wind
must match the scenario, and its wind changes must exactly match the scenario's
wind events. Receipts must be immediate at handover and at most 60 minutes after
observation. Optional incoming reports have the same text-only and delay rules
as the weather runtime. For a one-case catalog `reset()` can omit the case ID;
otherwise select a catalog ID. Seed overrides must equal the pinned case seed.

Reset constructs the weather-coupled warm-up before initializing incident
operations, observations and the bus. The minute-zero weather stays fixed
during warm-up; moisture carries into handover with its clock reset. The
adapter retains the containment adapter's unresolved-land rejection and the
core's large-fire handover check. Its reset implementation is a copy of the
pinned core lifecycle with explicit scenario, fire and bus injection; changes
to the core require a new reviewed version rather than changing the pin.

At each subsequent minute, the core applies the scheduled true wind before
operations, the moisture proxy advances using that minute's conditions, and
the containment fire steps once. The existing adapter still handles offers,
hold authorizations, resource work and water accounting, containment reports,
and safety checks. Temperature, humidity and rain affect an uncalibrated
moisture proxy and spread factor, not measured fuel moisture or a forecast.

The instance-local bus suppresses ordinary wind reports and emits one synthetic
weather update at each scheduled observation time. It uses the core's radio
delay unless a receipt is explicitly supplied. Out-of-order stale deliveries
remain in the report ledger but do not update the latest reported wind or wake
the policy; duplicate observations are causally deduplicated against the
newest delivered observation. Incoming report categories never enter public
payloads. Public situation and transcript calls expose only delivered reports;
the true weather schedule, moisture, future deliveries, catalog, private
scenario metadata, fire state and checkpoints are trusted-driver data. The
public `handover_notice`, if supplied, is treated as public scenario content.

`checkpoint()` and `restore()` preserve the host, containment fire, weather
state and shared bus together, including queued communications, missions,
transcript and audits. The joint checkpoint pins the catalog, core, original
containment package, weather adapter and weather/bridge sources, as well as
the host's input, network and configuration pins. Restore checks the catalog
case and all clocks. Checkpoints are private and hashes detect accidental
changes; they are not authentication for untrusted inputs. As with the v10
adapter, an exception after a step begins can leave partially advanced state:
restore a prior checkpoint rather than retrying. Instances are for one trusted,
nonconcurrent driver, not an adversarial sandbox.

The public transcript has its own weather version and contains only actor
arguments and results. Trusted `private_replay_pins()` includes the catalog and
weather source pins. A fresh instance constructed with independently trusted
inputs, catalog and configuration can `replay_transcript()` and compare public
calls. After a finished episode, `verify_episode()` performs fresh replay,
compares the joint checkpoint, and independently recomputes the incident-day
reward from its minute audit. This demonstrates consistency, not physical
calibration, an independently observed containment audit, benchmark acceptance,
or policy quality.

The focused tests use real synthetic incident inputs and catalog cases, compare
warm-up, current-minute weather changes and reports with the existing weather
runtime, check late delivery, privacy, interruption and restore, and run a full
1,440-minute day with a dispatched hold and fresh verification. The complete
active-case/policy matrix, external catalog qualification, independent
containment audit and typed transport are not established by these tests.
