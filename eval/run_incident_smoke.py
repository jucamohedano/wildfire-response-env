"""Sequential CPU smoke for reactive incident tasks; no provider calls."""
import argparse
import gzip
import json
from pathlib import Path
import resource
import subprocess
import time

from wildfire_core.incident import ReactiveOpsEnv
from wildfire_core.topographic import ProtectionBaseline
from eval.run_ops_model import dump


CASES = ('incident-dev-000', 'incident-dev-001', 'incident-dev-003')


def run(output, factory):
    output = Path(output)
    if output.exists():
        raise ValueError('Choose a fresh output directory')
    output.mkdir(parents=True)
    started = time.monotonic()
    summary = dict(version='wildfire-reactive-smoke-v1',
        scope='Three synthetic development fixtures on one source region; CPU baseline comparison, not model evaluation',
        source_revision=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        cases=[], workers=1, delayed_protection_minutes=12,
        recon_only='Requests and authorizes one reconnaissance engine; never orders protection or suppression')
    for task_id in CASES:
        policies = ('passive', 'early_protection', 'delayed_protection')
        if task_id == CASES[0]:
            policies += ('recon_only',)
        for policy in policies:
            environment = factory()
            observation, _ = environment.reset(task_id)
            baseline = ProtectionBaseline(prefer_shelter=False)
            case_started = time.monotonic()
            while True:
                wait = policy == 'passive' or policy == 'delayed_protection' and observation['minute'] < 12
                if policy == 'recon_only':
                    action = {}
                    if observation['decision'] == 0:
                        action = dict(requests=[dict(resource_id='engine-valverde', sector_id='report:claim-0', mission='recon')])
                    elif observation['decision'] == 1:
                        action = dict(authorizations=[offer['id'] for offer in observation['offers']],
                                      readback_confirmations=[entry['id'] for entry in observation['readbacks']])
                else:
                    action = {} if wait else baseline(observation)
                observation, _, terminated, truncated, info = environment.step(action)
                if info['error']:
                    raise ValueError(info['error'])
                if terminated or truncated:
                    break
            verified = environment.verify_episode()
            if not verified['passed']:
                raise ValueError('Reactive episode verification failed')
            trace = environment.trajectory()
            path = output / task_id / policy
            path.mkdir(parents=True)
            (path/'trajectory.json.gz').write_bytes(gzip.compress(json.dumps(trace, sort_keys=True, allow_nan=False).encode(), mtime=0))
            dump(path/'private'/'verification.json', verified)
            events = observation['incident_events']
            record = dict(task_id=task_id, policy=policy, score=environment.reward,
                minute=observation['minute'], decisions=observation['decision'],
                terminated=terminated, truncated=truncated,
                boundary_censored=observation['topographic_status']['boundary_censored'],
                exposed=sum(group['exposed'] for group in observation['districts']),
                received_updates=len(events), resident_calls=sum(event['kind'] == 'resident_smoke_call' for event in events),
                support_requests=sum(event['kind'] == 'crew_support_request' for event in events),
                objectives=verified['objective_result']['by_kind'],
                implementation_sha256=trace['implementation_sha256'],
                elapsed_seconds=round(time.monotonic() - case_started, 3),
                trajectory=str(path/'trajectory.json.gz'))
            summary['cases'].append(record)
            dump(output/'summary.json', summary)
            print(json.dumps(record), flush=True)
    summary.update(elapsed_seconds=round(time.monotonic() - started, 3),
                   peak_rss_mib=round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2))
    dump(output/'summary.json', summary)
    return summary


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--prepared-inputs', required=True)
    parser.add_argument('--prepared-sha256', required=True)
    parser.add_argument('--topographic-inputs', required=True)
    parser.add_argument('--topographic-sha256', required=True)
    options = parser.parse_args()
    def factory():
        return ReactiveOpsEnv(options.prepared_inputs, options.topographic_inputs,
            expected_prepared_sha256=options.prepared_sha256, expected_topographic_sha256=options.topographic_sha256,
            slope_enabled=True, allow_synthetic_conditions=True)
    print(json.dumps(run(options.output, factory)), flush=True)


if __name__ == '__main__':
    main()
