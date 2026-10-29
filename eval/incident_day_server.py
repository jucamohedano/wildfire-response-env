"""Loopback-only incident-day viewer and bounded single-session HTTP API."""
import argparse
import ipaddress
import json
import math
from pathlib import Path
from threading import RLock
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import Field

from wildfire_core.incident_day import fixture_inputs
from wildfire_openenv.incident_day import (Identifier, IncidentDayAction, IncidentDayEnvironment,
                                           ReportQuery, StatusRequest, StrictModel)


MAX_BODY_BYTES = 65536


class ResetQuery(StrictModel):
    scenario_id: Identifier | None = None
    seed: int | None = Field(default=None, ge=0, le=2**63 - 1)


class RouteQuery(StrictModel):
    resource_id: Identifier
    sector_id: Identifier = 'incident'


class EvacuationOptionsQuery(StrictModel):
    group_id: Identifier
    destination_id: Identifier


class ActionQuery(StrictModel):
    action: IncidentDayAction


def _authority(value):
    try:
        parsed = urlsplit('http://' + value)
        if (parsed.hostname not in ('127.0.0.1', 'localhost', '::1') or parsed.username is not None
                or parsed.password is not None or parsed.path or parsed.query or parsed.fragment
                or parsed.port == 0):
            return None
        return parsed.hostname, parsed.port
    except ValueError:
        return None


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON field')
        result[key] = value
    return result


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('Non-finite JSON number')
    return number


def _reject_constant(value):
    raise ValueError('Non-finite JSON number')


class LocalRequests:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)

        async def reject(code, detail):
            await JSONResponse({'detail': detail}, status_code=code)(scope, receive, send)

        headers = scope['headers']
        hosts = [value.decode('latin1') for key, value in headers if key == b'host']
        try:
            client = ipaddress.ip_address(scope['client'][0])
        except (ValueError, TypeError, IndexError):
            return await reject(403, 'Loopback client required')
        if not client.is_loopback or len(hosts) != 1 or _authority(hosts[0]) is None:
            return await reject(403, 'Loopback client and Host required')
        if scope.get('query_string'):
            return await reject(400, 'Query strings are not supported')
        origins = [value.decode('latin1') for key, value in headers if key == b'origin']
        if origins:
            try:
                origin = urlsplit(origins[0])
                same_origin = (len(origins) == 1 and origin.scheme == scope['scheme']
                    and _authority(origin.netloc) == _authority(hosts[0])
                    and not origin.path and not origin.query and not origin.fragment)
            except ValueError:
                same_origin = False
            if not same_origin:
                return await reject(403, 'Same-origin request required')
        elif any(key == b'sec-fetch-site' and value in (b'cross-site', b'same-site') for key, value in headers):
            return await reject(403, 'Cross-origin request denied')
        if scope['method'] != 'POST':
            return await self.app(scope, receive, send)
        lengths = [value for key, value in headers if key == b'content-length']
        try:
            if len(lengths) > 1 or (lengths and (not lengths[0].isdigit() or int(lengths[0]) < 0)):
                raise ValueError
            if lengths and int(lengths[0]) > MAX_BODY_BYTES:
                return await reject(413, 'Request body too large')
        except ValueError:
            return await reject(400, 'Invalid Content-Length')
        body = bytearray()
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            body.extend(message.get('body', b''))
            if len(body) > MAX_BODY_BYTES:
                return await reject(413, 'Request body too large')
            if not message.get('more_body', False):
                break
        try:
            json.loads(body, object_pairs_hook=_unique_fields, parse_float=_finite_float,
                       parse_constant=_reject_constant)
        except (ValueError, UnicodeError, RecursionError):
            return await reject(422, 'Invalid JSON body')
        delivered = False

        async def buffered_receive():
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {'type': 'http.request', 'body': bytes(body), 'more_body': False}

        return await self.app(scope, buffered_receive, send)


