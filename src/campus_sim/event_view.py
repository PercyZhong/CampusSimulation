"""Read bounded, exported action facts for the local workbench."""

import json
from pathlib import Path

MAX_PAGE_SIZE = 100


def _validate_filters(day, action, actor, offset, limit):
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a nonnegative integer")
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError("limit must be between 1 and 100")
    if day is not None and (type(day) is not int or day < 1):
        raise ValueError("day must be a positive integer")
    if action is not None and action not in {
        "attend", "rest", "peer", "request", "accept_support", "outreach_information",
        "staff_shift", "adviser_review", "model_decision", "staff_follow_up", "course_absence",
        "peer_invitation", "peer_response", "peer_callback", "study_group",
        "intervention_exposure", "scenario_trigger", "teacher_review",
        "academic_check_in", "committee_check_in", "peer_support_offer",
        "professional_referral_offer", "referral_information"
    }:
        raise ValueError("unknown action")
    if actor is not None and (not isinstance(actor, str) or not actor or
                              not (actor[0] in {"s", "c", "t"} and actor[1:].isdigit())):
        raise ValueError("invalid actor")

def read_events(run_dir, *, day=None, action=None, actor=None, offset=0, limit=50):
    _validate_filters(day, action, actor, offset, limit)
    path = Path(run_dir) / "events.jsonl"
    total = 0
    page = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            event = json.loads(line)
            if day is not None and event["day"] != day:
                continue
            if action is not None and event["action"] != action:
                continue
            if (actor is not None and event["actor"] != actor
                    and not (actor.startswith("c")
                             and event.get("detail", {}).get("staff_id") == actor)
                    and not (actor.startswith("t")
                             and event.get("detail", {}).get("teacher_id") == actor)
                    and event.get("detail", {}).get("committee_id") != actor):
                continue
            if offset <= total < offset + limit:
                page.append(event)
            total += 1
    return {"total": total, "offset": offset, "limit": limit, "events": page}


def read_timeline(run_dir, *, day=None, action=None, actor=None, offset=0, limit=50):
    """Group actions by day and subject before paginating whole daily paths."""
    _validate_filters(day, action, actor, offset, limit)
    grouped = {}
    path = Path(run_dir) / "events.jsonl"
    with path.open(encoding="utf-8") as stream:
        for position, line in enumerate(stream):
            event = json.loads(line)
            if event["action"] == "student_state":
                continue
            if day is not None and event["day"] != day:
                continue
            if action is not None and event["action"] != action:
                continue
            subjects = set()
            source = event["actor"]
            if (isinstance(source, str) and len(source) > 1
                    and source[0] in {"c", "t", "s"} and source[1:].isdigit()):
                subjects.add(source)
            detail = event.get("detail") or {}
            staff = detail.get("staff_id") if isinstance(detail, dict) else None
            if (isinstance(staff, str) and len(staff) > 1
                    and staff[0] == "c" and staff[1:].isdigit()):
                subjects.add(staff)
            for key, prefix in (("teacher_id", "t"), ("committee_id", "s")):
                related = detail.get(key) if isinstance(detail, dict) else None
                if (isinstance(related, str) and len(related) > 1
                        and related[0] == prefix and related[1:].isdigit()):
                    subjects.add(related)
            for subject in subjects:
                if actor is None or actor == subject:
                    grouped.setdefault((event["day"], subject), []).append(
                        (event.get("ordinal", position), position, event))

    def order(key):
        day_number, subject = key
        return day_number, {"c": 0, "t": 1, "s": 2}[subject[0]], int(subject[1:])

    keys = sorted(grouped, key=order)
    rows = [{"day": key[0], "actor": key[1],
             "events": [event for _, _, event in sorted(grouped[key])]}
            for key in keys[offset:offset + limit]]
    return {"total": len(keys), "offset": offset, "limit": limit, "rows": rows}
