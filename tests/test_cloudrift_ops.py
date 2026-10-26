import io
import json
from decimal import Decimal
import unittest
from urllib.error import HTTPError

from envs.wildfire_response.regional.ops.cloudrift import (
    BASE_URL, MODEL, CloudRiftHTTPError, CloudRiftOpsPolicy, CloudRiftTransport, NoRedirects,
)
from wildfire_core.ops_model import OpsModelPolicy
from eval.run_ops_model import BoundedTransport


def metadata():
    return dict(id=MODEL, pricing=dict(prompt='0.000014', completion='0.000028'))


class Reply:
    def __init__(self, data, content_type='application/json'):
        self.stream = io.BytesIO(json.dumps(data).encode())
        self.headers = {'Content-Type': content_type}

    def read1(self, size):
        return self.stream.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *arguments):
        pass


class Opener:
    def __init__(self, data=None, error=None):
        self.data, self.error, self.requests = data, error, []

    def open(self, request, timeout):
        self.requests.append(request)
        if self.error:
            raise self.error
        return Reply(self.data)


class CloudRiftTests(unittest.TestCase):
    def transport(self, cap=5):
        return CloudRiftTransport(api_key='rift_fake_test_secret', model_metadata=metadata(), usd_cap=cap)

    def payload(self):
        return dict(model=MODEL, messages=[dict(role='user', content='Synthetic ops context')],
                    tools=[dict(type='function', function=dict(name='submit_action'))], max_tokens=512,
                    provider={'max_price': {'prompt': 0}}, reasoning={'enabled': False})

    def test_user_endpoint_and_exact_nonstreaming_sampling_parameters(self):
        transport = self.transport()
        transport.opener = Opener(dict(choices=[], usage=dict(prompt_tokens=100, completion_tokens=20)))
        transport(self.payload())
        request = transport.opener.requests[0]
        self.assertEqual(request.full_url, BASE_URL+'/chat/completions')
        body = json.loads(request.data)
        self.assertFalse(body['stream'])
        self.assertEqual(body['model'], MODEL)
        self.assertEqual(body['temperature'], 1.)
        self.assertEqual(body['top_p'], 1.)
        self.assertEqual(body['n'], 1)
        self.assertEqual(body['presence_penalty'], 0.)
        self.assertEqual(body['frequency_penalty'], 0.)
        self.assertNotIn('provider', body)
        self.assertNotIn('reasoning', body)
        self.assertNotIn('rift_fake_test_secret', request.data.decode())
        self.assertEqual(transport.spent, Decimal('0.00196'))
        self.assertEqual(transport.budget_status()['prompt_tokens'], 100)

    def test_cost_is_reserved_before_calls_and_missing_usage_is_conservative(self):
        tiny = self.transport(.001)
        tiny.opener = Opener({})
        with self.assertRaisesRegex(RuntimeError, 'cap reached'):
            tiny(self.payload())
        self.assertEqual(tiny.opener.requests, [])
        transport = self.transport()
        transport.opener = Opener(dict(choices=[]))
        transport(self.payload())
        self.assertGreater(transport.spent, 0)
        self.assertEqual(transport.usage_responses, 0)
        self.assertNotIn('rift_fake_test_secret', json.dumps(transport.budget_status()))

    def test_explicit_cloudrift_opt_in_does_not_weaken_openrouter_free_model_guard(self):
        with self.assertRaises(ValueError):
            OpsModelPolicy(model=MODEL, transport=lambda payload: {})
        with self.assertRaises(ValueError):
            CloudRiftOpsPolicy(model=MODEL, transport=lambda payload: {})
        actor = CloudRiftOpsPolicy(model=MODEL, transport=lambda payload: {}, allow_cloudrift=True)
        self.assertEqual(actor.model, MODEL)
        with self.assertRaises(ValueError):
            CloudRiftOpsPolicy(model='another-model', transport=lambda payload: {}, allow_cloudrift=True)
        with self.assertRaises(ValueError):
            self.transport().request_body(dict(self.payload(), model='another-model'))

    def test_http_limit_stops_once_and_redacts_key_and_account_data(self):
        transport = self.transport()
        error = HTTPError(BASE_URL, 429, 'limit', {}, io.BytesIO(json.dumps(dict(
            error=dict(message='Limit for rift_fake_test_secret'), user_id='private-account')).encode()))
        transport.opener = Opener(error=error)
        with self.assertRaises(CloudRiftHTTPError) as caught:
            transport(self.payload())
        self.assertEqual(caught.exception.status_code, 429)
        self.assertNotIn('rift_fake_test_secret', caught.exception.safe_message)
        self.assertNotIn('private-account', caught.exception.safe_message)
        self.assertEqual(len(transport.opener.requests), 1)
        self.assertGreater(transport.spent, 0)

    def test_cross_host_redirects_and_streaming_responses_are_not_accepted(self):
        with self.assertRaises(RuntimeError):
            NoRedirects().redirect_request(None, None, 302, '', {}, 'https://another.example/')
        transport = self.transport()
        class StreamingOpener:
            def open(self, request, timeout):
                return Reply({}, 'text/event-stream')
        transport.opener = StreamingOpener()
        with self.assertRaisesRegex(RuntimeError, 'streaming'):
            transport(self.payload())

    def test_nonzero_usage_requires_separate_explicit_wrapper_opt_in(self):
        with self.assertRaises(RuntimeError):
            BoundedTransport(lambda payload: dict(usage={'cost': .01}), interval=0)({})
        result = BoundedTransport(lambda payload: dict(usage={'cost': .01}), interval=0,
                                  allow_nonzero_cost=True)({})
        self.assertEqual(result['usage']['cost'], .01)


if __name__ == '__main__':
    unittest.main()
