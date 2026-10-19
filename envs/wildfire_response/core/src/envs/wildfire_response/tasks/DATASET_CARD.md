# Wildfire Response synthetic tasks v1

120 procedural configurations: 80 training, 20 validation, 20 test. All six
families occur in every split. Data are synthetic templates and scenario events,
not actual phone calls, satellite detections or official evacuation instructions.
The Brunete district locations and allocations are inherited demo conventions;
see the repository's population provenance document. No phone numbers, private
chat IDs or environment-variable credentials belong in these records.

Generate with `python -m envs.wildfire_response.eval.run --generate-only`.
Public JSONL files contain scenario discovery metadata. The private task files
contain replay seeds, scheduled events, reports and evaluator labels. The
environment can reconstruct scenarios from its versioned generator, so a policy
needs only its current public observation. Actor and evaluator data must remain
separate even when both are published for reproducibility.

Trajectory transition exports contain the observation, structured action, next
observation, scalar reward, termination/truncation flags and version identifiers.
Reports retain observation, receipt and actual agent-availability timestamps.
Rollout source hashes and scenario hashes should accompany published results.
Generated report truth and event-relatedness are offline diagnostic labels,
never a reward for trusting or dismissing a speaker based on role.

This is an environment and baseline release; no model was trained. Held-out
evaluation must use the same seed/horizon for each policy. A frozen retrieval
bank may contain only training trajectories; its hash is stored with each run.
Continual learning with test outcomes is a different experiment and is not
part of these results. The bounded counterfactual oracle is best-found search,
not a proof of optimality, and truncated searches cannot create teacher labels.

Code licensing and scenario/map redistribution terms must be resolved before
public hosting. No dataset license is asserted by this draft card.
