"""Instruction-isolated Codex actor over a public-only loopback bridge."""
import argparse
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import time

from envs.wildfire_response.regional.benchmark_v1.model import BenchmarkModelPolicy
from eval.run_benchmark_cloudrift import run
from wildfire_core.benchmark_v1 import BenchmarkEnv


MODEL = 'gpt-6-luna'


class SubagentPolicy(BenchmarkModelPolicy):
    def validate_model(self, model):
        if model != MODEL:
            raise ValueError('Only the requested GPT-6 Luna subagent is supported')


class Bridge:
    def __init__(self, seconds=1800):
        self.condition = threading.Condition()
        self.pending = None
        self.reply = None
        self.sequence = 0
        self.finished = False
        self.deadline = time.monotonic() + seconds

    def __call__(self, payload):
        with self.condition:
            self.sequence += 1
            self.pending = dict(request_id=self.sequence, payload=copy.deepcopy(payload))
            self.reply = None
            self.condition.notify_all()
            while self.reply is None:
                remaining = self.deadline - time.monotonic()
                if remaining <= 0 or self.finished:
                    self.pending = None
                    raise RuntimeError('Subagent bridge deadline exhausted')
                self.condition.wait(min(remaining, 1))
            reply = self.reply
            self.pending = None
            return dict(model=MODEL, choices=[dict(finish_reason='tool_calls', message=dict(
                tool_calls=[dict(id=f'actor-{self.sequence}', type='function', function=dict(
                    name=reply['name'], arguments=json.dumps(reply['arguments'], allow_nan=False)))]))])

    def next(self, after=0):
        with self.condition:
            deadline = min(self.deadline, time.monotonic() + 55)
            while not self.finished and (self.pending is None or self.pending['request_id'] <= after):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return dict(waiting=True)
                self.condition.wait(remaining)
            return dict(finished=True) if self.finished else copy.deepcopy(self.pending)

    def submit(self, body):
        with self.condition:
            if (not isinstance(body, dict) or set(body) != {'request_id', 'name', 'arguments'}
                    or type(body['request_id']) is not int or not isinstance(body['name'], str)):
                raise ValueError('Expected request_id, tool name and JSON arguments')
            if (self.pending is None or body['request_id'] != self.pending['request_id']
                    or self.reply is not None or self.finished):
                raise ValueError('Stale or duplicate response; no action applied')
            self.reply = copy.deepcopy(body)
            self.condition.notify_all()

    def close(self):
        with self.condition:
            self.finished = True
            self.condition.notify_all()


def handler_for(bridge, capability):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, value, status=200):
            encoded = json.dumps(value, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            if not self.path.startswith('/'+capability+'/next?after='):
                return self.respond(dict(error='Not found'), 404)
            try:
                after = int(self.path.rsplit('=', 1)[1])
            except ValueError:
                return self.respond(dict(error='Invalid sequence'), 400)
            self.respond(bridge.next(after))

        def do_POST(self):
            if self.path != '/'+capability+'/reply':
                return self.respond(dict(error='Not found'), 404)
            try:
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 65536:
                    raise ValueError('Bounded JSON body required')
                def unique(pairs):
                    value = {}
                    for key, item in pairs:
                        if key in value:
                            raise ValueError('Duplicate JSON field')
                        value[key] = item
                    return value
                body = json.loads(self.rfile.read(length), object_pairs_hook=unique,
                                  parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
                bridge.submit(body)
            except (ValueError, TypeError):
                return self.respond(dict(error='Invalid or stale reply; no action applied'), 400)
            self.respond(dict(accepted=True))
    return Handler


def main():
    parser = argparse.ArgumentParser(__doc__)
    for name in ('output', 'prepared-inputs', 'topographic-inputs', 'connection-file'):
        parser.add_argument('--'+name, required=True)
    parser.add_argument('--seconds', type=int, default=1800)
    args = parser.parse_args()
    prepared = json.loads(Path(args.prepared_inputs).read_text())
    domain = json.loads(Path(args.topographic_inputs).read_text())
    def factory():
        return BenchmarkEnv(prepared, domain, expected_prepared_sha256=prepared['sha256'],
            expected_topographic_sha256=domain['sha256'], allow_synthetic_conditions=True)
    bridge = Bridge(args.seconds)
    capability = secrets.token_urlsafe(24)
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(bridge, capability))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = Path(args.connection_file)
    connection.parent.mkdir(parents=True, exist_ok=True)
    connection.write_text(json.dumps(dict(url=f'http://127.0.0.1:{server.server_port}/{capability}')))
    connection.chmod(0o600)
    try:
        result = run(args.output, factory, bridge, model=MODEL, policy_type=SubagentPolicy, policy_options={},
            seconds=args.seconds, max_requests=240, agent_metadata=dict(
                requested_model=MODEL, reasoning_effort='medium', fresh_context=True,
                bridge_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                isolation='Instruction-only; not a security sandbox. Actor instructed to use only public bridge.',
                differences='Persistent Codex context across decisions/cases; shell JSON tool wrapper instead of provider tool serialization; 240 request ceiling; model token sampling and USD usage unavailable.',
                billed_api_requests=0))
        print(json.dumps(dict(status=result['status'], requests=result['requests'])), flush=True)
    finally:
        bridge.close()
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
