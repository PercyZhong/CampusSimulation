"""Evidence-bound interpretation of completed synthetic experiments."""

import csv
import json
import re
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from .models import ModelClient, _json_object_prefix

PROMPT_VERSION = "experiment-analysis-v1"
METRICS = {
    "requested_rate": "求助率",
    "supported_rate": "获得支持率",
    "low_connection_7d_rate": "持续低联结率",
    "completed_wait_mean_days": "已完成服务的平均等待天数",
    "mean_stress": "末日平均压力",
    "mean_mood": "末日平均心情",
}
PROSE_NUMBER = re.compile(r"\d")


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _complete_run(path):
    summary = _json(path / "summary.json")
    if summary.get("status") == "failed_diagnostic":
        raise ValueError(f"{path.name}: failed diagnostic run cannot be analyzed")
    steps = summary.get("complete_steps")
    if type(steps) is not int or steps < 1:
        raise ValueError(f"{path.name}: run has no completed day count")
    runner = path / "runner-status.json"
    if runner.is_file():
        status = _json(runner)
        if status.get("complete_step", 0) < steps:
            raise ValueError(f"{path.name}: run is incomplete")
    return summary


def _run_metrics(summary):
    return {
        "requested_rate": summary["requested_rate"],
        "supported_rate": summary["supported_rate"],
        "low_connection_7d_rate": summary["low_connection_7d_rate"],
        "completed_wait_mean_days": summary["completed_wait_mean_days"],
        "mean_stress": summary["stress_distribution"]["mean"],
        "mean_mood": summary.get("mood_distribution", {}).get("mean"),
    }


def _evidence(items, label, value, source, *, detail=None):
    item = {"id": f"E{len(items) + 1}", "label": label, "value": value,
            "source": source}
    if detail is not None:
        item["detail"] = detail
    items.append(item)
    return item


