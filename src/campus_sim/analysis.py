"""Export only authoritative facts through Society0's read-only Observation."""

import csv
import itertools
import json
from collections import Counter, defaultdict
from pathlib import Path

from society0.kernel.observation import Observation

SCHEMA_VERSION = 1
ACTION_NAMES = ("attend", "rest", "peer", "request", "accept_support")


def interaction_records(events):
    """Project observed actions into one auditable interaction shape."""
    records = []
    for event in events:
        action, detail = event["action"], event["detail"]
        common = {"day": event["day"], "ordinal": event["ordinal"],
                  "source_action": action, "actor": event["actor"],
                  "status": event["status"]}
        if action == "peer" and event["status"] == "completed":
            records.append({**common, "kind": "peer_dialogue",
                            "participants": [event["actor"], detail["partner"]],
                            "venue": None, "response": "dialogue_recorded",
                            "outcome": "completed",
                            "partner_response": detail.get("partner_response", "not_recorded"),
                            "response_basis": detail.get("response_basis", "not_recorded")})
        elif action == "peer" and event["status"] == "rejected":
            records.append({**common, "kind": "peer_dialogue",
                            "participants": [event["actor"]], "venue": None,
                            "response": "unavailable", "outcome": event.get("reason")})
        elif action == "peer_invitation" and event["status"] == "completed":
            records.append({**common, "kind": "peer_invitation",
                            "participants": [event["actor"], detail["partner"]],
                            "venue": None, "response": detail.get("response"),
                            "outcome": "invited"})
        elif action == "peer_response":
            records.append({**common, "kind": "peer_response",
                            "participants": [detail.get("inviter"), event["actor"]],
                            "venue": None, "response": event["status"],
                            "outcome": "responded", "delayed": detail.get("delayed", False)})
        elif action == "peer_callback" and event["status"] == "completed":
            records.append({**common, "kind": "peer_callback",
                            "participants": [event["actor"], detail["inviter"]],
                            "venue": None, "response": "accepted",
                            "outcome": "callback", "source_day": detail["source_day"]})
        elif action == "attend" and event["status"] == "completed":
            context = detail.get("context") or {}
            records.append({**common, "kind": "class_attendance",
                            "participants": ([event["actor"], detail["classmate"]]
                                             if detail.get("classmate") else [event["actor"]]),
                            "venue": context.get("venue"),
                            "response": "not_recorded", "outcome": "attended"})
        elif action == "study_group" and event["status"] == "completed":
            records.append({**common, "kind": "study_group_copresence",
                            "participants": detail["participants"],
                            "venue": detail["context"].get("venue"),
                            "response": "not_recorded", "outcome": "copresent"})
        elif action == "club_copresence" and event["status"] == "completed":
            records.append({**common, "kind": "club_copresence",
                            "participants": detail["participants"],
                            "venue": detail["context"].get("venue"),
                            "response": "not_recorded", "outcome": "copresent"})
        elif (action == "rest" and event["status"] == "completed"
              and detail.get("trigger") == "shared_residence_presence_v1"):
            records.append({**common, "kind": "residence_presence",
                            "participants": [event["actor"], *detail["roommates"]],
                            "venue": detail["context"]["venue"],
                            "response": "not_recorded", "outcome": "copresent"})
        elif action == "accept_support" and event["status"] == "completed":
            records.append({**common, "kind": "service_contact",
                            "participants": [event["actor"],
                                             detail.get("staff_id") or "service:main"],
                            "venue": None, "response": "service_delivered",
                            "outcome": "completed"})
        elif action == "staff_follow_up" and event["status"] == "completed":
            records.append({**common, "kind": "service_follow_up",
                            "participants": [detail.get("staff_id") or "service:main",
                                             event["actor"]],
                            "venue": None, "response": "not_recorded",
                            "outcome": "completed"})
    return records


def selected_actions(events):
    """Use LLM decisions when present, otherwise student action attempts."""
    decisions = [event for event in events if event["action"] == "model_decision"
                 and event["status"] == "completed"]
    source = decisions if decisions else [event for event in events
                                         if event["action"] in ACTION_NAMES
                                         and event["status"] in {"completed", "rejected"}]
    return [(event["actor"], event["day"], event["ordinal"], action)
            for event in source
            if (action := event["detail"].get("action", event["action"])) in ACTION_NAMES]


