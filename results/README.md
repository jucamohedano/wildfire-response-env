# Results and limitations

- [Frozen held-out baseline audit](../envs/wildfire_response/core/src/envs/wildfire_response/regional/HELDOUT.md):
  48 independently verified/replayed rollouts; not model training performance.
- [Actual Qwen3 GPU pilots](../envs/wildfire_response/core/src/envs/wildfire_response/regional/training/RESULTS.md):
  LoRA/GRPO execution verified, but no useful learned director; zero-gradient groups
  and missing offer authorizations remain unresolved learning limitations.
- [Machine-readable GPU results](../envs/wildfire_response/core/src/envs/wildfire_response/regional/training/pilot_results.json)
  and [held-out baseline scores](../envs/wildfire_response/core/src/envs/wildfire_response/regional/heldout_results.json).

Reports retain their source revisions from the original project. Local source
digests change during packaging extraction; the locked benchmark identity must not.
New migration acceptance is recorded separately. No model weights or private
evaluator logs are committed, and prior observed test outcomes are not unseen tests.
