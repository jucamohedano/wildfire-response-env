"""First operations interface over the existing regional response simulator.

This facade changes tool names and public role labels only. It does not add a
reward, alter simulator rules, or define a certified emergency procedure.
"""
import copy

from ..environment import RegionalResponseEnv


class OpsControlCenterEnv:
    """Expose read, check, and committed operations for a control-center agent.

    Reads and order checks do not advance simulated time. Every committed
    operation delegates one combined action to exactly one core ``step``.
    Invalid committed actions retain the core's atomic failure and time-step
    behavior.
    """

    def __init__(self, *, max_decisions=60, ticks_per_action=2):
        self._core = RegionalResponseEnv(max_decisions=max_decisions,
                                         ticks_per_action=ticks_per_action)

    def reset(self, scenario_id='regional-000', seed=None):
        observation, info = self._core.reset(scenario_id, seed)
        return self._present(observation), copy.deepcopy(info)

    def get_situation(self):
        return self._present(self._core.observe())

    def get_reports(self, report_ids=None):
        if report_ids is not None and not isinstance(report_ids, list):
            raise ValueError('report_ids must be a list of received report IDs')
        identifiers = None if report_ids is None else copy.deepcopy(report_ids)
        return copy.deepcopy(self._core.reports(identifiers))

    def check_orders(self, action):
        return copy.deepcopy(self._core.check_action(action))

    def request_resources(self, requests):
        return self._commit(dict(requests=requests))

    def authorize_resources(self, offer_ids):
        return self._commit(dict(authorizations=offer_ids))

    def release_resources(self, resource_ids):
        return self._commit(dict(releases=resource_ids))

    def send_messages(self, messages):
        return self._commit(dict(messages=messages))

    def advance(self):
        return self._commit({})

    def submit_action(self, action):
        return self._commit(action)

    def _commit(self, action):
        observation, reward, terminated, truncated, info = self._core.step(action)
        return (self._present(observation), reward, terminated, truncated,
                copy.deepcopy(info))

    @staticmethod
    def _present(observation):
        public = copy.deepcopy(observation)
        public['authority_role'] = 'ops'
        rules = public.get('rules', {})
        if 'authority' in rules:
            rules['authority'] = rules['authority'].replace(
                'simulated provincial coordinator', 'simulated wildfire ops control center')
        if 'dispatch' in rules:
            rules['dispatch'] = rules['dispatch'].replace('director', 'ops')
        rules['authority_role'] = 'ops'
        for acknowledgement in public.get('acknowledgements', []):
            for field in ('source', 'recipient', 'via'):
                if acknowledgement.get(field) == 'director':
                    acknowledgement[field] = 'ops'
        return public