def trajectory_metrics(events):
    """Count each student's selected-action path and completed peer relationships."""
    by_actor = {}
    for actor, day, ordinal, action in selected_actions(events):
        by_actor.setdefault(actor, []).append((day, ordinal, action))
    transitions = Counter()
    longest_streak = 0
    for items in by_actor.values():
        path = [item[2] for item in sorted(items)]
        for left, right in itertools.pairwise(path):
            transitions[left, right] += 1
        for _, group in itertools.groupby(path):
            longest_streak = max(longest_streak, sum(1 for _ in group))
    peer_pairs = Counter(tuple(sorted((event["actor"], event["detail"]["partner"])))
                         for event in events if event["action"] == "peer"
                         and event["status"] == "completed")
    repeat_contacts = sum(count - 1 for count in peer_pairs.values())
    return {"trajectory_action_transitions": sum(
                count for (left, right), count in transitions.items() if left != right),
            "trajectory_transition_matrix": {
                left: {right: transitions[left, right] for right in ACTION_NAMES}
                for left in ACTION_NAMES},
            "trajectory_longest_same_action_streak": longest_streak,
            "trajectory_observed_students": len(by_actor),
            "peer_relationships_unique": len(peer_pairs),
            "peer_repeat_contacts": repeat_contacts,
            "peer_repeat_contact_rate": (repeat_contacts / sum(peer_pairs.values())
                                         if peer_pairs else None)}


def trajectory_assessment(events, students, complete_step, window_days=7):
    """Build a compact, auditable trajectory assessment for review and plotting."""
    profiles = trajectory_profiles(events, students, complete_step, window_days)
    selected = selected_actions(events)
    by_student = {student["id"]: [] for student in students}
    daily = defaultdict(Counter)
    for actor, day, ordinal, action in selected:
        by_student.setdefault(actor, []).append({"day": day, "ordinal": ordinal,
                                                  "action": action})
        daily[day][action] += 1
    for path in by_student.values():
        path.sort(key=lambda item: (item["day"], item["ordinal"]))
    action_names = list(ACTION_NAMES)
    daily_rows = [{"day": day, **{action: counts.get(action, 0) for action in action_names}}
                  for day, counts in sorted(daily.items())]
    profile_rows = profiles["by_student"]
    for student_id, row in profile_rows.items():
        row["action_path"] = by_student.get(student_id, [])
        row["action_days"] = len({item["day"] for item in row["action_path"]})
        row["action_counts"] = dict(Counter(item["action"] for item in row["action_path"]))
    return {"window_days": profiles["window_days"], "window_start": profiles["window_start"],
            "window_end": profiles["window_end"], "complete_step": complete_step,
            "action_names": action_names, "daily_action_counts": daily_rows,
            "trajectory_metrics": trajectory_metrics(events),
            "trajectory_type_counts": profiles["trajectory_type_counts"],
            "isolation_status_counts": profiles["isolation_status_counts"],
            "eligible_students": profiles["eligible_students"], "students": profile_rows}


def trajectory_profiles(events, students, complete_step, window_days=7):
    """Classify observed recent paths without inventing unrecorded contact."""
    start = max(1, complete_step - window_days + 1)
    window = [event for event in events if start <= event["day"] <= complete_step]
    social_kinds = {"peer_dialogue", "class_attendance", "study_group_copresence",
                    "residence_presence", "club_copresence"}
    contacts = defaultdict(set)
    for record in interaction_records(window):
        if record["kind"] not in social_kinds or record["status"] != "completed":
            continue
        if len(set(record["participants"])) < 2:
            continue
        for actor in record["participants"]:
            contacts[actor].add(record["day"])
    snapshots = defaultdict(set)
    scheduled = defaultdict(set)
    missed = defaultdict(set)
    invitations = defaultdict(set)
    invitation_opportunities = defaultdict(set)
    requests = defaultdict(set)
    for event in window:
        actor, day, action = event["actor"], event["day"], event["action"]
        if action == "student_state" and event["status"] == "completed":
            snapshots[actor].add(day)
        if action in {"attend", "course_absence"} and event["status"] == "completed":
            scheduled[actor].add(day)
        if action == "course_absence" and event["status"] == "completed":
            missed[actor].add(day)
        if action == "peer_invitation" and event["status"] == "completed":
            invitations[actor].add(day)
            invitation_opportunities[actor].add(day)
            partner = event["detail"].get("partner")
            if partner:
                invitation_opportunities[partner].add(day)
    for actor, day, _, action in selected_actions(window):
        if action == "request":
            requests[actor].add(day)
    complete_window = complete_step >= window_days
    by_student = {}
    types = Counter()
    isolation = Counter()
    for student in sorted(students, key=lambda row: row["id"]):
        actor = student["id"]
        observed_days = len(snapshots[actor])
        eligible = complete_window and observed_days == window_days
        opportunity_days = scheduled[actor] | invitation_opportunities[actor]
        contact_days = len(contacts[actor])
        if not eligible:
            kind = "insufficient_observation"
            isolation_status = "insufficient_observation"
        else:
            if contact_days == 0:
                isolation_status = ("no_contact_despite_opportunity" if opportunity_days
                                    else "no_observed_opportunity")
            else:
                isolation_status = "contact_observed"
            if len(missed[actor]) >= 3:
                kind = "persistent_absence"
            elif len(requests[actor]) >= 2:
                kind = "repeated_help_attempts"
            elif contact_days == 0:
                kind = "no_social_contact"
            else:
                kind = "mixed_or_connected"
        row = {"student_id": actor, "window_start": start, "window_end": complete_step,
               "observed_snapshot_days": observed_days,
               "scheduled_course_days": len(scheduled[actor]),
               "missed_course_days": len(missed[actor]),
               "peer_invitation_days": len(invitations[actor]),
               "social_opportunity_days": len(opportunity_days),
               "social_contact_days": contact_days,
               "request_attempt_days": len(requests[actor]),
               "trajectory_type": kind, "isolation_status": isolation_status}
        by_student[actor] = row
        types[kind] += 1
        isolation[isolation_status] += 1
    return {"window_days": window_days, "window_start": start,
            "window_end": complete_step, "eligible_students": sum(
                row["trajectory_type"] != "insufficient_observation"
                for row in by_student.values()),
            "trajectory_type_counts": dict(types),
            "isolation_status_counts": dict(isolation),
            "by_student": by_student}


