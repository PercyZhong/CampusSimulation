import json

import pytest

from campus_sim.event_view import read_events


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
