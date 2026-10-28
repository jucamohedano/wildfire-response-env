import json

import pytest

from envs.wildfire_response.incident_day.events import EventBus


def advance(bus, end, views):
    delivered = []
    for minute in range(end + 1):
        delivered.extend(bus.tick(minute, views(minute) if callable(views) else views))
    return delivered


def test_delays_are_seeded_and_independent_per_source_and_kind():
    first = EventBus(42, ["north"])
    second = EventBus(42, ["north"])
    for index in range(20):
        second.emit("other", "update", {"index": index}, 0, channel="phone")
        second.request_status("north", "other", 0)
    first_id = first.emit("crew", "update", {}, 0, channel="radio")
    second_id = second.emit("crew", "update", {}, 0, channel="radio")
    assert first_id == second_id
    first_report = advance(first, 60, {})[0]
    second_report = next(record for record in advance(second, 60, {}) if record["id"] == second_id)
    assert first_report == second_report
    assert 1 <= first_report["received_at"] <= 5


@pytest.mark.parametrize("channel,urgent,minimum,maximum", [
    ("radio", False, 1, 5), ("phone", False, 1, 15),
    ("routine", False, 5, 60), ("phone", True, 1, 3),
])
def test_delay_ranges(channel, urgent, minimum, maximum):
    bus = EventBus(9, [])
    for index in range(30):
        bus.emit("crew", "update", {"index": index}, 0, channel=channel, urgent=urgent)
    records = advance(bus, 60, {})
    assert len(records) == 30
    assert all(minimum <= record["received_at"] <= maximum for record in records)


def test_status_reply_snapshots_only_answering_center_at_service_time():
    bus = EventBus(3, ["north", "south"])
    request_id = bus.request_status("north", "fire", 0)
    assert bus.requests[request_id]["status"] == "pending"
    assert bus.get_reports() == []

    def views(minute):
        return {"north": {"fire": {"value": minute, "observed_at": minute - 1}},
                "south": {"fire": {"secret": "south-only"}}}

    records = advance(bus, 30, views)
    assert len(records) == 1
    response = records[0]
    service_minute = response["payload"]["serviced_at"]
    assert 0 < service_minute < response["received_at"]
    assert response["payload"]["knowledge"] == {"value": service_minute, "observed_at": service_minute - 1}
    assert response["payload"]["staleness_minutes"] == 1
    assert response["actionable"] is True
    assert response["correlation_id"] == request_id
    assert bus.requests[request_id]["status"] == "answered"
    assert "south-only" not in json.dumps(response)
    assert "due" not in json.dumps(bus.reports)
    assert "due" not in json.dumps(bus.requests)


def test_unknown_unavailable_and_timeout():
    unknown = EventBus(4, ["north"])
    unknown_id = unknown.request_status("north", "missing", 0)
    response = advance(unknown, 20, {"north": {}})[0]
    assert unknown.requests[unknown_id]["status"] == "answered"
    assert response["payload"]["known"] is False
    assert response["payload"]["staleness_minutes"] is None

    unavailable = EventBus(4, ["north"])
    unavailable_id = unavailable.request_status("north", "fire", 0)
    response = advance(unavailable, 20, {})[0]
    assert unavailable.requests[unavailable_id]["status"] == "unavailable"
    assert response["payload"]["available"] is False
    assert response["actionable"] is True

    timeout = EventBus(4, ["north"])
    timeout_id = timeout.request_status("north", "fire", 0)
    assert timeout.tick(60, {"north": {"fire": "current"}}) == []
    assert timeout.requests[timeout_id]["status"] == "timeout"
    assert timeout.tick(61, {"north": {"fire": "current"}}) == []


def test_coalescing_keeps_ledger_and_routine_telemetry_is_quiet():
    bus = EventBus(1, [])
    ids = [bus.emit("crew", "update", {"text": "smoke"}, 0, delay=1),
           bus.emit("crew", "update", {"text": "smoke"}, 1, delay=1),
           bus.emit("crew", "update", {"text": "flames"}, 2, delay=1),
           bus.emit("sensor", "telemetry", {"value": 1}, 0, delay=1),
           bus.emit("sensor", "update", {"value": 2}, 0, channel="routine", delay=1)]
    advance(bus, 5, {})
    records = {record["id"]: record for record in bus.reports}
    assert [records[identifier]["actionable"] for identifier in ids] == [True, False, True, False, False]
    assert len(bus.get_reports(after_cursor=1, limit=2)) == 2
    assert bus.get_reports(ids=[ids[0]]) == [records[ids[0]]]
    bus.get_reports()[0]["payload"].clear()
    assert bus.reports[0]["payload"]


