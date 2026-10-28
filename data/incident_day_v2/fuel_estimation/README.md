# Incident-day fuel assumptions

`estimates.json` is an unapproved exercise input, not measured fuel or a
historical reconstruction. Its canonical document SHA-256 is carried in
`sha256`. It contains an estimate for each of the 80 unknown source cells, a
full class matrix with known cells unchanged, and the original known mask. The
source prepared/domain document pins, cropped grid and source matrix pins, and
canonical digest of the saved SIOSE probe are included. The probe hash covers
the saved connector text, not raw HTTP bytes or headers. The source ledger and
probe file hashes are checked by the generator before use.

All retained, complete response polygons are pooled and checked at each centre,
including holes and boundaries. This yields 24 woodland, 16 shrub, seven
cultivation, three riparian and three urban centres, plus 27 unresolved centres.
Code 317 is riparian vegetation. A point does not establish cover fractions
for the 500 m cell; mixed cover and its fractions remain unverified. No cached
centre demonstrates open water. No feature is not evidence of sea or land, and
terrain heights cannot prove the absence of sea. The unresolved cells must be
displayed or censored at uncertain land boundaries by a consuming application.

For vegetation, compatible source classes are assumed as follows: woodland
8–10, shrub 4–7, cultivation 1–3, riparian 1–10. For unresolved centres, 1–10
are considered with no claim of a known cover. These are exercise assumptions,
not a validated land-cover-to-fuel crosswalk. At most eight nearest compatible
known cells vote with inverse grid distance. Ties choose the lower class ID.
Segments touching a retained built polygon are blocked. If no compatible
support remains, the explicit fallback is class 8 for woodland, 1 for
cultivation, and 4 for shrub, riparian, or unresolved land. The three urban centres do
not borrow neighbouring forest: each uses class 1 with built terrain and a
nonzero 0.35 fuel multiplier as an assumed representation of possible gardens.
The multiplier and terrain are assumptions for the runtime consumer; this
document alone does not apply them. No zero-fuel sea firebreak is invented.
All confidence labels are low; vote share is not a probability of correctness.
The evidence schema does not currently identify water polygons, so it never
emits `water`; adding positively evidenced water support needs a reviewed code
mapping and corresponding barrier tests.

`holdout.json` holds out four spatial quadrants in turn and scores up to ten
known cells per quadrant with unconstrained categorical voting. Its 24/40
correct predictions (60%) and vote-share groups are an exploratory diagnostic,
not calibrated uncertainty or evidence of accuracy on the missing cells. The
quadrant holdout tests a different setup from the category-constrained missing
cell estimates; the sample is small and spatially dependent.

Reproduce using the locally cached packs (no network or raster library):

```sh
PYTHONDONTWRITEBYTECODE=1 python eval/estimate_incident_day_fuels.py \
  --runtime /home/juancm/hackspain/wildfire-response-env/.runtime \
  --output /tmp/incident-day-fuel-estimates
```

For integration, `build_estimates(prepared, domain, landcover_probe)` returns a
document, and `validate_estimates(document, source_classes, source_known,
grid)` validates and returns it. The consumer should compare its prepared and
domain hashes with `source_pins` as well as pinning the estimate document hash;
the validator receives the cropped source matrices and grid, not the original
prepared/domain documents. Neither the validator nor this artifact changes the
source-known mask or approves the source pack.
