"""Explicitly authorized, bounded non-streaming CloudRift inference."""
from decimal import Decimal, InvalidOperation
import json
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ...director.policy import ProviderHTTPError
from .model import OpsModelPolicy


MODEL = 'deepseek-ai/DeepSeek-V4-Flash-0731'
BASE_URL = 'https://inference.cloudrift.ai/v1'


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        raise RuntimeError('CloudRift redirects are not permitted for credential-bearing requests')


class CloudRiftHTTPError(ProviderHTTPError):
    def __init__(self, status_code, safe_message=None):
        self.status_code = status_code
        self.safe_message = safe_message
        RuntimeError.__init__(self, f'CloudRift HTTP {status_code}; no retry or fallback attempted')


class CloudRiftTransport:
    def __init__(self, *, api_key=None, model_metadata, usd_cap, timeout=90):
        self._api_key = api_key or os.environ.get('CLOUDRIFT_INFERENCE_KEY')
        if not self._api_key:
            raise ValueError('CloudRift inference key is unavailable')
        if model_metadata.get('id') != MODEL:
            raise ValueError('Expected metadata for the explicitly authorized CloudRift model')
        try:
            self.cap = Decimal(str(usd_cap))
            pricing = model_metadata['pricing']
            self.input_rate = Decimal(str(pricing['prompt']))
            self.output_rate = Decimal(str(pricing['completion']))
        except (KeyError, TypeError, InvalidOperation):
            raise ValueError('Missing or invalid API-advertised price metadata') from None
        if (not self.cap.is_finite() or not 0 < self.cap <= 5
                or any(not rate.is_finite() or rate < 0 for rate in (self.input_rate, self.output_rate))):
            raise ValueError('Use a positive estimated cost cap of at most5USD and finite prices')
        self.timeout = timeout
        self.spent = Decimal(0)
        self.prompt_tokens = self.completion_tokens = self.calls = self.usage_responses = 0
        self.opener = build_opener(NoRedirects())

    def request_body(self, payload):
        if payload.get('model') != MODEL:
            raise ValueError('CloudRift model substitution is not permitted')
        return dict(model=MODEL, messages=payload['messages'], tools=payload['tools'],
                    tool_choice='auto', temperature=1., max_tokens=payload['max_tokens'],
                    stream=False, top_p=1., n=1, presence_penalty=0., frequency_penalty=0.)

    def __call__(self, payload):
        body = self.request_body(payload)
        encoded = json.dumps(body, allow_nan=False).encode()
        reserve = (Decimal(len(encoded)+4096)*self.input_rate
                   + Decimal(body['max_tokens'])*self.output_rate)
        if self.spent+reserve > self.cap:
            raise RuntimeError('Conservative CloudRift estimated cost cap reached before next request')
        self.spent += reserve
        self.calls += 1
        request = Request(BASE_URL+'/chat/completions', data=encoded, method='POST', headers={
            'Authorization': 'Bearer '+self._api_key, 'Content-Type': 'application/json'})
        try:
            started = time.monotonic()
            with self.opener.open(request, timeout=self.timeout) as response:
                if 'text/event-stream' in response.headers.get('Content-Type', ''):
                    raise RuntimeError('CloudRift returned streaming data for a non-streaming request')
                chunks, size = [], 0
                while True:
                    if time.monotonic()-started >= self.timeout:
                        raise TimeoutError('CloudRift response deadline exceeded')
                    chunk = response.read1(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > 2_000_000:
                        raise RuntimeError('CloudRift response exceeds2MB')
            result = json.loads(b''.join(chunks))
            if not isinstance(result, dict):
                raise RuntimeError('CloudRift returned an invalid completion envelope')
            usage = result.get('usage', {})
            prompt, completion = usage.get('prompt_tokens'), usage.get('completion_tokens')
            if type(prompt) is int and type(completion) is int and prompt >= 0 and completion >= 0:
                charge = Decimal(prompt)*self.input_rate+Decimal(completion)*self.output_rate
                self.spent += charge-reserve
                self.prompt_tokens += prompt
                self.completion_tokens += completion
                self.usage_responses += 1
            if self.spent > self.cap:
                raise RuntimeError('CloudRift estimated usage exceeded cap; no further calls allowed')
            return result
        except HTTPError as error:
            message = None
            try:
                value = json.loads(error.read(4096)).get('error', {})
                value = value.get('message') if isinstance(value, dict) else value
                if isinstance(value, str):
                    message = re.sub(r'rift_[A-Za-z0-9_-]+', '[REDACTED]', value.replace(self._api_key, '[REDACTED]'))
                    message = ''.join(character for character in message if character.isprintable())[:512]
            except (ValueError, TypeError, AttributeError, OSError):
                pass
            raise CloudRiftHTTPError(error.code, message) from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise RuntimeError('CloudRift request failed; no retry or provider fallback attempted') from None

    def budget_status(self):
        return dict(estimated_usd=float(self.spent), estimated_usd_cap=float(self.cap),
                    input_usd_per_token=str(self.input_rate), output_usd_per_token=str(self.output_rate),
                    prompt_tokens=self.prompt_tokens, completion_tokens=self.completion_tokens,
                    calls=self.calls, responses_with_usage=self.usage_responses,
                    interpretation='Conservative API-advertised uncached token estimate, not a billing receipt; failed/unknown-usage calls retain their reserved estimate')


class CloudRiftOpsPolicy(OpsModelPolicy):
    def __init__(self, *, allow_cloudrift=False, transport, **options):
        if allow_cloudrift is not True:
            raise ValueError('CloudRift inference requires explicit authorization')
        super().__init__(transport=transport, **options)

    def validate_model(self, model):
        if model != MODEL:
            raise ValueError('Use only the explicitly authorized DeepSeek CloudRift model')