def test_connectivity_and_role_condition_independent_reaction_and_delivery():
    normal = EventBus(123, [])
    poor = EventBus(123, [])
    responder = EventBus(123, [])
    for index in range(30):
        normal.emit("resident", "call", {"index": index}, 0, role="resident", channel="phone")
        poor.emit("resident", "call", {"index": index}, 0, role="resident", channel="phone", connectivity="poor")
        responder.emit("resident", "call", {"index": index}, 0, role="responder", channel="phone")
    normal_records = {record["id"]: record for record in advance(normal, 60, {})}
    poor_records = {record["id"]: record for record in advance(poor, 60, {})}
    responder_records = {record["id"]: record for record in advance(responder, 60, {})}
    assert normal_records.keys() == poor_records.keys() == responder_records.keys()
    assert all(poor_records[key]["reported_at"] >= normal_records[key]["reported_at"] for key in normal_records)
    assert all(poor_records[key]["received_at"] >= normal_records[key]["received_at"] for key in normal_records)
    assert any(poor_records[key]["received_at"] > normal_records[key]["received_at"] for key in normal_records)
    assert any(responder_records[key]["reported_at"] != normal_records[key]["reported_at"] for key in normal_records)
    reactions = {record["reported_at"] - record["observed_at"] for record in normal_records.values()}
    deliveries = {record["received_at"] - record["reported_at"] for record in normal_records.values()}
    assert len(reactions) > 1 and len(deliveries) > 1
    assert all(1 <= record["received_at"] <= 15 for record in poor_records.values())


def test_repeated_resident_updates_ignore_nested_observation_timestamps():
    bus = EventBus(8, [])
    for minute, status in ((0, "unwarned"), (1, "unwarned"), (2, "sheltering")):
        bus.emit("population-0", "resident_update", {"group": {"status": status,
            "observed_at": minute, "nested": [{"received_at": minute, "value": 1}]}},
            minute, role="resident", channel="phone", delay=1)
    records = advance(bus, 4, {})
    assert [record["actionable"] for record in records] == [True, False, True]
    assert [record["payload"]["group"]["observed_at"] for record in records] == [0, 1, 2]
    assert all(record["observed_at"] <= record["reported_at"] <= record["received_at"] for record in records)


def test_seeded_reaction_and_delivery_survive_checkpoint():
    bus = EventBus(42, [])
    identifier = bus.emit("resident", "call", {"text": "smoke"}, 0,
                          role="resident", channel="phone", connectivity="degraded")
    restored = EventBus.restore(json.loads(json.dumps(bus.checkpoint())))
    original = advance(bus, 60, {})
    replayed = advance(restored, 60, {})
    assert original == replayed
    assert original[0]["id"] == identifier
    assert original[0]["observed_at"] <= original[0]["reported_at"] < original[0]["received_at"]


def test_checkpoint_round_trip_preserves_pending_and_deterministic_continuation():
    bus = EventBus("seed", ["north"])
    bus.emit("crew", "update", {"text": "first"}, 0, delay=1)
    request_id = bus.request_status("north", "fire", 0)
    advance(bus, 2, {"north": {"fire": {"observed_at": 0}}})
    snapshot = json.loads(json.dumps(bus.checkpoint()))
    restored = EventBus.restore(snapshot)
    assert restored.emit("crew", "update", {"text": "first"}, 2, delay=1) == bus.emit("crew", "update", {"text": "first"}, 2, delay=1)
    for minute in range(3, 25):
        views = {"north": {"fire": {"observed_at": 0, "value": minute}}}
        assert restored.tick(minute, views) == bus.tick(minute, views)
    assert restored.requests[request_id]["status"] == "answered"
    assert restored.checkpoint() == bus.checkpoint()


@pytest.mark.parametrize("call", [
    lambda bus: bus.emit("", "update", {}, 0),
    lambda bus: bus.emit("crew", "update", {}, -1),
    lambda bus: bus.emit("crew", "update", {}, 1441),
    lambda bus: bus.emit("crew", "update", {}, 0, delay=61),
    lambda bus: bus.emit("crew", "update", {}, 0, channel="unknown"),
    lambda bus: bus.emit("crew", "update", {}, 0, channel=[]),
    lambda bus: bus.emit("crew", "update", {}, 0, connectivity="unknown"),
    lambda bus: bus.emit("crew", "update", {}, 0, connectivity=[]),
    lambda bus: bus.emit("crew", "update", {"bad": float("nan")}, 0),
    lambda bus: bus.request_status("missing", "fire", 0),
    lambda bus: bus.tick(0, None),
    lambda bus: bus.get_reports(after_cursor=-1),
])
def test_invalid_inputs(call):
    with pytest.raises(ValueError):
        call(EventBus(1, ["north"]))
