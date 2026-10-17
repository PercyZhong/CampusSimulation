"""The frozen matrix rejects incomplete and mixed-protocol results."""

import json

import pytest

from campus_sim.matrix_v069 import _config, _name, build_report, protocol


def test_protocol_has_four_conditions_and_distinct_names():
    spec = protocol()
    names = {_name("rule", condition, seed) for condition in spec["conditions"]
             for seed in spec["seeds"]}
    assert len(names) == 12
    assert spec["trajectory_window_days"] == 7
    assert all(_config(condition, seed, "rule")[1]
               for condition in spec["conditions"] for seed in spec["seeds"])


def test_report_rejects_missing_and_bad_config(tmp_path, monkeypatch):
    spec = protocol()
    with pytest.raises(ValueError, match="missing V069 runs"):
        build_report(tmp_path)
    monkeypatch.setattr("campus_sim.matrix_v069._behavior_is_frozen", lambda sha: True)
    monkeypatch.setattr("campus_sim.matrix_v069._current_behavior_is_frozen", lambda: True)
    for condition in spec["conditions"]:
        for seed in spec["seeds"]:
            path = tmp_path / _name("rule", condition, seed)
            path.mkdir()
            _, digest = _config(condition, seed, "rule")
            summary = {"status": "complete_steps_only", "complete_steps": 10,
                       "seed": seed, "condition": condition, "n_students": 40,
                       "n_staff": 2, "config_sha256": digest, "driver": "rule",
                       "model_provider": None, "model_id": None, "scenario_git": "frozen",
                       "trajectory_profiles": {"window_days": 7, "eligible_students": 40,
                           "trajectory_type_counts": {"no_social_contact": 40},
                           "isolation_status_counts": {"no_contact_despite_opportunity": 40}},
                       "network_calls": 0, "requested_rate": 0.1,
                       "supported_rate": 0.05, "low_connection_7d_rate": 0.5,
                       "stress_distribution": {"mean": 0.3},
                       "mood_distribution": {"mean": 0.7},
                       "action_counts": {}, "completed_wait_mean_days": None}
            (path / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
            profiles = [{"student_id": f"s{i:03d}", "trajectory_type": "no_social_contact",
                         "isolation_status": "no_contact_despite_opportunity"}
                        for i in range(40)]
            (path / "trajectory_profiles.jsonl").write_text(
                "\n".join(json.dumps(row) for row in profiles), encoding="utf-8")
    report = build_report(tmp_path)
    assert report["found_runs"] == 12
    assert report["paired_delta_stats"]["outreach"]["requested_rate"]["mean"] == 0
    path = tmp_path / _name("rule", "baseline", 11) / "summary.json"
    bad = json.loads(path.read_text(encoding="utf-8"))
    bad["config_sha256"] = "wrong"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="config_sha256"):
        build_report(tmp_path)
