# Domain-ground-population host v2: path-bound evacuation

This separately versioned host uses `wildfire_source_population_tick_v3` and
preserves the previous host, stage and checkpoint identities. The v1 host and
v2 stage retain their earlier evacuation behavior and should not be used for
this corrected path selection.

The public `route-1` option is bound privately to a digest of the complete
previewed canonical journey, not to the route guard's hazard-dependent option
ID. When an order is submitted, the stage asks the pinned segment guard for
options using actual current fire, wind and exclusions, and accepts only an
exact journey digest match. A different safe path cannot be substituted. If the
path is unsafe or no longer available, the action receives the existing delayed
negative feedback; actors are not told the hidden hazard at submission. During
movement the existing exact-path and swept-segment checks still run each minute.

The public preview continues to use only delivered wind and handover fire. A
change in private fire or wind alone does not invalidate an otherwise identical,
still-safe path. A genuine private hazard does stop the order or movement.
The new `wildfire_domain_population_scored_v2` wrapper binds this host and has
its own reward and verifier status identities. It is a synthetic single-case
exercise; it is not unrestricted qualification, and source access and other
case limitations are unchanged.

A bounded source-backed smoke constructed the v2 owner, obtained one
exercise-assumption route in the public preview, submitted an evacuation
request at minute 1, and remained active through minute 3. The current modeled
origin hazard guard returned available. The probe did not establish delivered
order acceptance, evacuation movement or arrival. Same-run identities are
review candidates, not independently approved pins.
