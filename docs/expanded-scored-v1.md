# Experimental expanded fixed-mask scoring v1: unscored

`wildfire_expanded_full_v1.scored.ExpandedScoredV1` is an experimental private
wrapper over the synthetic population v1 host. **Do not use it to grade an
episode.** Population v1 initializes every cohort as unexposed even if it was
already exposed at handover. A cohort occupying a burning cell at handover can
therefore be counted as newly exposed on the first day tick. Replay and an
independent recomputation reproduce that missing input; neither can correct it.

The v1 verification gate now fails closed. It returns no score for a completed,
uncensored day, using status `handover_exposure_unavailable`; censored,
unfinished and timed-out episodes also remain unscored. Existing v1 private
counter and audit diagnostics do not establish correct inherited exposure.
The source hash changes with this gate; previous configuration identities and
any previously reported v1 scores must not be treated as verified. The wrapper,
its Python instance, inputs, audit, checkpoint and counter remain private
trusted-driver data, not actor interfaces.

## Required v2 integration seam

A separately versioned population host and synthetic population catalog must
supply explicit, independently pinned handover exposure for **each** cohort;
missing values must be rejected rather than defaulted. Values are synthetic
exercise inputs, not historical observations, inferred mortality, or a claim
that warmup population exposure was simulated. Their validation must distinguish
booleans from integers and preserve the cohort's population count. A cohort
occupying a burning cell at handover must not be accepted as unexposed; reject
that inconsistency without silently changing the supplied field. An already
exposed cohort need not currently occupy a burning cell. The author of the
population host owns the exact schema and implementation.

The scorer must accept the exact reviewed population v2 implementation, require
its catalog digest and source identity in independently trusted configuration,
and verify that the operational state and minute-zero audit both reflect the
catalog's inherited exposure. Its counter denominator must exclude inherited
exposure; later exposure must be cumulative and additional. Inherited burned
footprint must remain excluded from the independently pinned fixed mask's
eligible denominator. A separate scorer version must replay from the v2 handover,
compare private checkpoints and public call results, and independently recompute
accounting before returning any score. Timeout, censor and incomplete days remain
unscored.

Handover exposure is evaluator-private unless explicitly supplied as delivered
actor knowledge through a separately reviewed public contract. The v2 host must
not reveal the catalog field, hidden exposure, audit, checkpoint, true position,
or dose through `situation`, reports or other actor outputs; it must keep
reported knowledge distinct from operational truth; represent unknown actor
knowledge as unknown or omitted rather than asserting a false observation.
Later public reports may reveal exposure only through their normal delivery lifecycle. Private
checkpoints must retain the inherited state and enough delivered-knowledge state
for fresh-instance continuation and replay. The scorer must not synthesize or
mutate a v1 host into v2, infer inherited exposure from an episode under test,
or use the live episode as its independent identity pin.

Before enabling v2 scoring, test a synthetic case containing both an explicitly
inherited exposed cohort and a previously unexposed cohort that becomes exposed
after handover. Check the exact initial and new population counts, 80/20 score,
fixed-mask accounting, unchanged public observations until delivery, checkpoint
continuation, fresh replay, identity/catalog mismatch and unscored censor and
timeout. The population v2 contract must exist and be reviewed before a scorer
can be implemented or that end-to-end regression run. No source-backed scenario
qualification or complete active-case/policy matrix follows from this seam.

Run `PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/test_expanded_scored_v1.py
-q -p no:cacheprovider` for the v1 fail-closed regression. It uses a synthetic
fixture and a test-only mixed-input trust anchor, including a controlled boundary
censor; it does not qualify any of the twelve source cases.
