"""Trim old tool history without discarding the latest tool-call/result pair."""
MARKER='Continue from the delivered tool results below. Read-only tools do not advance simulation time.'


def trim_once(messages):
    if len(messages)<4:
        raise ValueError('Initial situation exceeds context budget')
    if messages[1].get('content')!=MARKER:
        return [messages[0],{'role':'user','content':MARKER},*messages[2:]]
    if len(messages)>4:
        return [*messages[:2],*messages[4:]]
    raise ValueError('Latest tool response exceeds context budget')
