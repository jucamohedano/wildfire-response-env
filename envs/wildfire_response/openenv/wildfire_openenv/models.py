"""Typed wire contracts retained from the tested regional adapter."""
from envs.wildfire_response.regional.openenv.models import RegionalAction as WildfireAction
from envs.wildfire_response.regional.openenv.models import RegionalObservation as WildfireObservation
from envs.wildfire_response.regional.openenv.models import RegionalState as WildfireState

__all__ = ['WildfireAction', 'WildfireObservation', 'WildfireState']
