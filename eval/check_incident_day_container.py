"""Build and exercise the real Linux image through an external loopback client."""

import argparse
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'envs/wildfire_response/core/src'), str(ROOT / 'envs/wildfire_response/openenv')]

from wildfire_core.incident_day import COVERAGE_VARIANT_IDS, IncidentDayEnv, fixture_inputs
from wildfire_openenv.incident_day_client import IncidentDayClient
from envs.wildfire_response.incident_day.baselines import DayDispatcher
from envs.wildfire_response.incident_day.environment import implementation_digest


def command(arguments, timeout=300, capture=False):
    return subprocess.run(arguments, cwd=ROOT, check=True, text=True, timeout=timeout,
                          stdout=subprocess.PIPE if capture else None).stdout


def compare(actual, expected, label):
    if actual != expected:
        raise ValueError(label + ' differs between source and container')


def run(output, budget):
    output = Path(output).absolute()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ValueError('Use a fresh report path')
    if not 60 <= budget <= 240:
        raise ValueError('Client wall budget must be between 60 and 240 seconds')
    serial = uuid.uuid4().hex[:12]
    image, name = 'wildfire-day-check:' + serial, 'wildfire-day-check-' + serial
    report = dict(version='incident-day-container-check-v1', status='incomplete',
        source_revision=command(['git', 'rev-parse', 'HEAD'], capture=True).strip(),
        implementation_sha256=implementation_digest(), cases=[],
        scope='Synthetic fixture, actual installed Linux image, external Python client; no publication or model inference')
    started = time.monotonic()
    launched = False
    try:
        command(['docker', 'version'])
        command(['docker', 'pull', 'python:3.12-slim'])
        base = json.loads(command(['docker', 'image', 'inspect', 'python:3.12-slim'], capture=True))[0]
        report['base_image'] = base['RepoDigests'][0]
        command(['docker', 'build', '--file', 'envs/wildfire_response/openenv/Dockerfile.incident-day',
                 '--build-arg', 'BASE_IMAGE=' + report['base_image'], '--tag', image, '.'])
        metadata = json.loads(command(['docker', 'image', 'inspect', image], capture=True))[0]
        report.update(image_id=metadata['Id'], image_bytes=metadata['Size'],
                      runtime_user=metadata['Config']['User'])
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        command(['docker', 'run', '--detach', '--name', name, '--network', 'host',
                 '--read-only', '--tmpfs', '/tmp:rw,noexec,nosuid,size=128m',
                 '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                 '--memory', '768m', '--cpus', '1', '--pids-limit', '128', image,
                 'python', '-m', 'eval.incident_day_server', '--fixture', '--port', str(port)])
        launched = True
        url = 'http://127.0.0.1:' + str(port)
        deadline = time.monotonic() + budget
        opener = build_opener(ProxyHandler({}))
        while True:
            try:
                with opener.open(url + '/', timeout=2) as response:
                    if response.status == 200:
                        break
            except (URLError, TimeoutError):
                if time.monotonic() >= deadline:
                    raise TimeoutError('Container did not become ready')
                time.sleep(.2)
        installed_hash = command(['docker', 'exec', name, 'python', '-c',
            'from envs.wildfire_response.incident_day.environment import implementation_digest; print(implementation_digest())'], capture=True).strip()
        compare(installed_hash, report['implementation_sha256'], 'Installed implementation identity')
        client = IncidentDayClient(url)
        inputs, network = fixture_inputs()
        for case_id in COVERAGE_VARIANT_IDS:
            local = IncidentDayEnv(inputs, network, expected_inputs_sha256=inputs['sha256'],
                                   expected_network_sha256=network['sha256'])
            observation, unused = local.reset(case_id)
            compare(client.reset(case_id)['data'], observation, 'Reset observation')
            compare(client.check_action({'advance_minutes': 1}), local.check_action({'advance_minutes': 1}), 'Draft check')
            frames = [observation]

            class Routes:
                def route_options(self, resource_id, sector_id='incident'):
                    value = client.route_options(resource_id, sector_id)
                    compare(value, local.route_options(resource_id, sector_id), 'Resource routes')
                    return value

                def evacuation_options(self, group_id, destination_id):
                    value = client.evacuation_options(group_id, destination_id)
                    compare(value, local.evacuation_options(group_id, destination_id), 'Evacuation routes')
                    return value

            policy = DayDispatcher()
            proxy = Routes()
            for decision in range(1000):
                if time.monotonic() >= deadline:
                    raise TimeoutError('External-client wall budget exhausted')
                action = policy.action(proxy, observation)
                observation, reward, terminated, truncated, unused_info = local.step(action)
                result = client.step(action)
                compare((result['data'], result['reward'], result['terminated'], result['truncated']),
                        (observation, reward, terminated, truncated), 'Step')
                frames.append(observation)
                if terminated or truncated:
                    break
            if local.minute != 1440 or not local.terminated or local.truncated:
                raise ValueError('Client did not complete the incident day')
            compare(client.get_state()['trajectory'], frames, 'Public replay frames')
            verification = local.verify_episode()
            if not all(verification[key] for key in ('passed', 'exact_replay', 'independent_reward_agreement')):
                raise ValueError('Independent host verification failed')
            report['cases'].append(dict(case=case_id, completed_minutes=local.minute,
                decisions=decision + 1, exact_http_parity=True, verification=verification,
                qualification='Remote public steps/reward match the independently verified local episode; remote private state is not exposed'))
        report['status'] = 'passed'
    except Exception as error:
        report.update(status='failed', error=type(error).__name__ + ': ' + str(error))
    finally:
        if launched:
            state = subprocess.run(['docker', 'inspect', name], capture_output=True, text=True)
            if state.returncode == 0:
                report['container_state'] = json.loads(state.stdout)[0]['State']
            subprocess.run(['docker', 'logs', '--tail', '20', name], check=False)
            cleanup = subprocess.run(['docker', 'rm', '--force', name], check=False)
            report['container_removed'] = cleanup.returncode == 0
        if shutil.which('docker'):
            subprocess.run(['docker', 'image', 'rm', image], check=False, capture_output=True, timeout=30)
        report['elapsed_seconds'] = time.monotonic() - started
        output.write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--wall-budget', type=int, default=240)
    options = parser.parse_args()
    result = run(options.output, options.wall_budget)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['status'] == 'passed' else 1)