def support_window_metrics(events, complete_step, width=3):
    """Describe selected actions around first completed request and support events."""
    paths = {}
    for actor, day, _, action in selected_actions(events):
        paths.setdefault(actor, []).append((day, action))
    anchors = {}
    for event in sorted(events, key=lambda item: item["ordinal"]):
        if event["status"] == "completed" and event["action"] in {
                "request", "accept_support"}:
            anchors.setdefault(event["action"], {}).setdefault(event["actor"], event["day"])
    result = {"window_days": width}
    for action, windows in (("request", ("before", "after")),
                            ("accept_support", ("after",))):
        anchor_days = anchors.get(action, {})
        prefix = "request" if action == "request" else "support"
        for window in windows:
            counts = {name: 0 for name in ACTION_NAMES}
            eligible = 0
            for actor, day in anchor_days.items():
                if window == "before" and day - width < 1:
                    continue
                if window == "after" and day + width > complete_step:
                    continue
                eligible += 1
                for selected_day, chosen in paths.get(actor, []):
                    if (day - width <= selected_day < day if window == "before"
                            else day < selected_day <= day + width):
                        counts[chosen] += 1
            result[f"{prefix}_{window}_eligible_students"] = eligible
            result[f"{prefix}_{window}_censored_students"] = len(anchor_days) - eligible
            result[f"{prefix}_{window}_action_counts"] = counts
    return result


def support_recovery_metrics(events, complete_step, width=3):
    """Pair supported students' end-of-day states at service and three days later."""
    snapshots = {}
    for event in events:
        if event["action"] != "student_state" or event["status"] != "completed":
            continue
        detail = event["detail"]
        if detail.get("phase") == "end_of_day_v1":
            key = event["day"], event["actor"]
            if key in snapshots:
                raise ValueError("duplicate end-of-day student state")
            if any(not isinstance(detail.get(name), (int, float))
                   or isinstance(detail[name], bool) or not 0 <= detail[name] <= 1
                   for name in ("stress", "mood", "connection")):
                raise ValueError("invalid end-of-day student state")
            snapshots[key] = detail
    anchors = {}
    for event in sorted(events, key=lambda item: item["ordinal"]):
        if event["action"] == "accept_support" and event["status"] == "completed":
            anchors.setdefault(event["actor"], event["day"])
    censored = sum(day + width > complete_step for day in anchors.values())
    missing = 0
    observed = []
    for actor, day in anchors.items():
        if day + width > complete_step:
            continue
        first = snapshots.get((day, actor))
        last = snapshots.get((day + width, actor))
        if first is None or last is None:
            missing += 1
            continue
        if first.get("supported") is not True or last.get("supported") is not True:
            raise ValueError("supported student state disagrees with support event")
        observed.append({name: last[name] - first[name]
                         for name in ("stress", "mood", "connection")})
    return {"window_days": width, "supported_students": len(anchors),
            "observed_students": len(observed), "censored_students": censored,
            "missing_snapshot_students": missing,
            "mean_change": {name: (sum(row[name] for row in observed) / len(observed)
                                  if observed else None)
                            for name in ("stress", "mood", "connection")}}


