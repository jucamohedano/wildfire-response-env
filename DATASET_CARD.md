# Wildfire benchmark v1 catalog — private candidate

An opt-in [synthetic coverage expansion](data/synthetic_expansion_v1/README.md)
adds 960 configuration groups / 1,920 tasks. It does not replace this frozen v1
catalog or add historical incidents. Its validation/test outcomes are not used
by the initial CPU smoke run.

## Historical anchor and randomization seed

The historical anchor is the **Sierra Bermeja wildfire, Jubrique / Málaga,
Spain, first reported on 8 September 2021**—not 2019 or 2020. See the
[Copernicus EMSR545 activation](https://mapping.emergency.copernicus.eu/activations/EMSR545/)
and the [Junta de Andalucía incident summary](https://www.juntadeandalucia.es/presidencia/portavoz/tierraymar/165737/fuego/Infoca/incendio/desarrollosostenible/medioambiente/SierraBermeja/Jubrique).
This anchors the source evidence and landscape, not the actions or outcomes of
the generated exercises. Other layers have their own editions and dates; the
incident year is not a claim that every terrain/fuel input was surveyed in 2021.

The expansion's **random-number design seed is `20261026`**, used with Python's
`random.Random`. Per-episode seeds are `82000000 + 2 * group_index + variant`,
where the deterministic catalog order supplies the zero-based group index and
`variant` is 0 or 1. The original benchmark v1 has its own unchanged seed scheme.
Seeds, compiler version and pinned input hashes jointly identify a dataset;
a seed alone does not. Metadata is recorded in
[`source_context.json`](data/synthetic_expansion_v1/source_context.json).

The environment is a general operations-control-centre interface, with randomized
synthetic scenarios. **Its current dataset is still grounded in one regional
landscape.** Randomization does not create new regions or independent historical
incidents. The legacy `regional/` module path is not a claim of multi-region
validation, nor does renaming it add that coverage. No trained model is released
here; this information belongs in the dataset/environment card.

The current horizon remains 120 simulated minutes. A separately versioned
six-hour pilot, followed conditionally by a twelve-hour night-to-day test, is
proposed in the [long-session strategy](docs/long-sessions.html); it is not enabled
by this documentation update.

Versioned [scenario-quality rules](data/scenario_quality_v1/README.md) now audit
every concrete case in the expansion, benchmark and topographic-stress catalogs.
Hard contradictions block new expansion exports; plausibility warnings require
review. Static consistency and exact replay do not certify real-world realism.

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
