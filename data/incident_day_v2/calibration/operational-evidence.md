# Operational evidence ledger

Run the checker with local runtime roots, repeating the option when files are split across locations:

```sh
python data/incident_day_v2/calibration/check_operational_evidence.py \
  --runtime /path/to/station-runtime --runtime /path/to/normalized-runtime \
  --egif-archive /path/to/egif-spain-2021.zip
```

A runtime root contains `malaga-station-weather/` or `malaga-weather-normalized-v1/`, corresponding to the paths under `.runtime/` in the source metadata. Both options are optional. The checker reports each file's actual availability and verifies its recorded SHA-256. It checks the tracked normalization metadata against its provenance entry, the profile and fuel accounting, and, when both derived weather and chart files are available, all 384 timestamps and five measurement fields. A mismatch exits 1; missing evidence is a warning. Hash matching establishes byte integrity against recorded hashes, not source authenticity, observation quality or a calibration claim. Avoid sharing the raw HTML: it is only hashed, since it can contain session fields.

The earlier acquisition sidecar recorded eight ambiguous `24:00` rows. The subsequent normalization resolves all eight using the official chart, its parser and the SiAR UTC manual: 384 distinct half-hour source timestamps, no gaps or imputation. The source clock is resolved; interval start/end semantics and historical publication/receipt remain unknown. The checker reports whether the normalized rows, chart, raw provider files and manual were actually loaded and hash-verified. Estepona station wind is in m/s; the engine wind and its 2.8 threshold are model units. No validated mapping connects them, and the station does not establish mountain fire-front wind or aircraft gusts. The older `weather_access_status.json` describes an earlier, unsuccessful acquisition investigation; later station and normalization sidecars supersede that acquisition status, not the replay-readiness gate.

The optional EGIF probe hashes the locally cached archive and inspects the first 100 XML records in source order. In this bounded convenience sample, 100 ground detection-to-arrival differences were nonnegative (0–139 minutes, median 29.5); 24 air differences were nonnegative (5–160 minutes, median 33), and 76 air fields were missing or invalid. This is not a representative sample, a universal response standard or evidence of setup and duty durations. Timezone, field quality and historical actor receipt remain unverified. No record-level incident data is exported by the checker.

[MITECO's land-loading aircraft description](https://www.miteco.gob.es/en/biodiversidad/temas/incendios-forestales/extincion/act.html) gives 250 km/h unloaded cruise for a broad class and lists AT-802 and S2R-T660 examples; the synthetic profile uses 240 km/h without specifying type or load. A [2020 ministry campaign document](https://www.miteco.gob.es/content/dam/miteco/es/prensa/200618presentacioncampanaincendiosforestales_tcm30-509908.pdf) describes a 3,100 L land-loading AT-802, matching the fixture's 3,100 L capacity as type context. Neither establishes which aircraft flew this incident, loaded mission speed, refill duration or flight-window limits. These comparisons are not calibration or bounds on all aircraft.

The profile retains two six-minute aircraft refill fields for compatibility.
Operations and the corrected plausibility screen both consume
`air_refill_minutes`; `aircraft_refill_minutes` is a legacy alias. Divergence is
reported as a warning. Agreement does not validate six minutes. Fuel, spread,
suppression, roads, resources, duty, population, exposure and communications remain
independently unvalidated. No held-out outcomes are used here. Redistribution
rights for retained source evidence are a separate gate.