def course_exposure_metrics(events):
    """Describe recorded attendance contexts without inferring missing schedules."""
    attendance = [event for event in events if event["action"] == "attend"
                  and event["status"] == "completed"]
    by_actor = {}
    for event in attendance:
        actor = event["actor"]
        row = by_actor.setdefault(actor, {"attendance_days": set(),
                                          "recorded_contexts": 0,
                                          "courses": set(), "venues": set()})
        row["attendance_days"].add(event["day"])
        context = event["detail"].get("context")
        if not isinstance(context, dict) or not all(
                isinstance(context.get(key), str) and context[key]
                for key in ("course_id", "venue")):
            continue
        row["recorded_contexts"] += 1
        row["courses"].add(context["course_id"])
        row["venues"].add(context["venue"])
    observed = sum(row["recorded_contexts"] for row in by_actor.values())
    courses = set().union(*(row["courses"] for row in by_actor.values()))
    venues = set().union(*(row["venues"] for row in by_actor.values()))
    return {"attendance_events": len(attendance),
            "context_recorded_events": observed,
            "context_missing_events": len(attendance) - observed,
            "unique_courses": len(courses) if observed else None,
            "unique_venues": len(venues) if observed else None,
            "students_with_attendance": len(by_actor),
            "students_with_recorded_context": sum(
                row["recorded_contexts"] > 0 for row in by_actor.values()),
            "by_student": {actor: {
                "attendance_days": len(row["attendance_days"]),
                "recorded_contexts": row["recorded_contexts"],
                "unique_courses": len(row["courses"]) if row["recorded_contexts"] else None,
                "unique_venues": len(row["venues"]) if row["recorded_contexts"] else None}
                for actor, row in sorted(by_actor.items())}}


def campus_environment_metrics(events):
    """Count observed course bookings and rejected attempts by recorded reason."""
    completed = [event for event in events if event["action"] == "attend"
                 and event["status"] == "completed"]
    rejected = [event for event in events if event["action"] == "attend"
                and event["status"] == "rejected"]
    venues = Counter((event["detail"].get("context") or {}).get("venue")
                     for event in completed)
    venues.pop(None, None)
    occupancy = Counter()
    actor_slots = set()
    for event in completed:
        schedule = event["detail"].get("schedule")
        if not isinstance(schedule, dict):
            continue
        if not schedule.get("open"):
            raise ValueError("attendance recorded for a closed course")
        seat = (event["day"], schedule["course_id"], schedule["venue"], schedule["slot"])
        actor_slot = (event["day"], event["actor"], schedule["slot"])
        if actor_slot in actor_slots:
            raise ValueError("student has conflicting campus bookings")
        actor_slots.add(actor_slot)
        occupancy[seat] += 1
        if occupancy[seat] > schedule["capacity"]:
            raise ValueError("course occupancy exceeds recorded capacity")
    absences = [event for event in events if event["action"] == "course_absence"
                and event["status"] == "completed"]
    missed = Counter(event["actor"] for event in absences)
    return {"course_attendance_events": len(completed),
            "course_rejected_attempts": len(rejected),
            "course_rejection_reasons": dict(Counter(event.get("reason") for event in rejected)),
            "venue_usage": dict(sorted(venues.items())),
            "scheduled_course_sessions": len(occupancy),
            "missed_scheduled_courses": len(absences),
            "students_with_scheduled_absence": len(missed),
            "longest_missed_course_streak": max(
                (event["detail"].get("missed_course_streak", 0) for event in absences),
                default=0)}


def interaction_network_metrics(events):
    """Track observed student pairs by day and interaction source."""
    sources = ("peer", "class_contact", "study_group_copresence",
               "residence_copresence", "club_copresence")
    occurrences = []
    for event in events:
        if event["status"] != "completed":
            continue
        detail = event["detail"]
        if event["action"] == "peer" and detail.get("partner"):
            pairs = [(event["actor"], detail["partner"])]
            source = "peer"
        elif event["action"] == "attend" and detail.get("classmate"):
            pairs = [(event["actor"], detail["classmate"])]
            source = "class_contact"
        elif event["action"] == "study_group":
            pairs = list(itertools.combinations(detail["participants"], 2))
            source = "study_group_copresence"
        elif event["action"] == "club_copresence":
            pairs = list(itertools.combinations(detail["participants"], 2))
            source = "club_copresence"
        elif (event["action"] == "rest"
              and detail.get("trigger") == "shared_residence_presence_v1"):
            pairs = [(event["actor"], sid) for sid in detail["roommates"]]
            source = "residence_copresence"
        else:
            continue
        for left, right in pairs:
            if left == right:
                raise ValueError("interaction network contains self contact")
            occurrences.append((event["day"], event["ordinal"], source,
                                tuple(sorted((left, right)))))
    observed = set()
    by_source = {source: set() for source in sources}
    daily = {}
    for day, _, source, pair in sorted(occurrences):
        row = daily.setdefault(day, {"day": day, "contacts": 0,
                                     "new_edges": 0, "repeat_contacts": 0})
        row["contacts"] += 1
        if pair in observed:
            row["repeat_contacts"] += 1
        else:
            row["new_edges"] += 1
            observed.add(pair)
        by_source[source].add(pair)
    return {"interaction_network_unique_edges": len(observed),
            "interaction_network_edges_by_source": {
                source: len(by_source[source]) for source in sources},
            "interaction_network_contacts_by_source": {
                source: sum(item[2] == source for item in occurrences) for source in sources},
            "interaction_network_daily_change": list(daily.values())}


