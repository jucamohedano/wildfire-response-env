"""Local UI transport for caller-pinned, public wildfire exercises."""
import argparse
import copy
import json
import math
from pathlib import Path
from threading import RLock
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from wildfire_core.prepared import load_inputs
from wildfire_core.incident import CASE_IDS as INCIDENT_CASE_IDS, ReactiveOpsEnv, verify_reactive_trajectory
from wildfire_core.stress import StressEnv, build_catalog, verify_stress_trajectory
from wildfire_core.topographic import (CASE_IDS, ProtectionBaseline, TopographicEnv,
                                       load_domain, verify_topographic_trajectory)


MAX_BODY_BYTES = 1024 * 1024
MAX_IMPORT_BYTES = 20 * 1024 * 1024
SMOKE_GROUP_INDICES = (*range(6), *range(18, 24))
LOOPBACK_HOSTS = {'localhost', '127.0.0.1', '::1'}


class Payload(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class ResetPayload(Payload):
    task_id: str


class EpisodePayload(Payload):
    episode_id: str
    expected_decision: int = Field(ge=0)


class ActionPayload(EpisodePayload):
    action: dict[str, Any]


class AdvancePayload(EpisodePayload):
    policy: Literal['passive', 'evacuate', 'shelter']


class ImportPayload(Payload):
    trajectory: dict[str, Any] | None = None
    trajectory_json: str | None = None


def unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON field')
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError('Non-finite JSON number')


def local_authority(value):
    try:
        parsed = urlsplit('http://' + value)
        return (parsed.hostname in LOOPBACK_HOSTS and parsed.port != 0
                and parsed.username is None and parsed.password is None
                and not parsed.path and not parsed.query and not parsed.fragment)
    except ValueError:
        return False


class LocalRequests:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = scope['headers']
        hosts = [value.decode('latin1') for key, value in headers if key == b'host']
        if len(hosts) != 1 or not local_authority(hosts[0]):
            return await JSONResponse({'detail': 'Loopback Host required'}, 403)(scope, receive, send)
        if scope['method'] != 'POST':
            return await self.app(scope, receive, send)
        origins = [value.decode('latin1') for key, value in headers if key == b'origin']
        if origins:
            try:
                origin = urlsplit(origins[0])
                allowed = (len(origins) == 1 and origin.scheme in ('http', 'https')
                           and local_authority(origin.netloc) and not origin.path
                           and not origin.query and not origin.fragment)
            except ValueError:
                allowed = False
            if not allowed:
                return await JSONResponse({'detail': 'Loopback Origin required'}, 403)(scope, receive, send)
        elif (b'sec-fetch-site', b'cross-site') in headers:
            return await JSONResponse({'detail': 'Cross-site mutation denied'}, 403)(scope, receive, send)
        limit = MAX_IMPORT_BYTES if scope['path'] == '/api/import' else MAX_BODY_BYTES
        lengths = [value for key, value in headers if key == b'content-length']
        try:
            if len(lengths) > 1 or (lengths and int(lengths[0]) < 0):
                raise ValueError
            if lengths and int(lengths[0]) > limit:
                return await JSONResponse({'detail': 'Request body too large'}, 413)(scope, receive, send)
        except ValueError:
            return await JSONResponse({'detail': 'Invalid Content-Length'}, 400)(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            body.extend(message.get('body', b''))
            if len(body) > limit:
                return await JSONResponse({'detail': 'Request body too large'}, 413)(scope, receive, send)
            if not message.get('more_body', False):
                break
        try:
            json.loads(body, object_pairs_hook=unique_fields, parse_constant=reject_constant,
                       parse_float=finite_float)
        except (ValueError, UnicodeError, RecursionError):
            return await JSONResponse({'detail': 'Invalid JSON body'}, 422)(scope, receive, send)

        delivered = False

        async def buffered_receive():
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {'type': 'http.request', 'body': bytes(body), 'more_body': False}

        await self.app(scope, buffered_receive, send)


def finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('Non-finite JSON number')
    return number


def public_summary(result):
    return dict(passed=result['passed'], objective_score=result['objective_score'],
                by_kind=result['objective_result']['by_kind'],
                boundary_censored=result['boundary_censored'],
                suitable_for_training=result['suitable_for_training'])


def trace_frames(trajectory):
    frames = [dict(observation=trajectory['initial_observation'], action=None, reward=0,
                   total_reward=0, terminated=False, truncated=False)]
    total = 0
    for transition in trajectory['transitions']:
        total += transition['objective_reward']
        frames.append(dict(observation=transition['observation'], action=transition['action'],
            reward=transition['objective_reward'], total_reward=total,
            terminated=transition['terminated'], truncated=transition['truncated']))
    return frames


class Episode:
    def __init__(self, prepared, domain, catalog):
        self.prepared, self.domain, self.catalog = prepared, domain, catalog
        self.lock = RLock()
        self.cases = {identifier: 'development' for identifier in CASE_IDS}
        self.cases.update({identifier: 'reactive' for identifier in INCIDENT_CASE_IDS})
        for index in SMOKE_GROUP_INDICES:
            group = catalog['groups'][index]
            case = group['cases'][0]
            if group['split'] != 'train' or case['variant'] != 0:
                raise ValueError('Registered smoke cases must be training variant zero')
            self.cases[case['id']] = 'stress'
        self.reset(CASE_IDS[0])

    def reset(self, task_id):
        if task_id not in self.cases:
            raise HTTPException(422, 'Task is not in the UI case allowlist')
        options = dict(expected_prepared_sha256=self.prepared['sha256'],
            expected_topographic_sha256=self.domain['sha256'], slope_enabled=True,
            allow_synthetic_conditions=True, max_decisions=60, ticks_per_action=2)
        if self.cases[task_id] == 'stress':
            environment = StressEnv(self.prepared, self.domain, self.catalog,
                expected_catalog_sha256=self.catalog['sha256'], **options)
        elif self.cases[task_id] == 'reactive':
            environment = ReactiveOpsEnv(self.prepared, self.domain, **options)
        else:
            environment = TopographicEnv(self.prepared, self.domain, **options)
        observation, _ = environment.reset(task_id)
        self.environment = environment
        self.policies = {name: ProtectionBaseline(prefer_shelter=name == 'shelter')
                         for name in ('evacuate', 'shelter')}
        self.envelope = dict(episode_id=uuid4().hex, observation=observation, reward=0,
            total_reward=0, terminated=False, truncated=False, info={}, last_action=None)
        self.frames = [dict(observation=copy.deepcopy(observation), action=None, reward=0,
                            total_reward=0, terminated=False, truncated=False)]
        return copy.deepcopy(self.envelope)

    def require_current(self, payload, *, live=False):
        if (payload.episode_id != self.envelope['episode_id']
                or payload.expected_decision != self.envelope['observation']['decision']):
            raise HTTPException(409, 'Stale episode or decision; refresh state before retrying')
        if live and (self.envelope['terminated'] or self.envelope['truncated']):
            raise HTTPException(409, 'Episode is complete; reset before advancing')

    def step(self, action):
        try:
            observation, reward, terminated, truncated, info = self.environment.step(action)
        except (ValueError, TypeError) as error:
            raise HTTPException(422, str(error)) from error
        self.envelope.update(observation=observation, reward=reward,
            total_reward=self.environment.reward, terminated=terminated, truncated=truncated,
            info=info, last_action=copy.deepcopy(action))
        self.frames.append(dict(observation=copy.deepcopy(observation), action=copy.deepcopy(action),
            reward=reward, total_reward=self.environment.reward, terminated=terminated, truncated=truncated))
        return copy.deepcopy(self.envelope)

    def import_trace(self, trajectory):
        task_id = trajectory.get('task_id')
        if not isinstance(task_id, str) or task_id not in self.cases:
            raise HTTPException(422, 'Task is not in the UI case allowlist')
        if (type(trajectory.get('max_decisions')) is not int or trajectory['max_decisions'] != 60
                or type(trajectory.get('ticks_per_action')) is not int or trajectory['ticks_per_action'] != 2
                or trajectory.get('slope_enabled') is not True):
            raise HTTPException(422, 'Trace must use the pinned UI episode settings')
        try:
            if self.cases[task_id] == 'development':
                result = verify_topographic_trajectory(trajectory, self.prepared, self.domain)
            elif self.cases[task_id] == 'reactive':
                result = verify_reactive_trajectory(trajectory, self.prepared, self.domain)
            else:
                result = verify_stress_trajectory(trajectory, self.prepared, self.domain, self.catalog)
            frames = trace_frames(trajectory)
        except (ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError, RecursionError) as error:
            raise HTTPException(422, 'Trace does not replay against the pinned public contract') from error
        return dict(frames=frames, task_id=task_id, total_reward=result['total_score'], read_only=True)


def create_app(prepared_inputs, topographic_inputs, *, expected_prepared_sha256,
               expected_topographic_sha256, expected_catalog_sha256=None,
               enable_evaluator=False, terrain=None, dist=None):
    prepared = load_inputs(prepared_inputs, expected_sha256=expected_prepared_sha256)
    domain = load_domain(topographic_inputs, prepared, expected_topographic_sha256)
    catalog = build_catalog(prepared, domain)
    if expected_catalog_sha256 is not None and expected_catalog_sha256 != catalog['sha256']:
        raise ValueError('Catalog differs from the caller-pinned digest')
    episode = Episode(prepared, domain, catalog)
    terrain_cache = copy.deepcopy(terrain)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(LocalRequests)

    def map_data():
        nonlocal terrain_cache
        if terrain_cache is None:
            from eval.ui_terrain import render_terrain
            try:
                terrain_cache = render_terrain(prepared, domain)
            except (ValueError, OSError) as error:
                raise HTTPException(503, 'Terrain display failed validation; restore the pinned source cache or inspect the offline renderer') from error
        return terrain_cache

    @app.get('/api/config')
    def config():
        return dict(cases=[dict(id=identifier, mode=mode) for identifier, mode in episode.cases.items()],
                    ticks_per_action=2, max_decisions=60, evaluation_available=bool(enable_evaluator))

    @app.get('/api/map')
    def map_metadata():
        with episode.lock:
            data = map_data()
            return dict(extent=copy.deepcopy(data['extent']), crs=data['crs'],
                        image_url='/api/map.png', source_label=data['source_label'])

    @app.get('/api/map.png')
    def map_png():
        with episode.lock:
            return Response(map_data()['png'], media_type='image/png')

    @app.get('/api/state')
    def state():
        with episode.lock:
            return copy.deepcopy(episode.envelope)

    @app.post('/api/reset')
    def reset(payload: ResetPayload):
        with episode.lock:
            return episode.reset(payload.task_id)

    @app.post('/api/action')
    def action(payload: ActionPayload):
        with episode.lock:
            episode.require_current(payload, live=True)
            return episode.step(payload.action)

    @app.post('/api/advance')
    def advance(payload: AdvancePayload):
        with episode.lock:
            episode.require_current(payload, live=True)
            action = ({} if payload.policy == 'passive' else
                      episode.policies[payload.policy](copy.deepcopy(episode.envelope['observation'])))
            return episode.step(action)

    @app.post('/api/check')
    def check(payload: ActionPayload):
        with episode.lock:
            episode.require_current(payload, live=True)
            try:
                checked = episode.environment.check_orders(payload.action)
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
            return dict(episode_id=episode.envelope['episode_id'],
                        decision=episode.envelope['observation']['decision'], action_check=checked)

    @app.get('/api/trajectory')
    def trajectory():
        with episode.lock:
            return episode.environment.trajectory()

    @app.get('/api/history')
    def history(after: int | None = None):
        with episode.lock:
            frames = [frame for frame in episode.frames
                      if after is None or frame['observation']['decision'] > after]
            return dict(episode_id=episode.envelope['episode_id'], frames=copy.deepcopy(frames),
                        live_decision=episode.envelope['observation']['decision'])

    @app.post('/api/import')
    def import_trace(payload: ImportPayload):
        with episode.lock:
            if (payload.trajectory is None) == (payload.trajectory_json is None):
                raise HTTPException(422, 'Supply exactly one trajectory or original trajectory_json string')
            trajectory = payload.trajectory
            if payload.trajectory_json is not None:
                try:
                    trajectory = json.loads(payload.trajectory_json, object_pairs_hook=unique_fields,
                                            parse_constant=reject_constant, parse_float=finite_float)
                except (ValueError, UnicodeError, RecursionError) as error:
                    raise HTTPException(422, 'Invalid original trajectory JSON') from error
                if not isinstance(trajectory, dict):
                    raise HTTPException(422, 'Expected a trajectory JSON object')
            return episode.import_trace(trajectory)

    @app.get('/api/evaluator')
    def evaluator():
        if not enable_evaluator:
            raise HTTPException(404, 'Not found')
        with episode.lock:
            fire = [dict(column=column, row=row, heat=cell['heat'], burned=cell['burned'])
                    for row, cells in enumerate(episode.environment._core.sim.cells)
                    for column, cell in enumerate(cells) if cell['heat'] > 0 or cell['burned'] > 0]
            return dict(episode_id=episode.envelope['episode_id'],
                        decision=episode.envelope['observation']['decision'], world_fire=fire)

    @app.post('/api/verify')
    def verify(payload: EpisodePayload):
        with episode.lock:
            episode.require_current(payload)
            if not (episode.envelope['terminated'] or episode.envelope['truncated']):
                raise HTTPException(409, 'Finish the episode before verification')
            return public_summary(episode.environment.verify_episode())

    if dist is not None:
        directory = Path(dist).resolve(strict=True)
        if (directory / 'assets').is_symlink():
            raise ValueError('UI assets must be a directory inside the build')
        if (directory / 'assets').is_dir():
            app.mount('/assets', StaticFiles(directory=directory / 'assets', follow_symlink=False), name='assets')

        @app.get('/')
        def index():
            path = directory / 'index.html'
            if path.is_symlink() or not path.is_file():
                raise HTTPException(404, 'UI build not found')
            return FileResponse(path, media_type='text/html')

    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared-inputs', required=True)
    parser.add_argument('--prepared-sha256', required=True)
    parser.add_argument('--topographic-inputs', required=True)
    parser.add_argument('--topographic-sha256', required=True)
    parser.add_argument('--catalog-sha256')
    parser.add_argument('--enable-evaluator', action='store_true')
    parser.add_argument('--dist', default='ui/dist')
    parser.add_argument('--host', choices=('127.0.0.1', 'localhost', '::1'), default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8767)
    options = parser.parse_args(argv)
    app = create_app(options.prepared_inputs, options.topographic_inputs,
        expected_prepared_sha256=options.prepared_sha256,
        expected_topographic_sha256=options.topographic_sha256,
        expected_catalog_sha256=options.catalog_sha256,
        enable_evaluator=options.enable_evaluator, dist=options.dist)
    import uvicorn
    uvicorn.run(app, host=options.host, port=options.port)


if __name__ == '__main__':
    main()
