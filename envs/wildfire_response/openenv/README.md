# Wildfire OpenEnv adapter

The separate `wildfire-openenv` distribution depends on the local CPU core and the
tested OpenEnv 0.7.0 SDK. From repository root:

```sh
uv pip install --python .venv/bin/python -e envs/wildfire_response/core \
  -e 'envs/wildfire_response/openenv[dev]'
.venv/bin/python -m wildfire_openenv.server --port 8001
# In another terminal:
.venv/bin/python envs/wildfire_response/openenv/rollout.py --url http://127.0.0.1:8001
```

Use `wildfire_openenv.client.WildfireClient` and
`wildfire_openenv.models.WildfireAction`. These forward the already tested
regional wire contracts; check actions do not advance time, final actions do.
One active hosted episode is supported. The inherited v1 drone adapter is a
compatibility module, not the canonical server for this project.

Build the supplied container from repository root with
`docker build -f envs/wildfire_response/openenv/Dockerfile .`.
No container build or hosted Space deployment is claimed until explicitly tested.
The root layout follows PortSimEnv, but there is no copied port viewer, MCP
playground or AgentEnv/Harbor integration. No live communication account is needed.