def _read(run_dir):
    with Observation(run_dir) as observation:
        status = observation.status()
        complete = status["complete"]["step"]
        if complete < 1:
            raise ValueError("no complete simulation step")
        def facts(view):
            days = [json.loads(body) for _, body in view.iter_query(
                "SELECT day,body FROM campus_days WHERE day<=? ORDER BY day", (complete,))]
            events = [{"ordinal": ordinal, "day": day, "actor": actor, "action": action,
                       "status": result, "reason": reason, "detail": json.loads(detail)}
                      for ordinal, day, actor, action, result, reason, detail in view.iter_query(
                          "SELECT ordinal,day,actor,action,status,reason,detail FROM campus_events WHERE day<=? ORDER BY ordinal", (complete,))]
            students = [json.loads(body) for body, in view.iter_query(
                "SELECT body FROM campus_students ORDER BY id")]
            staff = [json.loads(body) for body, in view.iter_query(
                "SELECT body FROM campus_staff ORDER BY id")]
            return days, events, students, staff
        days, events, students, staff = observation.reader.read(facts)
        if len(days) != complete:
            raise ValueError("complete step and daily facts disagree")
        return status, days, events, students, staff


def summarize(run_dir):
    run_dir = Path(run_dir)
    status, days, events, students, staff = _read(run_dir)
    manifest = json.loads((run_dir / "runner.json").read_text(encoding="utf-8"))
    n = len(students)
    waits = [e["detail"]["wait_days"] for e in events if e["action"] == "accept_support" and e["status"] == "completed"]
    pending = sum(s["pending_day"] is not None for s in students)
    groups = {}
    for group in ("lower", "higher"):
        members = [s for s in students if s["initial_group"] == group]
        groups[group] = {"n": len(members), "supported": sum(s["ever_supported"] for s in members),
                         "support_rate": sum(s["ever_supported"] for s in members) / len(members) if members else None}
    action_counts = {}
    for event in events:
        key = event["action"] + ":" + event["status"]
        action_counts[key] = action_counts.get(key, 0) + 1
    deliveries = [event for event in events if event["action"] == "outreach_information"
                  and event["status"] == "completed"]
    exposures = [event for event in events if event["action"] == "intervention_exposure"
                 and event["status"] == "completed"]
    decisions = [e for e in events if e["action"] == "model_decision" and e["status"] == "completed"]
    selection_counts = {name: sum(e["detail"].get("action") == name for e in decisions)
                        for name in ACTION_NAMES}
    selection_rates = {name: count / len(decisions) if decisions else None
                       for name, count in selection_counts.items()}
    peer_events = [e for e in events if e["action"] == "peer" and e["status"] == "completed"]
    peer_invitations = [e for e in events if e["action"] == "peer_invitation"
                        and e["status"] == "completed"]
    peer_responses = [e for e in events if e["action"] == "peer_response"]
    delayed_peer_responses = [e for e in peer_responses if e["detail"].get("delayed")]
    callbacks = [e for e in events if e["action"] == "peer_callback"
                 and e["status"] == "completed"]
    delayed_dialogues = sum(bool(e["detail"].get("delayed_response")) for e in peer_events)
    accepted_responses = sum(e["status"] == "accepted" for e in peer_responses)
    peer_llm = [e for e in peer_events if e["detail"].get("backend") == "llm"]
    peer_valid = [e for e in peer_llm if len(e["detail"].get("turns", [])) == 4]
    trajectories = trajectory_metrics(events)
    profiles = trajectory_profiles(events, students, status["complete"]["step"])
    support_windows = support_window_metrics(events, status["complete"]["step"])
    recovery = support_recovery_metrics(events, status["complete"]["step"])
    course_exposure = course_exposure_metrics(events)
    environment = campus_environment_metrics(events)
    network = interaction_network_metrics(events)
    relationships = Counter()
    relationship_types = Counter()
    relationship_strengths = []
    for event in peer_events:
        rel = event["detail"].get("relationship") or {}
        if rel:
            relationships["formed" if rel.get("contacts") == 1 else "repeated"] += 1
            relationship_types[rel.get("relationship_type", "unknown")] += 1
            relationship_strengths.append(rel.get("strength", 0))
    memory_actions = Counter()
    for student in students:
        memory_actions.update(student.get("long_term_memory", {}).get("action_counts", {}))
    peer_partners = {e["detail"].get("partner") for e in peer_events}
    classmate_events = [e for e in events if e["action"] == "attend"
                        and e["status"] == "completed" and e["detail"].get("classmate")]
    current_contacts = [e for e in classmate_events if e["detail"].get("contact_rule")
                        == "same_course_prior_attendance_v1"]
    invalid_contacts = [e for e in current_contacts if e["detail"]["classmate"] == e["actor"]]
    if invalid_contacts:
        raise ValueError("class attendance contains self contact")
    attendance = {(e["day"], e["actor"]): (e["ordinal"], e["detail"].get("context"))
                  for e in events if e["action"] == "attend" and e["status"] == "completed"}
    if any((partner := attendance.get((e["day"], e["detail"]["classmate"]))) is None
           or partner[0] >= e["ordinal"] or partner[1] != e["detail"]["context"]
           for e in current_contacts):
        raise ValueError("class contact lacks same-day shared attendance")
    study_groups = [e for e in events if e["action"] == "study_group"
                    and e["status"] == "completed"]
    for event in study_groups:
        detail = event["detail"]
        participants = detail["participants"]
        if (len(participants) != 3 or len(set(participants)) != 3
                or participants[-1] != event["actor"]
                or detail.get("trigger") != "third_same_course_arrival_v1"
                or any((attended := attendance.get((event["day"], sid))) is None
                       or attended[0] >= event["ordinal"]
                       or attended[1] != detail["context"] for sid in participants)):
            raise ValueError("study group lacks three prior same-course arrivals")
    residence_events = [e for e in events if e["action"] == "rest"
                        and e["status"] == "completed"
                        and e["detail"].get("trigger") == "shared_residence_presence_v1"]
    residence = defaultdict(list)
    for event in sorted(residence_events, key=lambda item: item["ordinal"]):
        detail = event["detail"]
        venue = detail["context"]["venue"]
        earlier = residence[event["day"], venue]
        if (event["actor"] in earlier or detail["roommates"] != earlier):
            raise ValueError("residence copresence lacks prior same-venue arrival")
        earlier.append(event["actor"])
    club_events = [e for e in events if e["action"] == "club_copresence"
                   and e["status"] == "completed"]
    club_attendance = {}
    for event in events:
        if event["action"] == "attend" and event["status"] == "completed":
            context = event["detail"].get("club_context")
            if context:
                club_attendance[event["day"], event["actor"]] = (
                    event["ordinal"], context)
    for event in sorted(club_events, key=lambda item: item["ordinal"]):
        detail = event["detail"]
        context = detail["context"]
        participants = detail["participants"]
        if (len(participants) < 2 or participants[-1] != event["actor"]
                or detail.get("trigger") != "same_club_prior_attendance_v1"
                or any((arrival := club_attendance.get((event["day"], sid))) is None
                       or arrival[0] >= event["ordinal"] or arrival[1] != context
                       for sid in participants)):
            raise ValueError("club copresence lacks prior same-club arrival")
    recorded_model_calls = sum(
        e["action"] == "model_decision" or
        (e["action"] == "peer" and e["status"] == "completed"
         and e["detail"].get("backend") == "llm") for e in events)
    summary = {"schema_version": SCHEMA_VERSION, "run_id": status["run_id"],
               "status": "failed_diagnostic" if status["failed"] else "complete_steps_only",
               "complete_steps": status["complete"]["step"],
               "scenario_git": manifest["contract"]["release"]["scenario_git"],
               "society0_git": manifest["contract"]["release"]["society0_git"],
               "config_sha256": manifest["contract"]["configuration"]["config_sha256"],
               "condition": manifest["contract"]["configuration"]["plugins"]["campus"]["condition"],
               "seed": manifest["contract"]["configuration"]["plugins"]["campus"]["seed"],
               "n_students": n, "n_staff": len(staff),
               "requested_count": sum(s["ever_requested"] for s in students),
               "requested_denominator": n, "supported_count": sum(s["ever_supported"] for s in students),
               "supported_denominator": n, "completed_wait_count": len(waits),
               "completed_wait_mean_days": sum(waits) / len(waits) if waits else None,
               "pending_count": pending, "pending_wait_excluded": pending,
               "low_connection_7d_count": sum(s["low_streak"] >= 7 for s in students),
               "low_connection_denominator": n,
               "stress_distribution": {"min": min(s["stress"] for s in students),
                    "mean": sum(s["stress"] for s in students) / n,
                    "max": max(s["stress"] for s in students),
                    "bins_0_to_1_by_0_2": [sum(min(4, int(s["stress"] * 5)) == i for s in students)
                                           for i in range(5)]},
               "initial_groups": groups, "action_counts": action_counts,
               "intervention_delivery_count": len(deliveries),
               "intervention_reached_students": len({event["actor"] for event in deliveries}),
               "intervention_exposure_student_days": len(exposures),
               "intervention_information_student_days": sum(
                   event["detail"].get("information_active", False) for event in exposures),
               "intervention_access_student_days": sum(
                   event["detail"].get("access_active", False)
                   and event["detail"].get("effective_access_barrier") <
                   manifest["contract"]["configuration"]["plugins"]["campus"]["access_barrier"]
                   for event in exposures),
               "model_decision_count": len(decisions),
               "action_selection_counts": selection_counts,
               "action_selection_rates": selection_rates,
               "peer_dialogue_completed": len(peer_events),
               "peer_dialogue_llm_completed": len(peer_llm),
               "peer_dialogue_valid_count": len(peer_valid),
               "peer_dialogue_valid_rate": (len(peer_valid) / len(peer_llm)
                                             if peer_llm else None),
               "peer_invitation_count": len(peer_invitations),
               "peer_response_counts": dict(Counter(e["status"] for e in peer_responses)),
               "peer_delayed_response_count": len(delayed_peer_responses),
               "peer_callback_count": len(callbacks),
               "peer_delayed_dialogue_count": delayed_dialogues,
               "peer_acceptance_rate": (accepted_responses / len(peer_responses)
                                        if peer_responses else None),
               "peer_dialogue_completion_rate": (len(peer_events) / len(peer_invitations)
                                                  if peer_invitations else None),
               "relationship_contacts_formed": relationships["formed"],
               "relationship_repeat_contacts": relationships["repeated"],
               "relationship_type_counts": dict(relationship_types),
               "relationship_mean_strength": (sum(relationship_strengths) /
                                               len(relationship_strengths)
                                               if relationship_strengths else None),
               "scenario_trigger_events": sum(e["action"] == "scenario_trigger"
                                               for e in events),
               "long_term_state_means": {
                   key: (sum(s[key] for s in students) / n
                         if all(key in s for s in students) else None)
                   for key in ("academic_engagement", "self_efficacy", "belonging",
                               "help_seeking_belief", "social_avoidance")},
               "long_term_memory": {
                   "students_with_partner_traces": sum(bool(s.get("long_term_memory", {}).get(
                       "partners")) for s in students),
                   "retained_partner_traces": sum(len(s.get("long_term_memory", {}).get(
                       "partners", {})) for s in students),
                   "action_counts": dict(memory_actions)},
               **trajectories,
               "trajectory_profiles": {key: value for key, value in profiles.items()
                                       if key != "by_student"},
               "support_window_metrics": support_windows,
               "support_recovery_metrics": recovery,
               "course_exposure_metrics": course_exposure,
               "campus_environment_metrics": environment,
               **network,
               "trajectory_unique_peer_partners": len(peer_partners - {None}),
               "class_attendance_interactions": len(classmate_events),
               "study_group_events": len(study_groups),
               "residence_presence_events": len(residence_events),
               "club_copresence_events": len(club_events),
               "served_total": sum(d["served_today"] for d in days),
               "network_calls": recorded_model_calls,
               "paid_model_calls": recorded_model_calls,
               "interpretation": "uncalibrated model-internal scenario result"}
    summary["driver"] = manifest["contract"]["configuration"].get("driver", "rule")
    summary["model_provider"] = manifest["contract"]["configuration"]["plugins"]["campus"].get("model_provider")
    summary["model_id"] = manifest["contract"]["configuration"]["plugins"]["campus"].get("model_id")
    summary["requested_rate"] = summary["requested_count"] / n
    if all("initial_stress" in s and "mood" in s for s in students):
        changes = [s["stress"] - s["initial_stress"] for s in students]
        moods = [s["mood"] for s in students]
        summary["stress_change_distribution"] = {"min": min(changes),
            "mean": sum(changes) / n, "max": max(changes)}
        summary["mood_distribution"] = {"min": min(moods),
            "mean": sum(moods) / n, "max": max(moods)}
    follow_ups = [e for e in events if e["action"] == "staff_follow_up"
                  and e["status"] == "completed"]
    summary["follow_up_total"] = len(follow_ups)
    if len(follow_ups) != sum(d.get("follow_ups_today", 0) for d in days):
        raise ValueError("follow-up events and daily usage disagree")
    shifts = [e for e in events if e["action"] == "staff_shift"
              and e["status"] == "completed"]
    if shifts:
        summary["staff_shift_count"] = len(shifts)
        summary["available_slots_total"] = sum(d["available_slots"] for d in days)
        if any(d["served_today"] + d.get("follow_ups_today", 0) > d["available_slots"] for d in days):
            raise ValueError("staff usage exceeds staff slots")
    summary["supported_rate"] = summary["supported_count"] / n
    summary["low_connection_7d_rate"] = summary["low_connection_7d_count"] / n
    if summary["supported_count"] != summary["served_total"] or summary["supported_count"] != len(waits):
        raise ValueError("support events and student states disagree")
    if any(not 0 <= s["stress"] <= 1 for s in students):
        raise ValueError("stress outside model range")
    return summary, days, events


