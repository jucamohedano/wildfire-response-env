"""Bounded single-GPU Qwen3 LoRA/GRPO pilot; never publishes or evaluates held-out tasks."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import time

from ...eval.run import dump, source_digest
from ..benchmark import verify_lock
from ..director.policy import INSTRUCTIONS
from ..scenarios import get_scenario
from .environment import RegionalTrainingEnv, outcome_reward

MODEL_ID = 'Qwen/Qwen3-1.7B'
MODEL_REVISION = '70d244cc86ccca08cf5af4e1e306ecf908b1ad5e'
MODEL_REVISIONS = {MODEL_ID: MODEL_REVISION,
    'Qwen/Qwen3-0.6B': 'c1899de289a04d12100db370d81485cdf75e47ca'}


def run(output, model_id=MODEL_ID, revision=None, steps=2, completion_tokens=1536, tool_iterations=8):
    if (type(steps) is not int or not 1 <= steps <= 8 or type(completion_tokens) is not int
            or not 256 <= completion_tokens <= 2048 or type(tool_iterations) is not int
            or not 4 <= tool_iterations <= 16):
        raise ValueError('Pilot permits 1–8 optimizer steps, 256–2048 completion tokens and 4–16 tool turns')
    revision = MODEL_REVISIONS.get(model_id) if revision is None else revision
    if model_id not in MODEL_REVISIONS or not isinstance(revision, str) or len(revision) != 40 or any(
            character not in '0123456789abcdef' for character in revision):
        raise ValueError('Use a reviewed small Qwen3 model and an immutable revision')
    output = Path(output)
    if output.exists():
        raise ValueError('Choose a fresh pilot output directory')
    benchmark = verify_lock()
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    os.environ['DO_NOT_TRACK'] = '1'
    os.environ['WANDB_DISABLED'] = 'true'
    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer, set_seed
    from trl import GRPOConfig, GRPOTrainer

    if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
        raise RuntimeError('A CUDA GPU supporting BF16 is required; CPU fallback is disabled')
    set_seed(20261019)
    output.mkdir(parents=True)
    started = time.perf_counter()
    metadata = dict(model=model_id, revision=revision, benchmark=benchmark, source_sha256=source_digest(),
        gpu=torch.cuda.get_device_name(0), cuda=torch.version.cuda, steps=steps,
        completion_tokens=completion_tokens, max_tool_calling_iterations=tool_iterations,
        python=platform.python_version(), thinking=False, inference_backend='transformers_sdpa',
        versions={name: importlib.metadata.version(name) for name in ('torch','transformers','trl','peft','huggingface_hub')},
        training_tasks=['regional-train-001', 'regional-train-003'], heldout_evaluation=False,
        reward='Unchanged full-horizon outcome cost; early model stop preserves missions and passively advances time',
        publish=False)
    dump(output/'run.json', metadata)
    try:
        tokenizer = AutoTokenizer.from_pretrained(model_id, revision=revision, trust_remote_code=False)
        model = AutoModelForCausalLM.from_pretrained(model_id, revision=revision, trust_remote_code=False,
            dtype=torch.bfloat16, attn_implementation='sdpa')
        model.to('cuda')
        rows = []
        for identifier in metadata['training_tasks']:
            task = get_scenario(identifier)
            rows.append(dict(scenario_id=identifier, seed=task['seed'], prompt=[
                dict(role='system', content=INSTRUCTIONS),
                dict(role='user', content='Manage this synthetic incident using the supplied tools and public observations. '
                     f'This pilot allows {tool_iterations} tool turns. Use submit_action for normal decisions. '
                     'Checks are optional: after a valid check, submit that action instead of checking it again. '
                     'Checking alone never executes orders. Continue until done when the token budget allows; '
                     'if you stop early, the evaluator advances the remaining time with no new orders.')]))
        dataset = Dataset.from_list(rows)
        config = GRPOConfig(output_dir=str(output/'trainer'), max_steps=steps,
            per_device_train_batch_size=1, gradient_accumulation_steps=2,
            per_device_eval_batch_size=2, num_generations=2, num_generations_eval=2,
            max_completion_length=completion_tokens, max_tool_calling_iterations=tool_iterations,
            chat_template_kwargs=dict(enable_thinking=False), temperature=.7, top_p=.8, top_k=20,
            learning_rate=1e-5, beta=0.0, bf16=True, gradient_checkpointing=True,
            gradient_checkpointing_kwargs=dict(use_reentrant=False), use_vllm=False,
            logging_steps=1, report_to='none', save_strategy='no', push_to_hub=False,
            seed=20261019, data_seed=20261019, dataloader_num_workers=0,
            log_completions=False, mask_truncated_completions=False)
        def factory():
            return RegionalTrainingEnv(output/'episodes')
        trainer = GRPOTrainer(model=model, processing_class=tokenizer, args=config,
            train_dataset=dataset, eval_dataset=dataset, reward_funcs=outcome_reward,
            environment_factory=factory,
            peft_config=LoraConfig(r=8, lora_alpha=16, lora_dropout=0,
                target_modules=['q_proj','k_proj','v_proj','o_proj'], task_type='CAUSAL_LM'))

        def adapter_digest():
            digest = hashlib.sha256()
            for name, parameter in trainer.model.named_parameters():
                if parameter.requires_grad:
                    digest.update(name.encode())
                    digest.update(parameter.detach().float().cpu().numpy().tobytes())
            return digest.hexdigest()

        before_digest = adapter_digest()
        before = trainer.evaluate(metric_key_prefix='before')
        trained = trainer.train()
        after = trainer.evaluate(metric_key_prefix='after')
        after_digest = adapter_digest()
        trainer.save_model(str(output/'adapter'))
        tokenizer.save_pretrained(output/'adapter')
        summaries = [json.loads(path.read_text()) for path in (output/'episodes').glob('*/summary.json')]
        result = dict(status='complete', benchmark=benchmark, optimizer_steps=trainer.state.global_step,
            adapter_changed=before_digest!=after_digest, before_adapter_sha256=before_digest,
            after_adapter_sha256=after_digest, before=before, after=after, train=trained.metrics,
            logs=trainer.state.log_history, independently_verified_episodes=len(summaries),
            model_submissions=sum(row['model_submissions'] for row in summaries),
            model_checks=sum(row['model_checks'] for row in summaries),
            elapsed_seconds=round(time.perf_counter()-started, 3),
            peak_cuda_allocated_bytes=torch.cuda.max_memory_allocated(),
            note='Two-update pipeline pilot, not demonstrated policy improvement or held-out generalization')
        dump(output/'summary.json', result)
        return result
    except BaseException as error:
        dump(output/'status.json', dict(status='incomplete', error_type=type(error).__name__,
            elapsed_seconds=round(time.perf_counter()-started, 3), published=False))
        raise


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--model', choices=['Qwen/Qwen3-0.6B', MODEL_ID], default=MODEL_ID)
    parser.add_argument('--revision')
    parser.add_argument('--steps', type=int, default=2)
    parser.add_argument('--completion-tokens', type=int, default=1536)
    parser.add_argument('--tool-iterations', type=int, default=8)
    args = parser.parse_args()
    result = run(args.output, args.model, args.revision, args.steps, args.completion_tokens, args.tool_iterations)
    print(json.dumps({key: value for key, value in result.items() if key not in ('logs', 'before', 'after', 'train')}))


if __name__ == '__main__':
    main()
