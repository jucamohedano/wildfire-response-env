"""Bounded, credential-redacted evidence for rejected model tool responses."""
import math
import re


TEXT_BUDGET = 65536
SECRET_FIELDS = {'api_key', 'apikey', 'authorization', 'access_token', 'password', 'secret'}


def failure_diagnostics(response, error, stage, transport):
    secrets = []
    visited = set()
    while transport is not None and id(transport) not in visited:
        visited.add(id(transport))
        attributes = getattr(transport, '__dict__', {})
        key = attributes.get('_api_key')
        if isinstance(key, str) and key:
            secrets.append(key)
        transport = attributes.get('transport')
    remaining = TEXT_BUDGET
    redacted = False
    truncated = False

    def text(value):
        nonlocal remaining, redacted, truncated
        original = value
        for secret in secrets:
            value = value.replace(secret, '[REDACTED]')
        value = re.sub(r'\b(?:rift_|sk-)[A-Za-z0-9_-]{8,}|\bBearer\s+[^\s"\x27]+',
                       '[REDACTED]', value, flags=re.IGNORECASE)
        redacted |= value != original
        if len(value) > remaining:
            value = value[:remaining] + '[TRUNCATED]'
            remaining = 0
            truncated = True
        else:
            remaining -= len(value)
        return value

    def bounded(value, depth=0):
        nonlocal redacted, truncated
        if isinstance(value, str):
            return text(value)
        if value is None or type(value) in (bool, int):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else text(str(value))
        if depth >= 8:
            truncated = True
            return '[TRUNCATED: nesting limit]'
        if isinstance(value, list):
            truncated |= len(value) > 20
            return [bounded(item, depth + 1) for item in value[:20]]
        if isinstance(value, dict):
            truncated |= len(value) > 20
            result = {}
            for key, item in list(value.items())[:20]:
                label = text(str(key))
                if str(key).lower() in SECRET_FIELDS:
                    result[label] = '[REDACTED]'
                    redacted = True
                else:
                    result[label] = bounded(item, depth + 1)
            return result
        return {'value_type': type(value).__name__}

    def select(value, fields):
        if not isinstance(value, dict):
            return {'value_type': type(value).__name__, 'value': bounded(value)}
        return {field: bounded(value[field]) for field in fields if field in value}

    diagnostic = dict(version='wildfire-model-protocol-diagnostics-v1', stage=stage,
                      error_type=type(error).__name__, error_message=text(str(error)),
                      response_type=type(response).__name__)
    if isinstance(response, dict):
        choices = response.get('choices')
        diagnostic['choices_type'] = type(choices).__name__
        if isinstance(choices, list):
            diagnostic['choice_count'] = len(choices)
            truncated |= len(choices) > 4
            captured = []
            for choice in choices[:4]:
                entry = select(choice, ('index', 'finish_reason'))
                if isinstance(choice, dict) and 'message' in choice:
                    message = choice['message']
                    entry['message'] = select(message, ('role', 'content'))
                    if isinstance(message, dict) and 'tool_calls' in message:
                        calls = message['tool_calls']
                        entry['tool_calls_type'] = type(calls).__name__
                        if isinstance(calls, list):
                            entry['tool_call_count'] = len(calls)
                            truncated |= len(calls) > 4
                            captured_calls = []
                            for call in calls[:4]:
                                item = select(call, ('id', 'type'))
                                if isinstance(call, dict) and 'function' in call:
                                    item['function'] = select(call['function'], ('name', 'arguments'))
                                captured_calls.append(item)
                            entry['message']['tool_calls'] = captured_calls
                        else:
                            entry['message']['tool_calls'] = bounded(calls)
                captured.append(entry)
            diagnostic['choices'] = captured
    diagnostic.update(redacted=redacted, truncated=truncated,
                      text_character_budget=TEXT_BUDGET)
    return diagnostic
