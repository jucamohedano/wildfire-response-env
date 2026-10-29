"""Deterministic, delayed exercise communications for an incident day.

Delay ranges are simulation assumptions, not operational doctrine. Centre views
map centre IDs to subject-keyed dictionaries; a missing centre or a view with
``available: false`` is unavailable. A missing subject is explicitly unknown.
An observation can supply ``observed_at`` to quantify its age at service time.
Communication connectivity is ``normal``, ``degraded``, or ``poor``. Reaction
and transmission are sampled separately; degraded and poor connectivity add
bounded synthetic delay. An explicit ``delay`` overrides the total and gives
zero reaction delay. These parameters are exercise assumptions only.
"""

import copy
import hashlib
import json


EPISODE_MINUTES = 1440
REQUEST_TIMEOUT_MINUTES = 60
_CHANNELS = {"radio", "phone", "routine"}
EXERCISE_DELAY_ASSUMPTIONS = {
    "urgent": {"reaction_max": 1, "delivery_min": 1, "delivery_max": 2},
    "radio": {"reaction_max": 2, "delivery_min": 1, "delivery_max": 3},
    "phone": {"reaction_max": 3, "delivery_min": 1, "delivery_max": 12},
    "routine": {"reaction_max": 4, "delivery_min": 5, "delivery_max": 56},
    "connectivity_bias": {"normal": 0, "degraded": 1, "poor": 2},
    "resident_reaction_bias": 1,
}
_TIMESTAMP_FIELDS = frozenset(("observed_at", "received_at", "reported_at", "updated_at", "timestamp",
                               "observed_minute", "received_minute", "reported_minute"))


def _minute(value, name="minute"):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= EPISODE_MINUTES:
        raise ValueError(f"{name} must be an integer from 0 to {EPISODE_MINUTES}")


