"""Reproducible reporting for the V063 formal evaluation matrix."""

import json
import re
import statistics
from pathlib import Path

from .analysis import ACTION_NAMES, interaction_network_metrics, trajectory_metrics

PATTERN = re.compile(
    r"^v063-matrix-(rule|qwen|deepseek)-(baseline|outreach|easier_access)-seed(11|22|33)-10d$"
)
DRIVERS = ("rule", "qwen", "deepseek")
CONDITIONS = ("baseline", "outreach", "easier_access")
SEEDS = (11, 22, 33)


def _stats(values):
    values = [value for value in values if value is not None]
    return {"mean": statistics.mean(values) if values else None,
            "sample_sd": statistics.stdev(values) if len(values) > 1 else None,
            "n": len(values)}


def _row(path):
    match = PATTERN.match(path.name)
    if not match:
        return None
    driver, condition, seed = match.group(1), match.group(2), int(match.group(3))
    summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
    events = [json.loads(line) for line in (path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    if summary["status"] != "complete_steps_only" or summary["complete_steps"] != 10:
        raise ValueError(f"invalid completed run: {path}")
    if summary["n_students"] != 40 or summary["seed"] != seed:
        raise ValueError(f"protocol mismatch: {path}")
    decisions = [event for event in events
                 if event["action"] == "model_decision" and event["status"] == "completed"]
    selection_counts = {name: sum(event["detail"].get("action") == name for event in decisions)
                        for name in ACTION_NAMES}
    if not decisions:
        selection_counts = {name: sum(event["action"] == name and event["status"] in {"completed", "rejected"}
                                      for event in events) for name in ACTION_NAMES}
    selection_rates = {name: count / len(decisions) if decisions else None
                       for name, count in selection_counts.items()}
    peers = [event for event in events if event["action"] == "peer" and event["status"] == "completed"]
    llm_peers = [event for event in peers if event["detail"].get("backend") == "llm"]
    valid_peers = [event for event in llm_peers
                   if len(event["detail"].get("turns", [])) == 4]
    trajectories = trajectory_metrics(events)
    network = interaction_network_metrics(events)
    row = {"run": path.name, "driver": driver, "condition": condition, "seed": seed,
           "model_provider": summary.get("model_provider"), "model_id": summary.get("model_id"),
           "complete_steps": summary["complete_steps"],
           "requested_rate": summary["requested_rate"],
           "supported_rate": summary["supported_rate"],
           "mean_stress": summary["stress_distribution"]["mean"],
           "mean_mood": summary.get("mood_distribution", {}).get("mean"),
           "model_calls": summary.get("network_calls", 0),
           "trajectory_action_transitions": trajectories["trajectory_action_transitions"],
           "trajectory_longest_same_action_streak": trajectories[
               "trajectory_longest_same_action_streak"],
           "peer_relationships_unique": trajectories["peer_relationships_unique"],
           "peer_repeat_contacts": trajectories["peer_repeat_contacts"],
           "peer_repeat_contact_rate": trajectories["peer_repeat_contact_rate"],
           "trajectory_transition_matrix": trajectories["trajectory_transition_matrix"],
           "interaction_network_unique_edges": network["interaction_network_unique_edges"],
           "interaction_network_edges_by_source": network[
               "interaction_network_edges_by_source"],
           "interaction_network_daily_change": network["interaction_network_daily_change"],
           "peer_completed": len(peers), "peer_valid_count": len(valid_peers),
           "peer_dialogue_status": ("llm_validated" if llm_peers and len(valid_peers) == len(llm_peers)
                                     else "rule_offline" if driver == "rule" else "no_peer_selected"),
           "peer_valid_rate": (1.0 if driver == "rule" and peers else
                               len(valid_peers) / len(llm_peers) if llm_peers else None)}
    row["action_selection_counts"] = selection_counts
    row["action_selection_rates"] = selection_rates
    if driver != "rule" and llm_peers and row["peer_valid_rate"] != 1:
        raise ValueError(f"invalid LLM peer dialogues: {path}")
    return row


def build_report(root="runs"):
    root = Path(root)
    expected = {f"v063-matrix-{d}-{c}-seed{s}-10d"
                for d in DRIVERS for c in CONDITIONS for s in SEEDS}
    found = {path.name for path in root.iterdir() if path.is_dir() and PATTERN.match(path.name)}
    missing = sorted(expected - found)
    if missing:
        raise ValueError(f"missing matrix runs: {missing}")
    rows = [_row(root / name) for name in sorted(expected)]
    metrics = ("requested_rate", "supported_rate", "mean_stress", "mean_mood", "model_calls",
               "peer_completed", "peer_valid_rate", "trajectory_action_transitions",
               "trajectory_longest_same_action_streak", "peer_relationships_unique",
               "peer_repeat_contacts", "peer_repeat_contact_rate",
               "interaction_network_unique_edges")
    by_group = {}
    for driver in DRIVERS:
        for condition in CONDITIONS:
            subset = [row for row in rows if row["driver"] == driver and row["condition"] == condition]
            group = {metric: _stats([row[metric] for row in subset]) for metric in metrics}
            group["action_selection_counts"] = {
                name: sum(row["action_selection_counts"].get(name, 0) for row in subset)
                for name in ACTION_NAMES}
            group["action_selection_rates"] = {
                name: _stats([row["action_selection_rates"].get(name) for row in subset])
                for name in ACTION_NAMES}
            group["trajectory_transition_matrix"] = {
                left: {right: sum(row["trajectory_transition_matrix"][left][right]
                                  for row in subset) for right in ACTION_NAMES}
                for left in ACTION_NAMES}
            group["interaction_network_edges_by_source"] = {
                source: _stats([row["interaction_network_edges_by_source"][source]
                                for row in subset])
                for source in ("peer", "class_contact", "study_group_copresence")}
            by_group[f"{driver}:{condition}"] = group
    paired = []
    for driver in DRIVERS:
        for seed in SEEDS:
            baseline = next(row for row in rows if row["driver"] == driver
                            and row["condition"] == "baseline" and row["seed"] == seed)
            for condition in ("outreach", "easier_access"):
                other = next(row for row in rows if row["driver"] == driver
                             and row["condition"] == condition and row["seed"] == seed)
                paired.append({"driver": driver, "seed": seed, "condition": condition,
                               "requested_rate_delta": other["requested_rate"] - baseline["requested_rate"],
                               "supported_rate_delta": other["supported_rate"] - baseline["supported_rate"],
                               "mean_stress_delta": other["mean_stress"] - baseline["mean_stress"],
                               "mean_mood_delta": other["mean_mood"] - baseline["mean_mood"]})
    return {"protocol": "v063-formal-matrix-v1", "expected_runs": len(expected),
            "found_runs": len(rows), "missing_runs": missing, "runs": rows,
            "by_driver_condition": by_group, "paired_policy_deltas": paired,
            "interpretation": "uncalibrated model-internal scenario results; three seeds imply no significance"}
