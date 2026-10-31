# Private v4 weather/fire diagnostic

`weather_fire_v1.py` is a separately versioned, explicitly opted-in fire-only
composition of the independently gated v4 mixed-surface backend and the existing
`eval.incident_weather_v1` moisture proxy. It does not patch the old weather,
containment or incident-day implementations. It does not simulate resource
operations, evacuation, population exposure, dispatch, reports or rewards.
All non-weather operational events are explicitly marked **unmodeled**, and
results cannot be interpreted as runs of the full source scenarios.

The weather proxy and its source are pinned. Temperature, relative humidity and
rain affect its dimensionless state and hence the existing spread-factor mapping;
this is an uncalibrated exercise assumption, not measured fuel moisture,
verified historical weather, an operational forecast or a calibrated model.
The v4 mixed-fuel and water-crossing factors remain separately declared
exercise assumptions as described in [expanded-incident-mixed-v2.md](expanded-incident-mixed-v2.md).

The initial ignition is checked against the full fire-support stencil before
warm-up. During warm-up, minute-zero weather remains constant: each minute
advances the proxy under those conditions and applies the resulting factor before
one censored fire step. At handover, the moisture value carries over and its
weather clock resets to zero. For day tick `t`, the host selects the **true**
scheduled conditions at `t`, advances the carried proxy by one minute under
those conditions, applies that factor and wind, and then performs one censored
fire step. The physical fire and containment clock includes warm-up; it is
separately checked against the day and warm-up clocks. Unlike the existing
incident-day `set_wind` method, this separate experiment preserves the draft's
exact supplied wind components rather than rounding them. This is a versioned
model choice and is not a parity claim with the weather incident-day adapter.
Synthetic receipt times do not change physical weather, and no actor-facing
weather reports are generated.

Checks run before and after every physical step; an unsupported fire
neighbourhood, sea ignition or rectangular boundary censors the attempt. A
post-step censor records the corresponding weather and fire clock transition;
a pre-step censor advances neither. Private checkpoints pin the input,
scenario, fire, weather and code identities and include the RNG, fire,
containment, moisture, applied wind and spread factor. Restore verifies all
clocks and the exact checkpoint. A failure poisons the instance; restore a
trusted prior checkpoint instead of retrying a partial step. Hashes detect
changes but are not authentication. Checkpoints, schedules, evaluator identities
and per-case results must remain private.

`run_weather_v4` runs attempts round-robin under an explicit wall-clock budget.
It does not start a physical step after the deadline; an already-started step,
source validation and checkpoint I/O cannot be preempted and may extend elapsed
wall time beyond that limit. Active attempts at the limit are marked
`budget_exhausted`, never successful. A private checkpoint is atomically replaced
at most every 60 physical minutes per active attempt, including warm-up, and
on graceful SIGINT or SIGTERM after the current physical step. An uncatchable
kill can recover only the last completed checkpoint. A fresh process can resume
with identical pinned sources, inputs and configuration. Checkpoints from other
runner identities are rejected. Report and checkpoint destinations must be fresh
files inside a caller-owned mode-0700 directory outside all Git worktrees; they
are created mode 0600. Standard output contains only a non-result completion
message. Counts, per-case outcomes and interruption details remain private.

Synthetic tests compare warm-up and scheduled conditions with the existing
weather helper, including temperature, humidity and rain, verify the exact draft
wind, test restoration during warm-up and across a weather change, test censor
and privacy, and confirm that a non-weather event does not become a fabricated
fire effect. These are implementation checks, not the active-case/policy matrix,
full incident-day acceptance or physical validation. The separately retained
older v4 report is not overwritten.

The earlier weather pass used an untracked runner whose exact executed source
was not retained before it was edited. Its private report is preserved, but
that pass is not reproducible from committed source and exact replay has not
been established. Its recorded hash alone does not recover the source bytes.