def _days(path, steps):
    with (path / "daily_metrics.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if len(rows) != steps or [int(row["day"]) for row in rows] != list(range(1, steps + 1)):
        raise ValueError(f"{path.name}: daily metrics do not cover completed days")
    return rows


def _trajectories(path, steps, items):
    events_path = path / "events.jsonl"
    by_actor = defaultdict(list)
    with events_path.open(encoding="utf-8") as stream:
        for line in stream:
            event = json.loads(line)
            if event["actor"].startswith("s") and event["action"] in {
                "student_state", "attend", "course_absence", "peer", "request",
                "accept_support", "peer_response", "outreach_information"
            }:
                by_actor[event["actor"]].append(event)
    profiles_path = path / "trajectory_profiles.jsonl"
    with profiles_path.open(encoding="utf-8") as stream:
        profiles = [json.loads(line) for line in stream]
    if len(profiles) != len(by_actor) or len({p["student_id"] for p in profiles}) != len(profiles):
        raise ValueError(f"{path.name}: trajectory profiles disagree with events")
    selected = []
    states = {}
    for actor, events in by_actor.items():
        snapshots = [e for e in events if e["action"] == "student_state"]
        if snapshots:
            states[actor] = snapshots
    if states:
        rising = max(states, key=lambda actor: states[actor][-1]["detail"]["stress"] -
                     states[actor][0]["detail"]["stress"])
        falling = min(states, key=lambda actor: states[actor][-1]["detail"]["stress"] -
                      states[actor][0]["detail"]["stress"])
        for actor in (rising, falling):
            if actor not in selected:
                selected.append(actor)
    for kind in ("persistent_absence", "repeated_help_attempts", "no_social_contact",
                 "mixed_or_connected", "insufficient_observation"):
        candidate = next((p for p in profiles if p["trajectory_type"] == kind
                          and p["student_id"] not in selected), None)
        if candidate:
            selected.append(candidate["student_id"])
    for actor in selected[:6]:
        events = by_actor[actor]
        profile = next(p for p in profiles if p["student_id"] == actor)
        snapshots = [e for e in events if e["action"] == "student_state"]
        actions = [{"day": e["day"], "action": e["action"], "status": e["status"]}
                   for e in events if e["action"] != "student_state"]
        detail = {"student": actor, "trajectory_type": profile["trajectory_type"],
                  "isolation_status": profile["isolation_status"],
                  "actions": actions[:24], "action_count": len(actions),
                  "snapshot_days": len(snapshots), "complete_days": steps}
        if snapshots:
            detail["first_state"] = {"day": snapshots[0]["day"], **{
                key: snapshots[0]["detail"].get(key) for key in ("stress", "mood", "connection")}}
            detail["last_state"] = {"day": snapshots[-1]["day"], **{
                key: snapshots[-1]["detail"].get(key) for key in ("stress", "mood", "connection")}}
        _evidence(items, f"学生 {actor} 的轨迹片段", detail, "trajectory_profiles.jsonl + events.jsonl")


def run_evidence(path):
    path = Path(path)
    summary = _complete_run(path)
    steps = summary["complete_steps"]
    rows = _days(path, steps)
    if summary["requested_count"] / summary["n_students"] != summary["requested_rate"]:
        raise ValueError("request rate disagrees with its numerator")
    if summary["supported_count"] / summary["n_students"] != summary["supported_rate"]:
        raise ValueError("support rate disagrees with its numerator")
    if sum(summary["trajectory_profiles"]["trajectory_type_counts"].values()) != summary["n_students"]:
        raise ValueError("trajectory profile counts disagree with student count")
    for field, value in (("mean_stress", summary["stress_distribution"]["mean"]),
                         ("mean_mood", summary.get("mood_distribution", {}).get("mean"))):
        if value is not None and rows[-1].get(field) not in (None, "") and abs(
                float(rows[-1][field]) - value) > 0.00001:
            raise ValueError(f"last daily {field} disagrees with summary")
    items = []
    for key, value in _run_metrics(summary).items():
        if value is not None:
            _evidence(items, METRICS[key], round(value, 6), "summary.json")
    for field, label in (("mean_stress", "平均压力"), ("mean_mood", "平均心情")):
        values = [float(row[field]) for row in rows if row.get(field) not in (None, "")]
        if len(values) != steps:
            continue
        _evidence(items, f"逐日{label}首末变化", {
            "first_day": 1, "first": values[0], "last_day": steps,
            "last": values[-1], "delta": round(values[-1] - values[0], 6)},
            "daily_metrics.csv")
        if len(values) > 1:
            index = max(range(1, steps), key=lambda i: abs(values[i] - values[i - 1]))
            _evidence(items, f"逐日{label}最大相邻变化", {
                "from_day": index, "to_day": index + 1,
                "delta": round(values[index] - values[index - 1], 6)},
                "daily_metrics.csv")
    _evidence(items, "轨迹分型人数", summary["trajectory_profiles"]["trajectory_type_counts"],
              "summary.json")
    _trajectories(path, steps, items)
    return {"kind": "run", "name": path.name, "metadata": {
        key: summary.get(key) for key in ("condition", "seed", "driver", "model_provider",
                                          "model_id", "scenario_git", "config_sha256",
                                          "n_students", "n_staff", "complete_steps")},
        "evidence": items}


def batch_evidence(path, plan_path):
    path = Path(path)
    plan = _json(Path(plan_path))
    if not plan.get("runs") or len(plan["runs"]) != len(plan["conditions"]) * len(plan["seeds"]):
        raise ValueError("invalid batch plan")
    summaries = {}
    base = None
    for item in plan["runs"]:
        summary = _complete_run(path / item["name"])
        config = item["config"]
        identity = (config["condition"], config["seed"])
        if identity in summaries or summary["condition"] != identity[0] or summary["seed"] != identity[1]:
            raise ValueError("batch run identity mismatch")
        if summary["complete_steps"] != config["end"]:
            raise ValueError("batch run observation window mismatch")
        if (summary["n_students"] != config["n_students"]
                or summary["n_staff"] != config["n_staff"]
                or summary.get("driver", "rule") != config.get("driver", "rule")):
            raise ValueError("batch summary disagrees with planned configuration")
        comparable = {key: config.get(key) for key in ("n_students", "n_staff", "end",
                      "capacity", "open_every", "access_barrier", "outreach_day",
                      "periods_per_day", "driver", "model_provider", "model_id")}
        comparable["scenario_git"] = summary.get("scenario_git")
        if base is None:
            base = comparable
        elif comparable != base:
            raise ValueError("batch runs have incomparable configurations or code versions")
        summaries[identity] = summary
    if set(summaries) != {(condition, seed) for condition in plan["conditions"]
                         for seed in plan["seeds"]}:
        raise ValueError("batch plan and completed runs differ")
    items = []
    for condition in plan["conditions"]:
        for metric, label in METRICS.items():
            values = [_run_metrics(summaries[condition, seed])[metric] for seed in plan["seeds"]]
            if all(value is not None for value in values):
                _evidence(items, f"{condition}：{label}均值", round(sum(values) / len(values), 6),
                          "batch run summary.json", detail={"by_seed": dict(zip(plan["seeds"], values))})
                if condition != "baseline" and "baseline" in plan["conditions"]:
                    deltas = {seed: round(_run_metrics(summaries[condition, seed])[metric] -
                                          _run_metrics(summaries["baseline", seed])[metric], 6)
                              for seed in plan["seeds"]
                              if _run_metrics(summaries["baseline", seed])[metric] is not None}
                    if len(deltas) == len(plan["seeds"]):
                        _evidence(items, f"{condition} 相对 baseline：{label}同种子配对差", {
                            "mean": round(sum(deltas.values()) / len(deltas), 6),
                            "by_seed": deltas}, "batch run summary.json")
        representative = next(item for item in plan["runs"]
                              if item["config"]["condition"] == condition
                              and item["config"]["seed"] == plan["seeds"][0])
        run_path = path / representative["name"]
        trajectory_items = []
        _trajectories(run_path, summaries[condition, plan["seeds"][0]]["complete_steps"],
                      trajectory_items)
        for example in trajectory_items[:2]:
            _evidence(items, f"{condition} / seed {plan['seeds'][0]}：{example['label']}",
                      example["value"],
                      f"{representative['name']}/trajectory_profiles.jsonl + events.jsonl",
                      detail={"selection": "first shared seed; illustrative, not a group estimate"})
    return {"kind": "batch", "name": path.name, "metadata": {
        "conditions": plan["conditions"], "seeds": plan["seeds"],
        "run_count": len(summaries), "shared_config": base}, "evidence": items}


def _checked_sections(draft, evidence_ids):
    checked = {}
    for section, fields in (("findings", ("title", "interpretation")),
                            ("recommendations", ("action", "rationale", "verification"))):
        entries = draft.get(section, [])
        if not isinstance(entries, list) or not 1 <= len(entries) <= 8:
            raise ValueError(f"invalid {section} in model response")
        checked[section] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise TypeError(f"invalid {section} entry")
            prose = {field: entry.get(field, "") for field in fields}
            if any(not isinstance(value, str) or len(value) > 800 for value in prose.values()):
                raise ValueError("invalid analysis prose")
            refs = entry.get("evidence_ids", [])
            if not isinstance(refs, list) or len(refs) > 10:
                raise ValueError("invalid evidence references")
            valid = [ref for ref in refs if isinstance(ref, str) and ref in evidence_ids]
            notes = []
            if len(valid) != len(refs):
                notes.append("引用了不存在的证据")
            if any(PROSE_NUMBER.search(value) for value in prose.values()):
                notes.append("文字中出现未经程序附注的数字或日期")
            if not valid:
                notes.append("缺少可核对的证据")
            checked[section].append({**prose, "evidence_ids": valid,
                                     "status": "hypothesis" if notes else "evidence_linked",
                                     "review_notes": notes})
    limitations = draft.get("limitations", [])
    if not isinstance(limitations, list) or not 1 <= len(limitations) <= 8 or any(
            not isinstance(item, str) or len(item) > 800 for item in limitations):
        raise ValueError("invalid limitations in model response")
    checked["limitations"] = limitations
    return checked


def generate_report(packet, provider, model, *, api_key=None):
    """Request one report, allowing a transport retry, and check evidence references."""
    client = ModelClient(provider, model, 2, temperature=0.1, api_key=api_key)
    system = ("你是合成校园实验的分析员。只使用给定证据，返回一个 JSON 对象，不要 Markdown。"
              "字段：findings=[{title,interpretation,evidence_ids}]，"
              "recommendations=[{action,rationale,verification,evidence_ids}]，limitations=[字符串]。"
              "findings 给出主要发现、轨迹解释、反例或不一致；recommendations 给出可在下一次模拟中检验的校园服务建议。"
              "每项 evidence_ids 必须引用证据包中的 ID。不得自行计算、填写阿拉伯数字、百分比或日期；"
              "数值由程序附在证据卡中。没有直接证据的推测保留为空 evidence_ids，标为假设。"
              "不要诊断学生，也不要把模拟差异写成真实学校的因果效果。")
    content, usage = client._request(system, json.dumps(packet, ensure_ascii=False), 3500)
    draft = _json_object_prefix(content)
    if not isinstance(draft, dict):
        raise TypeError("model report must be a JSON object")
    checked = _checked_sections(draft, {item["id"] for item in packet["evidence"]})
    checked["limitations"].append("压力和心情是未校准的模型内部变量；建议仅用于下一轮模拟验证。")
    return {"schema_version": 1, "prompt_version": PROMPT_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            "analysis_model": {"provider": provider, "model": model, "temperature": 0.1,
                               "calls": client.calls, "usage": usage},
            "scope": {key: packet[key] for key in ("kind", "name", "metadata")},
            "evidence": packet["evidence"], **checked,
            "audit_note": "自动复核证据编号和文字数字；解释是否合理仍需人工审阅。"}


def save_report(path, report):
    folder = Path(path) / "analysis-reports"
    folder.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = folder / f"{stamp}-{uuid.uuid4().hex[:8]}.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target
