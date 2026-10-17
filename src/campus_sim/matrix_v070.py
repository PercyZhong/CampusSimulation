"""Frozen V070 runs and strict reporting, separate from the V063 matrix."""

import asyncio
import hashlib
import json
import statistics
import subprocess
from dataclasses import asdict
from pathlib import Path

from .domain import Config

PROTOCOL = Path(__file__).resolve().parents[2] / "configs/v070-evaluation-protocol.json"
BEHAVIOR_FILES = (
    "src/campus_sim/domain.py", "src/campus_sim/mechanism.py",
    "src/campus_sim/models.py", "src/campus_sim/plan.py",
    "src/campus_sim/schedule.py", "src/campus_sim/population.py",
    "src/campus_sim/analysis.py",
    "configs/baseline.json", "configs/outreach.json",
    "configs/easier_access.json", "configs/targeted_outreach.json",
)


def protocol():
    return json.loads(PROTOCOL.read_text(encoding="utf-8-sig"))


def _git(*args):
    return subprocess.run(["git", *args], cwd=PROTOCOL.parents[1], check=False,
                          capture_output=True, text=True)


def _behavior_is_frozen(sha):
    spec = protocol()
    anchor = spec["behavior_git"]
    if _git("merge-base", "--is-ancestor", anchor, sha).returncode:
        return False
    return _git("diff", "--quiet", anchor, sha, "--", *BEHAVIOR_FILES).returncode == 0


def _current_behavior_is_frozen():
    head = _git("rev-parse", "HEAD").stdout.strip()
    return (_behavior_is_frozen(head)
            and _git("diff", "--quiet", "HEAD", "--", *BEHAVIOR_FILES).returncode == 0)


def _config(condition, seed, driver):
    spec = protocol()
    raw = json.loads((PROTOCOL.parents[1] / "configs" / f"{condition}.json").read_text(
        encoding="utf-8"))
    raw.update(seed=seed, end=spec["days"])
    settings = spec["drivers"][driver]
    if driver != "rule":
        raw.update(driver="llm", model_provider=settings["provider"],
                   model_id=settings["model"], model_temperature=settings["temperature"],
                   max_model_calls=settings["max_model_calls"])
    parsed = Config.parse(raw)
    if (parsed.n_students, parsed.n_staff, parsed.condition) != (
            spec["n_students"], spec["n_staff"], condition):
        raise ValueError(f"base configuration differs from protocol: {condition}")
    public = asdict(parsed)
    public["release"] = raw.get("release", {"scenario": "0.6.0"})
    digest = hashlib.sha256(json.dumps(public, sort_keys=True,
                            separators=(",", ":")).encode()).hexdigest()
    return raw, digest


def _name(driver, condition, seed):
    return protocol()["output_pattern"].format(driver=driver, condition=condition, seed=seed)


def _stats(values):
    valid = [value for value in values if value is not None]
    return {"n": len(valid), "mean": statistics.mean(valid) if valid else None,
            "sample_sd": statistics.stdev(valid) if len(valid) > 1 else None}


def run_matrix(root="runs", driver="rule"):
    """Run one complete three-seed matrix; never overwrite an existing run."""
    from society0.kernel.runner import run_plan

    from .analysis import export
    from .plan import build

    spec = protocol()
    if driver not in spec["drivers"]:
        raise ValueError(f"unknown driver: {driver}")
    if not _current_behavior_is_frozen():
        raise ValueError("behavior code differs from the frozen V070 version")
    root = Path(root)
    completed = []
    existing = [str(root / _name(driver, condition, seed))
                for condition in spec["conditions"] for seed in spec["seeds"]
                if (root / _name(driver, condition, seed)).exists()]
    if existing:
        raise ValueError(f"run directories already exist: {existing}")
    for condition in spec["conditions"]:
        for seed in spec["seeds"]:
            path = root / _name(driver, condition, seed)
            raw, _ = _config(condition, seed, driver)
            result = asyncio.run(run_plan(path, build(raw)))
            if result["status"] != "completed" or result["complete_step"] != spec["days"]:
                raise ValueError(f"run did not complete: {path}: {result}")
            if result.get("diagnostic_errors", 0):
                raise ValueError(f"run has diagnostic errors: {path}: {result}")
            summary = export(path)
            if summary["status"] != "complete_steps_only":
                raise ValueError(f"export is incomplete: {path}")
            completed.append(path.name)
    return completed


