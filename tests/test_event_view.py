import json

import pytest

from campus_sim.event_view import read_events, read_timeline


def test_filtered_event_page_preserves_export_order(tmp_path):
    events = [
        {"day": 1, "actor": "s000", "action": "request", "status": "completed"},
        {"day": 2, "actor": "s001", "action": "peer", "status": "completed"},
        {"day": 2, "actor": "s002", "action": "request", "status": "rejected"},
        {"day": 2, "actor": "s003", "action": "request", "status": "completed"},
    ]
    (tmp_path / "events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )
    page = read_events(tmp_path, day=2, action="request", offset=1, limit=1)
    assert page["total"] == 2
    assert page["events"] == [events[3]]
    with pytest.raises(ValueError):
        read_events(tmp_path, limit=101)


def test_staff_shift_is_a_filterable_action(tmp_path):
    event = {"day": 1, "actor": "c000", "action": "staff_shift", "status": "completed"}
    (tmp_path / "events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
    assert read_events(tmp_path, action="staff_shift")["events"] == [event]


def test_model_decision_is_a_filterable_action(tmp_path):
    event = {"day": 1, "actor": "s000", "action": "model_decision", "status": "completed"}
    (tmp_path / "events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
    assert read_events(tmp_path, action="model_decision")["events"] == [event]


def test_staff_follow_up_is_a_filterable_action(tmp_path):
    event = {"day": 4, "actor": "s000", "action": "staff_follow_up", "status": "completed"}
    (tmp_path / "events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
    assert read_events(tmp_path, action="staff_follow_up")["events"] == [event]


def test_course_absence_is_a_filterable_action(tmp_path):
    event = {"day": 2, "actor": "s000", "action": "course_absence", "status": "completed"}
    (tmp_path / "events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
    assert read_events(tmp_path, action="course_absence")["events"] == [event]


def test_actor_filter_includes_staff_service_events(tmp_path):
    events = [
        {"day": 1, "actor": "c000", "action": "staff_shift", "detail": {}},
        {"day": 1, "actor": "s000", "action": "accept_support",
         "detail": {"staff_id": "c000"}},
        {"day": 1, "actor": "s001", "action": "staff_follow_up",
         "detail": {"staff_id": "c001"}},
    ]
    (tmp_path / "events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )
    assert read_events(tmp_path, actor="c000")["events"] == events[:2]
    assert read_events(tmp_path, actor="s000")["events"] == events[1:2]
    with pytest.raises(ValueError, match="actor"):
        read_events(tmp_path, actor="../../")


def test_timeline_groups_daily_actions_in_numeric_actor_and_event_order(tmp_path):
    events = [
        {"day": 2, "ordinal": 8, "actor": "s010", "action": "rest", "status": "completed"},
        {"day": 1, "ordinal": 6, "actor": "s002", "action": "rest", "status": "completed"},
        {"day": 1, "ordinal": 5, "actor": "s002", "action": "attend", "status": "completed"},
        {"day": 1, "ordinal": 4, "actor": "s010", "action": "request", "status": "rejected"},
        {"day": 1, "ordinal": 3, "actor": "c010", "action": "staff_shift", "status": "completed"},
        {"day": 1, "ordinal": 2, "actor": "c002", "action": "staff_shift", "status": "completed"},
        {"day": 1, "ordinal": 7, "actor": "s002", "action": "student_state", "status": "completed"},
    ]
    (tmp_path / "events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )
    page = read_timeline(tmp_path)
    assert page["total"] == 5
    assert [(row["day"], row["actor"]) for row in page["rows"]] == [
        (1, "c002"), (1, "c010"), (1, "s002"), (1, "s010"), (2, "s010")
    ]
    assert [event["action"] for event in page["rows"][2]["events"]] == ["attend", "rest"]
    assert read_timeline(tmp_path, offset=2, limit=1)["rows"] == page["rows"][2:3]


def test_timeline_attributes_service_to_staff_and_filters_whole_daily_rows(tmp_path):
    events = [
        {"day": 1, "ordinal": 1, "actor": "c000", "action": "staff_shift",
         "status": "completed", "detail": {}},
        {"day": 1, "ordinal": 2, "actor": "s000", "action": "request",
         "status": "completed", "detail": {}},
        {"day": 1, "ordinal": 3, "actor": "s000", "action": "accept_support",
         "status": "completed", "detail": {"staff_id": "c000"}},
        {"day": 2, "ordinal": 4, "actor": "s000", "action": "rest",
         "status": "completed", "detail": {}},
    ]
    (tmp_path / "events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )
    rows = read_timeline(tmp_path, day=1)["rows"]
    assert [(row["day"], row["actor"]) for row in rows] == [(1, "c000"), (1, "s000")]
    assert [event["action"] for event in rows[0]["events"]] == [
        "staff_shift", "accept_support"
    ]
    assert [event["action"] for event in rows[1]["events"]] == [
        "request", "accept_support"
    ]
    assert read_timeline(tmp_path, actor="c000")["rows"] == rows[:1]
    assert read_timeline(tmp_path, action="accept_support")["total"] == 2
    with pytest.raises(ValueError, match="limit"):
        read_timeline(tmp_path, limit=101)
