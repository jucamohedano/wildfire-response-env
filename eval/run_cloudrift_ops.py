"""Run the same small development sample through authorized CloudRift inference."""
import argparse
import atexit
import getpass
import json
import os
import signal
from urllib.request import urlopen

from eval.run_ops_model import run, stop_preview
from envs.wildfire_response.regional.ops.cloudrift import (
    BASE_URL, MODEL, CloudRiftOpsPolicy, CloudRiftTransport,
)
from wildfire_core.topographic import TopographicEnv


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--prepared-inputs', required=True)
    parser.add_argument('--prepared-sha256', required=True)
    parser.add_argument('--topographic-inputs', required=True)
    parser.add_argument('--topographic-sha256', required=True)
    parser.add_argument('--allow-cloudrift', action='store_true', required=True)
    parser.add_argument('--key-stdin', action='store_true')
    parser.add_argument('--usd-cap', type=float, default=5.)
    parser.add_argument('--max-requests', type=int, default=160)
    parser.add_argument('--max-tokens', type=int, default=4096)
    parser.add_argument('--seconds', type=int, default=1200)
    parser.add_argument('--stop-preview-pid-file')
    args = parser.parse_args()
    atexit.register(stop_preview, args.stop_preview_pid_file)
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    if not 0 < args.usd_cap <= 5 or not 1 <= args.max_requests <= 160 or not 256 <= args.max_tokens <= 4096 or not 1 <= args.seconds <= 1800:
        parser.error('Invalid explicitly bounded CloudRift evaluation budget')
    with urlopen(BASE_URL+'/models', timeout=25) as response:
        models = json.load(response)['data']
    metadata = next((model for model in models if model.get('id') == MODEL), None)
    if metadata is None:
        parser.error('The requested CloudRift model is not listed; no substitution attempted')
    key = getpass.getpass('CloudRift inference key (hidden, not stored): ') if args.key_stdin else os.environ.get('CLOUDRIFT_INFERENCE_KEY')
    transport = CloudRiftTransport(api_key=key, model_metadata=metadata, usd_cap=args.usd_cap)
    del key
    def factory():
        return TopographicEnv(args.prepared_inputs, args.topographic_inputs,
            expected_prepared_sha256=args.prepared_sha256, expected_topographic_sha256=args.topographic_sha256,
            slope_enabled=True, allow_synthetic_conditions=True)
    result = run(args.output, factory, transport, model=MODEL,
        policy_type=CloudRiftOpsPolicy, policy_options=dict(allow_cloudrift=True),
        provider='cloudrift', allow_nonzero_cost=True, provider_trial=True,
        max_requests=args.max_requests, max_tokens=args.max_tokens, seconds=args.seconds,
        preview_pid=args.stop_preview_pid_file)
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