def _string(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


def _json_copy(value):
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as error:
        raise ValueError("values must be JSON-safe") from error


def _without_timestamps(value):
    if isinstance(value, dict):
        return {key: _without_timestamps(item) for key, item in value.items() if key not in _TIMESTAMP_FIELDS}
    if isinstance(value, list):
        return [_without_timestamps(item) for item in value]
    return value


class EventBus:
    """Queue messages and centre status requests without advancing the clock.

    ``tick`` services work at the supplied minute, using only the centre's view
    supplied at that call. It returns newly delivered records; ``reports`` is the
    delivered ledger. Request timeout is 60 minutes, measured from submission.
    A request remains pending until its response arrives or it times out.
    """

    def __init__(self, seed, centers: list[str]):
        if isinstance(seed, bool) or not isinstance(seed, (int, str)):
            raise ValueError("seed must be an integer or string")
        if not isinstance(centers, list):
            raise ValueError("centers must be a list")
        for center in centers:
            _string(center, "center")
        if len(set(centers)) != len(centers):
            raise ValueError("centers must be unique")
        self.seed = seed
        self.centers = centers[:]
        self.reports = []
        self.requests = {}
        self._queue = []
        self._counters = {}
        self._last_updates = {}
        self._last_tick = -1

    def _next(self, source, kind):
        key = json.dumps([source, kind], separators=(",", ":"), ensure_ascii=True)
        count = self._counters.get(key, 0) + 1
        self._counters[key] = count
        digest = hashlib.sha256(json.dumps([self.seed, source, kind, count], separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
        return count, digest

    def _delay(self, source, kind, count, stage, low, high):
        data = json.dumps([self.seed, source, kind, count, stage], separators=(",", ":"), ensure_ascii=True)
        return low + int.from_bytes(hashlib.sha256(data.encode()).digest(), "big") % (high - low + 1)

    def emit(self, source, kind, payload, minute, *, role="responder", channel="radio",
             location=None, urgent=False, actionable=True, correlation_id=None, delay=None,
             connectivity="normal"):
        _string(source, "source")
        _string(kind, "kind")
        _string(role, "role")
        _minute(minute)
        if minute < self._last_tick:
            raise ValueError("minute precedes the last tick")
        if not isinstance(channel, str) or channel not in _CHANNELS:
            raise ValueError("channel must be radio, phone, or routine")
        if not isinstance(connectivity, str) or connectivity not in EXERCISE_DELAY_ASSUMPTIONS["connectivity_bias"]:
            raise ValueError("connectivity must be normal, degraded, or poor")
        if not isinstance(payload, dict):
            raise ValueError("payload must be a dictionary")
        if not isinstance(urgent, bool) or not isinstance(actionable, bool):
            raise ValueError("urgent and actionable must be booleans")
        if correlation_id is not None:
            _string(correlation_id, "correlation_id")
        if delay is not None and (isinstance(delay, bool) or not isinstance(delay, int) or not 0 <= delay <= 60):
            raise ValueError("delay must be an integer from 0 to 60")
        payload = _json_copy(payload)
        location = _json_copy(location)
        count, digest = self._next(source, kind)
        identifier = "event-" + digest
        assumptions = EXERCISE_DELAY_ASSUMPTIONS["urgent" if urgent else channel]
        conditions = json.dumps([role, channel, urgent], separators=(",", ":"))
        connectivity_bias = EXERCISE_DELAY_ASSUMPTIONS["connectivity_bias"][connectivity]
        if delay is None:
            reaction_max = assumptions["reaction_max"]
            reaction_delay = self._delay(source, kind, count, "reaction:" + conditions, 0, reaction_max)
            reaction_delay = min(reaction_max, reaction_delay + connectivity_bias
                                 + (EXERCISE_DELAY_ASSUMPTIONS["resident_reaction_bias"] if role == "resident" else 0))
            low, high = assumptions["delivery_min"], assumptions["delivery_max"]
            delivery_delay = self._delay(source, kind, count, "delivery:" + conditions, low, high)
            delivery_delay = min(high, delivery_delay + connectivity_bias * max(1, (high - low) // 4))
        else:
            reaction_delay, delivery_delay = 0, delay
        record = {"id": identifier, "source": source, "role": role, "channel": channel,
                  "location": location, "kind": kind, "payload": payload,
                  "text": str(payload.get("text", "")), "observed_at": minute,
                  "reported_at": minute + reaction_delay,
                  "urgent": urgent, "actionable": actionable,
                  "correlation_id": correlation_id}
        self._queue.append({"stage": "message", "due": minute + reaction_delay + delivery_delay, "record": record})
        return identifier

    def request_status(self, center_id, subject, minute):
        _string(center_id, "center_id")
        _string(subject, "subject")
        _minute(minute)
        if minute < self._last_tick:
            raise ValueError("minute precedes the last tick")
        if center_id not in self.centers:
            raise ValueError("unknown center")
        count, digest = self._next(center_id, "status_request")
        identifier = "request-" + digest
        self.requests[identifier] = {"id": identifier, "center_id": center_id, "subject": subject,
                                     "requested_at": minute, "status": "pending"}
        due = minute + self._delay(center_id, "status_request", count, "request", 1, 5)
        self._queue.append({"stage": "request", "due": due, "request_id": identifier, "count": count})
        return identifier

    def tick(self, minute, center_views: dict):
        _minute(minute)
        if minute < self._last_tick:
            raise ValueError("ticks must be nondecreasing")
        if not isinstance(center_views, dict):
            raise ValueError("center_views must be a dictionary")
        self._last_tick = minute
        delivered = []
        due = sorted((item for item in self._queue if item["due"] <= minute), key=lambda item: item["due"])
        self._queue = [item for item in self._queue if item["due"] > minute]
        for item in due:
            if item["stage"] == "message":
                record = item["record"]
                key = json.dumps([record["source"], record["kind"]], separators=(",", ":"))
                signature = json.dumps([_without_timestamps(record["payload"]), _without_timestamps(record["location"]), record["role"],
                                        record["channel"], record["urgent"]], sort_keys=True, separators=(",", ":"))
                if self._last_updates.get(key) == signature or record["channel"] == "routine" or record["kind"] == "telemetry":
                    record["actionable"] = False
                self._last_updates[key] = signature
                self._deliver(record, minute, delivered)
                continue
            request = self.requests[item["request_id"]]
            if request["status"] != "pending":
                continue
            if minute - request["requested_at"] >= REQUEST_TIMEOUT_MINUTES:
                request["status"] = "timeout"
                continue
            center = request["center_id"]
            count = item["count"]
            if item["stage"] == "request":
                due_at = minute + self._delay(center, "status_request", count, "service", 1, 5)
                self._queue.append({**item, "stage": "service", "due": due_at})
            elif item["stage"] == "service":
                view = center_views.get(center)
                available = isinstance(view, dict) and view.get("available", True) is not False
                known = available and request["subject"] in view
                knowledge = _json_copy(view[request["subject"]]) if known else None
                observed_at = knowledge.get("observed_at") if isinstance(knowledge, dict) else None
                if isinstance(observed_at, bool) or not isinstance(observed_at, int) or not 0 <= observed_at <= minute:
                    observed_at = None
                payload = {"subject": request["subject"], "available": available, "known": known,
                           "knowledge": knowledge, "observed_at": observed_at,
                           "staleness_minutes": minute - observed_at if observed_at is not None else None,
                           "serviced_at": minute}
                record = {"id": "response-" + request["id"][len("request-"):], "source": center,
                          "role": "center", "channel": "radio", "location": None,
                          "kind": "status_response", "payload": payload,
                          "text": "Status response" if known else ("Status unknown" if available else "Center unavailable"),
                          "observed_at": minute, "urgent": False, "actionable": True,
                          "correlation_id": request["id"]}
                due_at = minute + self._delay(center, "status_request", count, "response", 1, 5)
                self._queue.append({"stage": "response", "due": due_at, "request_id": request["id"],
                                    "count": count, "record": record})
            else:
                request["status"] = "answered" if item["record"]["payload"]["available"] else "unavailable"
                request["response_id"] = item["record"]["id"]
                self._deliver(item["record"], minute, delivered)
        for request in self.requests.values():
            if request["status"] == "pending" and minute - request["requested_at"] >= REQUEST_TIMEOUT_MINUTES:
                request["status"] = "timeout"
        return copy.deepcopy(delivered)

    def _deliver(self, record, minute, delivered):
        record["received_at"] = minute
        self.reports.append(record)
        delivered.append(record)

    def get_reports(self, ids=None, after_cursor=0, limit=20):
        if ids is not None and (not isinstance(ids, list) or any(not isinstance(item, str) for item in ids)):
            raise ValueError("ids must be a list of strings")
        if isinstance(after_cursor, bool) or not isinstance(after_cursor, int) or after_cursor < 0:
            raise ValueError("after_cursor must be a nonnegative integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("limit must be a nonnegative integer")
        selected = self.reports[after_cursor:]
        if ids is not None:
            selected = [record for record in selected if record["id"] in ids]
        return copy.deepcopy(selected[:limit])

    def checkpoint(self):
        return _json_copy({"seed": self.seed, "centers": self.centers, "reports": self.reports,
                           "requests": self.requests, "queue": self._queue, "counters": self._counters,
                           "last_updates": self._last_updates, "last_tick": self._last_tick})

    @classmethod
    def restore(cls, snapshot):
        state = _json_copy(snapshot)
        if not isinstance(state, dict):
            raise ValueError("snapshot must be a dictionary")
        try:
            bus = cls(state["seed"], state["centers"])
            bus.reports = state["reports"]
            bus.requests = state["requests"]
            bus._queue = state["queue"]
            bus._counters = state["counters"]
            bus._last_updates = state["last_updates"]
            bus._last_tick = state["last_tick"]
        except (KeyError, TypeError) as error:
            raise ValueError("invalid snapshot") from error
        return bus