VIEWER = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Incident day exercise</title><style>
body{font:16px system-ui;margin:2rem auto;max-width:1100px;padding:0 1rem;color:#18282b;background:#f3f5f1}
button,input,select{font:inherit;padding:.4rem}button{cursor:pointer}section{background:white;padding:1rem;margin:1rem 0;border-radius:.5rem}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:.8rem}input[type=range]{width:100%}.bar{display:flex;gap:1rem;align-items:center;flex-wrap:wrap}
</style></head><body><h1>Incident day exercise</h1>
<div class="bar"><label for="scenario">Scenario</label><select id="scenario"></select><button id="reset">Reset exercise</button><button id="wait">Wait up to 60 minutes</button><strong id="clock">Not started</strong></div>
<p id="message"></p><section><label for="history">Recorded trajectory</label><input id="history" type="range" min="0" max="0" value="0"><div id="frame"></div></section>
<section><h2>Protection</h2><pre id="protection"></pre></section><section><h2>Resources</h2><pre id="resources"></pre></section>
<section><h2>Reports</h2><pre id="reports"></pre></section>
<script>
let frames=[];
async function call(path,body){const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const result=await response.json();if(!response.ok)throw Error(JSON.stringify(result));return result}
function show(){if(!frames.length)return;const i=Number(document.getElementById('history').value),s=frames[i];document.getElementById('clock').textContent=`Minute ${frames[frames.length-1].minute} / 1440`;document.getElementById('frame').textContent=`Recorded minute ${s.minute} (${i+1} / ${frames.length})`;document.getElementById('protection').textContent=JSON.stringify({districts:s.districts,orders:s.protection_orders,destinations:s.destinations},null,2);document.getElementById('resources').textContent=JSON.stringify(s.resources,null,2);document.getElementById('reports').textContent=JSON.stringify(s.reports,null,2)}
async function refresh(){const r=await fetch('/state');if(!r.ok)return;const data=await r.json();frames=data.trajectory;const slider=document.getElementById('history');slider.max=Math.max(0,frames.length-1);slider.value=slider.max;show()}
async function run(path,body){try{await call(path,body);document.getElementById('message').textContent='';await refresh()}catch(error){document.getElementById('message').textContent=error.message}}
async function loadTasks(){try{const r=await fetch('/tasks');if(!r.ok)throw Error('Could not load scenarios');const data=await r.json(),select=document.getElementById('scenario');if(!data.default_scenario_id){const placeholder=document.createElement('option');placeholder.value='';placeholder.textContent='Select a scenario';select.appendChild(placeholder)}for(const task of data.tasks){const option=document.createElement('option');option.value=task.id;option.textContent=task.id;select.appendChild(option)}if(data.default_scenario_id)select.value=data.default_scenario_id}catch(error){document.getElementById('message').textContent=error.message}}
document.getElementById('reset').onclick=()=>run('/reset',{scenario_id:document.getElementById('scenario').value||null});document.getElementById('wait').onclick=()=>run('/step',{action:{advance_minutes:60}});document.getElementById('history').oninput=show;loadTasks();refresh();
</script></body></html>'''


def create_app(environment):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(LocalRequests)
    lock = RLock()

    def execute(function):
        with lock:
            try:
                return function()
            except RuntimeError as error:
                raise HTTPException(409, str(error)) from error
            except (ValueError, TypeError, KeyError) as error:
                raise HTTPException(400, str(error)) from error

    @app.get('/', response_class=HTMLResponse)
    def viewer():
        return VIEWER

    @app.get('/tasks')
    def tasks():
        def discover():
            tasks = environment.list_tasks('development')
            try:
                default = environment._select_case(None)
            except ValueError:
                default = None
            return {'tasks': tasks, 'default_scenario_id': default}
        return execute(discover)

    @app.post('/reset')
    def reset(query: ResetQuery):
        return execute(lambda: environment.reset(seed=query.seed, task_id=query.scenario_id).model_dump())

    @app.post('/step')
    def step(query: ActionQuery):
        return execute(lambda: environment.step(query.action).model_dump())

    @app.post('/check')
    def check(query: ActionQuery):
        return execute(lambda: environment.check_action(query.action))

    @app.post('/routes')
    def routes(query: RouteQuery):
        return execute(lambda: environment.route_options(query.resource_id, query.sector_id))

    @app.post('/evacuation-options')
    def evacuation_options(query: EvacuationOptionsQuery):
        return execute(lambda: environment.evacuation_options(query.group_id, query.destination_id))

    @app.post('/status')
    def status(query: StatusRequest):
        return execute(lambda: environment.request_status(query.center_id, query.subject).model_dump())

    @app.post('/reports')
    def reports(query: ReportQuery):
        return execute(lambda: {'reports': environment.get_reports(query.ids, query.after_cursor, query.limit)})

    @app.get('/state')
    def state():
        return execute(lambda: {'state': environment.state.model_dump(),
            'situation': environment.get_situation(), 'trajectory': environment.trajectory()})

    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', choices=('127.0.0.1', 'localhost', '::1'), default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8005)
    parser.add_argument('--fixture', action='store_true')
    parser.add_argument('--inputs', type=Path)
    parser.add_argument('--network', type=Path)
    parser.add_argument('--inputs-sha256')
    parser.add_argument('--network-sha256')
    parser.add_argument('--allow-unapproved-data', action='store_true')
    parser.add_argument('--allow-archived-scenarios', action='store_true', help='Enable explicit legacy scenario replay')
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    if args.fixture:
        if any((args.inputs, args.network, args.inputs_sha256, args.network_sha256, args.allow_unapproved_data)):
            parser.error('fixture cannot be combined with custom data options')
        inputs, network = fixture_inputs()
        inputs_hash, network_hash = inputs['sha256'], network['sha256']
    else:
        if not all((args.inputs, args.network, args.inputs_sha256, args.network_sha256)):
            parser.error('provide --fixture or both data paths and their SHA256 pins')
        inputs, network = args.inputs, args.network
        inputs_hash, network_hash = args.inputs_sha256, args.network_sha256
    environment = IncidentDayEnvironment(inputs, network, expected_inputs_sha256=inputs_hash,
        expected_network_sha256=network_hash, allow_unapproved_data=args.allow_unapproved_data,
        allow_archived_scenarios=args.allow_archived_scenarios)
    import uvicorn
    uvicorn.run(create_app(environment), host=args.host, port=args.port)


if __name__ == '__main__':
    main()