def _row(path, condition, seed, driver):
    spec = protocol()
    _, expected_hash = _config(condition, seed, driver)
    summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
    expected = {"status": "complete_steps_only", "complete_steps": spec["days"],
                "seed": seed, "condition": condition, "n_students": spec["n_students"],
                "n_staff": spec["n_staff"], "config_sha256": expected_hash,
                "driver": "rule" if driver == "rule" else "llm",
                "model_provider": spec["drivers"][driver]["provider"],
                "model_id": spec["drivers"][driver]["model"]}
    for key, value in expected.items():
        if summary.get(key) != value:
            raise ValueError(f"{path}: {key} differs from protocol")
    if not _behavior_is_frozen(summary["scenario_git"]):
        raise ValueError(f"{path}: behavior revision differs from protocol")
    profiles = [json.loads(line) for line in (path / "trajectory_profiles.jsonl").read_text(
        encoding="utf-8").splitlines()]
    if (len(profiles) != spec["n_students"]
            or len({p["student_id"] for p in profiles}) != len(profiles)):
        raise ValueError(f"{path}: invalid student profiles")
    aggregate = summary["trajectory_profiles"]
    if aggregate["window_days"] != spec["trajectory_window_days"]:
        raise ValueError(f"{path}: trajectory window differs from protocol")
    for field in ("trajectory_type_counts", "isolation_status_counts"):
        counts = {kind: sum(p["trajectory_type" if field.startswith("trajectory")
                              else "isolation_status"] == kind for p in profiles)
                  for kind in aggregate[field]}
        if counts != aggregate[field] or sum(counts.values()) != len(profiles):
            raise ValueError(f"{path}: {field} disagrees with profiles")
    if aggregate["eligible_students"] != spec["n_students"]:
        raise ValueError(f"{path}: incomplete trajectory observation")
    if driver == "rule" and summary["network_calls"] != 0:
        raise ValueError(f"{path}: rule run used model calls")
    if summary["network_calls"] > spec["drivers"][driver]["max_model_calls"]:
        raise ValueError(f"{path}: model call budget exceeded")
    isolated = aggregate["isolation_status_counts"].get("no_contact_despite_opportunity", 0)
    return {"run": path.name, "driver": driver, "condition": condition, "seed": seed,
            "scenario_git": summary["scenario_git"], "config_sha256": expected_hash,
            "requested_rate": summary["requested_rate"],
            "supported_rate": summary["supported_rate"],
            "low_connection_7d_rate": summary["low_connection_7d_rate"],
            "mean_stress": summary["stress_distribution"]["mean"],
            "mean_mood": summary["mood_distribution"]["mean"],
            "no_contact_despite_opportunity_rate": isolated / spec["n_students"],
            "trajectory_type_counts": aggregate["trajectory_type_counts"],
            "isolation_status_counts": aggregate["isolation_status_counts"],
            "action_counts": summary["action_counts"],
            "network_calls": summary["network_calls"],
            "completed_wait_mean_days": summary["completed_wait_mean_days"]}


def build_report(root="runs", driver="rule"):
    spec = protocol()
    if driver not in spec["drivers"]:
        raise ValueError(f"unknown driver: {driver}")
    if not _current_behavior_is_frozen():
        raise ValueError("behavior code differs from the frozen V070 version")
    root = Path(root)
    names = [_name(driver, condition, seed) for condition in spec["conditions"]
             for seed in spec["seeds"]]
    missing = [name for name in names if not (root / name).is_dir()]
    if missing:
        raise ValueError(f"missing V070 runs: {missing}")
    rows = [_row(root / _name(driver, condition, seed), condition, seed, driver)
            for condition in spec["conditions"] for seed in spec["seeds"]]
    metrics = spec["primary_metrics"]
    groups = {condition: {metric: _stats([row[metric] for row in rows
                                          if row["condition"] == condition]) for metric in metrics}
              for condition in spec["conditions"]}
    by_key = {(row["condition"], row["seed"]): row for row in rows}
    paired = [{"condition": condition, "seed": seed,
               "deltas": {metric: (by_key[(condition, seed)][metric]
                                    - by_key[("baseline", seed)][metric]) for metric in metrics}}
              for condition in spec["conditions"] if condition != "baseline"
              for seed in spec["seeds"]]
    delta_stats = {condition: {metric: _stats([entry["deltas"][metric] for entry in paired
                                              if entry["condition"] == condition])
                               for metric in metrics}
                   for condition in spec["conditions"] if condition != "baseline"}
    return {"protocol": spec["protocol_id"], "behavior_git": spec["behavior_git"],
            "driver": driver, "expected_runs": len(names), "found_runs": len(rows),
            "runs": rows, "by_condition": groups, "paired_condition_deltas": paired,
            "paired_delta_stats": delta_stats,
            "interpretation": "uncalibrated synthetic scenario; descriptive three-seed results"}

