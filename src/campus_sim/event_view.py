"""Read bounded, exported action facts for the local workbench."""

import json
from pathlib import Path

MAX_PAGE_SIZE = 100


def read_events(run_dir, *, day=None, action=None, offset=0, limit=50):
    if type(offset) is not int or offset < 0:
        raise ValueError("offset must be a nonnegative integer")
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ValueError("limit must be between 1 and 100")
    if day is not None and (type(day) is not int or day < 1):
        raise ValueError("day must be a positive integer")
    if action is not None and action not in {
        "attend", "rest", "peer", "request", "accept_support", "outreach_information",
        "staff_shift"
    }:
        raise ValueError("unknown action")

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
            if offset <= total < offset + limit:
                page.append(event)
            total += 1
    return {"total": total, "offset": offset, "limit": limit, "events": page}