def export(run_dir):
    summary, days, events = summarize(run_dir)
    output = Path(run_dir)
    (output / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with (output / "daily_metrics.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(days[0]))
        writer.writeheader()
        writer.writerows(days)
    with (output / "events.jsonl").open("w", encoding="utf-8") as stream:
        for event in events:
            stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    with (output / "interactions.jsonl").open("w", encoding="utf-8") as stream:
        for record in interaction_records(events):
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    _, _, _, students, _ = _read(run_dir)
    profiles = trajectory_profiles(events, students, summary["complete_steps"])
    with (output / "trajectory_profiles.jsonl").open("w", encoding="utf-8") as stream:
        for row in profiles["by_student"].values():
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    assessment = trajectory_assessment(events, students, summary["complete_steps"])
    (output / "trajectory-assessment.json").write_text(
        json.dumps(assessment, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return summary


def trajectory_report(run_dir):
    """Write JSON and a dependency-free HTML trajectory review artifact."""
    output = Path(run_dir)
    status, _, events, students, _ = _read(output)
    complete_step = status["complete"]["step"]
    assessment = trajectory_assessment(events, students, complete_step)
    (output / "trajectory-assessment.json").write_text(
        json.dumps(assessment, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    payload = json.dumps(assessment, ensure_ascii=False).replace("</", "<\\/")
    html = """<!doctype html><meta charset="utf-8"><title>CampusSimulation trajectory report</title>
<style>body{font:15px system-ui;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#172337}section{background:#f6f8fb;border-radius:10px;padding:1rem;margin:1rem 0}table{border-collapse:collapse;width:100%}td,th{padding:.4rem;border-bottom:1px solid #dce2eb;text-align:left}canvas{width:100%;border:1px solid #dce2eb}</style>
<h1>行为轨迹评估</h1><p>该报告描述模拟行为，不是现实或临床孤立诊断。</p>
<section><h2>每日行动分布</h2><canvas id="daily" width="1000" height="300"></canvas></section>
<section><h2>轨迹类型</h2><pre id="types"></pre><h2>孤立状态</h2><pre id="isolation"></pre></section>
<section><h2>学生轨迹</h2><table><thead><tr><th>学生</th><th>类型</th><th>孤立状态</th><th>行动日</th><th>行动路径</th></tr></thead><tbody id="students"></tbody></table></section>
<script id="data" type="application/json">""" + payload + """</script>
<script>const report=JSON.parse(document.getElementById('data').textContent);const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
types.textContent=JSON.stringify(report.trajectory_type_counts,null,2);isolation.textContent=JSON.stringify(report.isolation_status_counts,null,2);
for(const [id,row] of Object.entries(report.students)){students.insertAdjacentHTML('beforeend',`<tr><td>${esc(id)}</td><td>${esc(row.trajectory_type)}</td><td>${esc(row.isolation_status)}</td><td>${row.action_days}</td><td>${esc(row.action_path.map(x=>x.day+':'+x.action).join(' → '))}</td></tr>`)}
const c=document.getElementById('daily').getContext('2d'), rows=report.daily_action_counts, names=report.action_names, max=Math.max(1,...rows.map(r=>names.reduce((n,a)=>n+r[a],0))); c.font='12px system-ui'; rows.forEach((r,i)=>{let x=35+i*(930/Math.max(1,rows.length)), y=270; for(const a of names){let h=r[a]/max*220;c.fillStyle={attend:'#4c78a8',rest:'#f2a541',peer:'#59a14f',request:'#e15759',accept_support:'#b279a2'}[a]||'#999';c.fillRect(x,y-h,18,h);y-=h} c.fillStyle='#172337';c.fillText(r.day,x,290)});</script>
"""
    (output / "trajectory-report.html").write_text(html, encoding="utf-8")
    return {"assessment": str(output / "trajectory-assessment.json"),
            "html": str(output / "trajectory-report.html"),
            "students": len(assessment["students"])}


def verify_resume(original, resumed):
    left, left_days, left_events = summarize(original)
    right, right_days, right_events = summarize(resumed)
    if left["complete_steps"] != right["complete_steps"]:
        raise ValueError("step counts differ")
    if left_days != right_days or left_events != right_events:
        raise ValueError("authoritative daily facts or events differ")
    ignored = {"run_id", "scenario_git"}
    if {k: v for k, v in left.items() if k not in ignored} != {k: v for k, v in right.items() if k not in ignored}:
        raise ValueError("aggregate results differ")
    return {"equivalent": True, "steps": left["complete_steps"]}
