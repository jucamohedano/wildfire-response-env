# Inland low-water sensitivity profile

`eval/domain_inland_low_water_profile_v1.py` builds a separately versioned,
private sensitivity profile from the domain-wide v3 evidence and its pinned
sources. The same rule is applied across the grid, independently of scenario
outcomes. It considers only cells covered by both mapped land faces, outside
the coastal buffers, with limited water samples and no mapped water-area
conflict. It keeps source-supported fuel classes as separate alternatives and
records multiple hypothetical water-crossing factors. Continued censoring is
also an explicit alternative.

These alternatives are low-confidence synthetic assumptions, not historical
fuel observations, demonstrated water crossings or a calibrated range. Cells
without adequate land, terrain or neighbor support remain unknown. The profile
is evidence for a separately versioned evaluator; it is not itself a runnable
input or a real-world safety assessment. Preserve its private JSON and source
pins outside the repository.

The separate `eval/domain_low_water_weather_v2.py` converter accepts explicit
per-cell fuel choices and a crossing assumption. It preserves retained source
fuel and leaves omitted or unsupported cells censored. The v1 converter and
runner are retained for the earlier private fire-and-weather smoke; its input
identity differs from v2. Neither converter produces an operations score.

Use the compiler's `--help` for source paths and validation arguments. Run the
focused checks with:

```sh
PYTHONPATH=envs/wildfire_response/core/src:envs/wildfire_extended_inputs_v1:envs/wildfire_containment_v1/src:envs:eval:. \
  PYTHONDONTWRITEBYTECODE=1 PROJ_NETWORK=OFF python -m pytest \
  tests/test_domain_inland_low_water_profile_v1.py -q -p no:cacheprovider
```

See [the base domain profile](domain-fuel-surface-profile-v3.md) for the
underlying map and provenance rules.
