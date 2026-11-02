"""Actionable public-identifier feedback, independent of the frozen v2 engine."""

from . import _capture_source
_LOADED_SOURCE_SHA256, _LOADED_CODE_MATCH = _capture_source(__name__, __file__, __loader__)


class FeedbackMixin:
    """Precheck approach identity, then retain the next engine's validation.

    Lookup instructions use only the submitted, publicly known identifiers.
    Validation never queries routes, reads hidden operational state, advances
    time, changes the action, or chooses a replacement approach on the actor's
    behalf. Structural and all other checks remain owned by the next validator.
    """

    def _validate(self, action):
        if isinstance(action, dict) and 'advance_minutes' not in action:
            requests = action.get('requests', [])
            if isinstance(requests, list) and len(requests) <= 6:
                for index, request in enumerate(requests):
                    fields = {'resource_id', 'sector_id', 'approach_id'}
                    if (not isinstance(request, dict) or set(request) != fields
                            or any(type(request[field]) is not str for field in fields)):
                        continue
                    path = f'requests[{index}]'
                    resource = request['resource_id']
                    if resource not in self._known_resources:
                        raise ValueError(f'{path}.resource_id: choose a publicly reported resource ID. '
                                         'Next lookup: get_situation().')
                    lookup = f'route_options(resource_id={resource!r})'
                    option = self._approaches.get(request['approach_id'])
                    if option is None:
                        raise ValueError(f'{path}.approach_id: choose an issued resource approach. '
                                         f'Next lookup: {lookup}.')
                    if 'group_id' in option or 'destination_id' in option:
                        raise ValueError(f'{path}.approach_id: an evacuation approach cannot be used '
                                         f'for a resource request. Next lookup: {lookup}.')
                    if option.get('resource_id') != resource:
                        raise ValueError(f'{path}.resource_id: the resource does not match this approach. '
                                         f'Next lookup: {lookup}.')
                    if option.get('sector_id') != request['sector_id']:
                        raise ValueError(f'{path}.sector_id: the sector does not match this approach. '
                                         f'Next lookup: {lookup}; its public default sector_id is \'incident\'.')

            orders = action.get('protection_orders', [])
            if isinstance(orders, list) and len(orders) <= 6:
                for index, order in enumerate(orders):
                    fields = {'group_id', 'action', 'destination_id', 'approach_id'}
                    if (not isinstance(order, dict) or set(order) != fields
                            or order.get('action') != 'evacuate'
                            or any(type(order[field]) is not str for field in fields)):
                        continue
                    path = f'protection_orders[{index}]'
                    group, destination = order['group_id'], order['destination_id']
                    if group not in self._known_groups:
                        raise ValueError(f'{path}.group_id: choose a publicly reported population group ID. '
                                         'Next lookup: get_situation().')
                    if destination not in self._known_destinations:
                        raise ValueError(f'{path}.destination_id: choose a publicly reported destination ID. '
                                         'Next lookup: get_situation().')
                    lookup = f'evacuation_options(group_id={group!r}, destination_id={destination!r})'
                    option = self._approaches.get(order['approach_id'])
                    if option is None:
                        raise ValueError(f'{path}.approach_id: choose an issued evacuation approach. '
                                         f'Next lookup: {lookup}.')
                    if 'resource_id' in option or 'sector_id' in option:
                        raise ValueError(f'{path}.approach_id: a resource approach cannot be used '
                                         f'for evacuation. Next lookup: {lookup}.')
                    if option.get('group_id') != group:
                        raise ValueError(f'{path}.group_id: the group does not match this approach. '
                                         f'Next lookup: {lookup}.')
                    if option.get('destination_id') != destination:
                        raise ValueError(f'{path}.destination_id: the destination does not match this approach. '
                                         f'Next lookup: {lookup}.')
        return super()._validate(action)
