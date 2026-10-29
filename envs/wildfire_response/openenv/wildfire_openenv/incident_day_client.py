"""Named, external HTTP client for a local incident-day exercise."""
import json
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        return None


class IncidentDayClient:
    """Use the dedicated REST API; reads are not OpenEnv WebSocket actions."""

    def __init__(self, base_url='http://127.0.0.1:8005', *, timeout=20):
        if not isinstance(base_url, str):
            raise ValueError('base_url must be a loopback HTTP URL')
        try:
            parsed = urlsplit(base_url)
            if (parsed.scheme != 'http' or parsed.hostname not in ('localhost', '127.0.0.1', '::1')
                    or parsed.username is not None or parsed.password is not None
                    or parsed.path not in ('', '/') or parsed.query or parsed.fragment or parsed.port == 0):
                raise ValueError
        except ValueError as error:
            raise ValueError('base_url must be a loopback HTTP URL') from error
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 60:
            raise ValueError('timeout must be greater than 0 and at most 60 seconds')
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self._opener = build_opener(ProxyHandler({}), _NoRedirects())

    def _request(self, path, body=None):
        data = json.dumps(body, allow_nan=False).encode('utf-8') if body is not None else None
        headers = {'Content-Type': 'application/json'} if data is not None else {}
        request = Request(self.base_url + path, data=data, headers=headers)
        with self._opener.open(request, timeout=self.timeout) as response:
            return json.load(response)

    def reset(self, scenario_id=None, seed=None):
        return self._request('/reset', {'scenario_id': scenario_id, 'seed': seed})

    def step(self, action):
        return self._request('/step', {'action': action})

    def check_action(self, action):
        return self._request('/check', {'action': action})

    def route_options(self, resource_id, sector_id='incident'):
        return self._request('/routes', {'resource_id': resource_id, 'sector_id': sector_id})

    def evacuation_options(self, group_id, destination_id):
        return self._request('/evacuation-options', {'group_id': group_id, 'destination_id': destination_id})

    def request_status(self, center_id, subject='situation'):
        return self._request('/status', {'center_id': center_id, 'subject': subject})

    def get_reports(self, ids=None, after_cursor=0, limit=20):
        return self._request('/reports', {'ids': ids, 'after_cursor': after_cursor, 'limit': limit})

    def list_tasks(self):
        return self._request('/tasks')

    def get_state(self):
        return self._request('/state')
