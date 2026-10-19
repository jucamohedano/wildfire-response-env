"""Run with python -m envs.wildfire_response.openenv.server."""
from openenv.core.env_server import create_app

from .environment import WildfireEnvironment
from .models import WildfireAction, WildfireObservation, WildfireState

app = create_app(WildfireEnvironment, WildfireAction, WildfireObservation,
                 env_name='wildfire_response', max_concurrent_envs=1, state_cls=WildfireState)


def main():
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=8000)


if __name__ == '__main__':
    main()
