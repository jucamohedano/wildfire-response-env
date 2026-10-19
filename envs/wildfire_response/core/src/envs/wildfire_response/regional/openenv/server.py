"""Serve regional v2 locally; no model provider or private evaluator endpoint."""
from openenv.core.env_server import create_app

from .environment import RegionalEnvironment
from .models import RegionalAction, RegionalObservation, RegionalState

app = create_app(RegionalEnvironment,RegionalAction,RegionalObservation,
                 env_name='regional_wildfire_response', max_concurrent_envs=1, state_cls=RegionalState)


def main():
    import argparse
    import uvicorn
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument('--host',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=8001)
    args=parser.parse_args()
    uvicorn.run(app,host=args.host,port=args.port)


if __name__=='__main__':
    main()
