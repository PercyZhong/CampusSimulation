"""Export only authoritative facts through Society0's read-only Observation."""

import csv
import itertools
import json
from pathlib import Path

from society0.kernel.observation import Observation

SCHEMA_VERSION = 1
ACTION_NAMES = ("attend", "rest", "peer", "request", "accept_support")


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
    decisions = [e for e in events if e["action"] == "model_decision" and e["status"] == "completed"]
    selection_counts = {name: sum(e["detail"].get("action") == name for e in decisions)
                        for name in ACTION_NAMES}
    selection_rates = {name: count / len(decisions) if decisions else None
                       for name, count in selection_counts.items()}
    peer_events = [e for e in events if e["action"] == "peer" and e["status"] == "completed"]
    peer_llm = [e for e in peer_events if e["detail"].get("backend") == "llm"]
    peer_valid = [e for e in peer_llm if len(e["detail"].get("turns", [])) == 4]
    trajectory = [e["detail"].get("action") for e in decisions]
    transitions = sum(left != right for left, right in itertools.pairwise(trajectory))
    peer_partners = {e["detail"].get("partner") for e in peer_events}
    classmate_events = [e for e in events if e["action"] == "attend"
                        and e["status"] == "completed" and e["detail"].get("classmate")]
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
               "model_decision_count": len(decisions),
               "action_selection_counts": selection_counts,
               "action_selection_rates": selection_rates,
               "peer_dialogue_completed": len(peer_events),
               "peer_dialogue_llm_completed": len(peer_llm),
               "peer_dialogue_valid_count": len(peer_valid),
               "peer_dialogue_valid_rate": (len(peer_valid) / len(peer_llm)
                                             if peer_llm else None),
               "trajectory_action_transitions": transitions,
               "trajectory_unique_peer_partners": len(peer_partners - {None}),
               "class_attendance_interactions": len(classmate_events),
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
    return summary


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
