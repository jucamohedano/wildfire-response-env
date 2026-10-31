# Fresh two-component operations tick v1

`eval/fresh_two_component_operations_v1.py` is an opt-in private operations
contract layered on the fresh two-component weather owner. Existing owner,
component model, pins and checkpoints remain unchanged. This is an integration
building block, not a calibrated fire or aircraft model and not a complete
incident host.

The caller may provide containment assignments and explicit component drops
on a day minute. A drop names an aircraft resource, a cell and either the
vegetation or built component. The synthetic assumption subtracts 0.15 from
that known burning component's heat, with a floor of zero, and raises its wet
counter to at least five. A component or resource may occur only once among
drops in a tick. The same resource cannot also hold a line. Unknown component,
inert, transfer or crossing support is rejected; no absent component is
invented. The caller is responsible for resource budgets, route safety,
component choice and delayed public reporting. No operations are allowed during
warmup.

Each tick validates and applies operations on a private restored clone of the
component model, including its RNG and containment state. The exact model class,
restore method, step and integrity check are bound before cloning; rebinding
any of them fails before the live clock or RNG changes. Invalid commands
leave the live owner and its clock unchanged. If the staged model encounters
unknown support after a tick, its state is discarded and the owner becomes
censored without publishing the staged state. A trusted checkpoint from before
the tick can be restored. Successful weather, fire and containment advance are
committed together; no-operation trajectories match the previous fresh owner.
Checkpoints bind the new contract and drop assumption, and validate reported
containment costs against the model checkpoint. They are private evaluator data.

This contract does not yet connect the shared population/ground/air host or its
scorer. That host's exact backend checks, ground handover reconstruction,
source-supported masks, resource accounting and censored checkpoint policy need
separately versioned integration before actors can use these operations. No
11-case ground-access or full-day qualification claim follows from this tick.

A bounded source-backed check restored a retained fresh-component checkpoint,
applied one synthetic component drop, and reproduced the next checkpoint from
an independently reconstructed owner. It remained active at incident minute
574. The operation identity was generated in that run; this is not an
independent approval or a shared-host or ground-access test.
