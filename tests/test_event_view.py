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
