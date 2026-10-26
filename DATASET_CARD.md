# Wildfire benchmark v1 catalog — private candidate

The compiler defines120configuration groups, not120historical wildfires. Eight
families combine source-grounded geography with synthetic incident mechanics.
There are80training,20validation and20test groups, with two seed/wording variants
per group:160/40/40primary episodes. Twelve development probes are separate.

Grouping precedes trajectory generation. Variants of a physical configuration
stay in one split. Every family appears in every split. Opaque IDs do not encode
incident truth. Profile sensitivity experiments remain attached to their source
configuration and do not create independent incident evidence.

`data/benchmark_v1/manifest.json` is the public ID/split index;
`catalog_lock.json` pins compiler output and prepared inputs. Full configuration,
future events and private audits remain server/evaluator artifacts. The frozen
public artifact is metadata, not a claim that raw geographic assets are cleared
for redistribution.

Trajectories are synthetic interactions. Source-grounded terrain/weather context
does not make generated calls historical transcripts. SFT examples must replay,
belong to training, satisfy demonstration-quality guards, and use causal actor
observations. Invalid or passively completed transitions are not expert labels.
RL failures, provider failures, censoring and demonstrations have distinct status.

A frozen retrieval bank may use training-only verified cases; held-out queries
cannot update it. Model-readiness probes and final evaluation are separate.
Validation/test policy outcomes must not be fed back into training data, search
heuristics, templates or profile tuning. New tuning requires a new experiment
version and a fresh untouched final test set.

The catalog covers one Málaga landscape and night response. It establishes
configuration hold-outs, not region, country or operational generalization.
There is no claim of empirical suppression/medical calibration or optimal action
labels. Distribution remains subject to `LICENSE_STATUS.md`.
