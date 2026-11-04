"""Strict single-call decoding and monotonic policy-deadline checks."""
import json
import math
import re


def _pairs(values):
    result={}
    for key,value in values:
        if key in result: raise ValueError('Duplicate tool argument key')
        result[key]=value
    return result


def parse_call(response):
    match=re.fullmatch(r'\s*<tool_call>\s*(.*?)\s*</tool_call>\s*',response,re.S)
    if match is None or '<tool_call>' in match.group(1) or '</tool_call>' in match.group(1):
        raise ValueError('Expected exactly one complete tool_call envelope')
    def invalid_constant(value):raise ValueError('Non-finite tool argument')
    try:call=json.loads(match.group(1),object_pairs_hook=_pairs,parse_constant=invalid_constant)
    except (TypeError,RecursionError) as error:raise ValueError('Invalid tool JSON') from error
    def finite(value):
        if isinstance(value,float) and not math.isfinite(value):raise ValueError('Non-finite tool argument')
        if isinstance(value,dict):
            for item in value.values():finite(item)
        elif isinstance(value,list):
            for item in value:finite(item)
    try:finite(call)
    except RecursionError as error:raise ValueError('Tool argument nesting exceeds parser budget') from error
    if (type(call) is not dict or set(call)!={'name','arguments'}
            or type(call['name']) is not str or type(call['arguments']) is not dict):
        raise ValueError('Expected named tool and argument object')
    return call


def within_deadline(now,deadline):
    return now < deadline


def execute_with_deadline(callback,deadline,now=None):
    import time
    now=now or time.monotonic
    if not within_deadline(now(),deadline):return False,None,False
    value=callback()
    return True,value,within_deadline(now(),deadline)
