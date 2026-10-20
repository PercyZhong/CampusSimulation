import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from campus_sim.web import build_run_config, list_run_summaries, run_name, save_run_config

PROJECT = Path(__file__).resolve().parents[1]


def payload(**overrides):
    data = {"condition": "targeted_outreach", "seed": 7, "n_students": 12,
            "n_staff": 3, "days": 8, "periods_per_day": 3, "capacity": 6,
            "open_every": 2, "access_barrier": 0.3, "outreach_day": 4}
    return {**data, **overrides}


def test_web_config_combines_template_and_bounded_controls():
    config = build_run_config(PROJECT, payload())
    assert (config["n_students"], config["n_staff"], config["end"]) == (12, 3, 8)
    assert config["policy_components"]["service"] == {"capacity": 6, "open_every": 2}
    assert config["policy_components"]["information_delivery"]["day"] == 4
    assert config["periods_per_day"] == 3


@pytest.mark.parametrize("change", [
    {"n_students": 101}, {"n_staff": 11}, {"days": 31},
    {"capacity": 10}, {"periods_per_day": 2},
])
def test_web_config_rejects_out_of_bounds_settings(change):
    with pytest.raises(ValueError):
        build_run_config(PROJECT, payload(**change))


def test_run_names_use_shanghai_minute_and_reserve_collisions(tmp_path):
    when = datetime(2026, 10, 20, 3, 3, tzinfo=timezone.utc)
    assert run_name("baseline", 11, when) == "baseline-seed11-202610201103"
    data = {"condition": "baseline", "seed": 11}
    first, first_path = save_run_config(tmp_path, data, when)
    second, second_path = save_run_config(tmp_path, data, when)
    assert first == "baseline-seed11-202610201103"
    assert second == "baseline-seed11-202610201103-2"
    assert json.loads(first_path.read_text(encoding="utf-8")) == data
    assert second_path.is_file()


def test_runs_sort_by_start_time_with_legacy_file_fallback(tmp_path):
    old = tmp_path / "baseline-seed11-legacy"
    old.mkdir()
    old_summary = old / "summary.json"
    old_summary.write_text('{"condition":"baseline"}', encoding="utf-8")
    newer = tmp_path / "outreach-seed11-202610201103"
    newer.mkdir()
    (newer / "summary.json").write_text('{"condition":"outreach"}', encoding="utf-8")
    (tmp_path / f"{newer.name}.status.json").write_text(json.dumps({
        "status": "complete", "started_at": old_summary.stat().st_mtime + 10,
    }), encoding="utf-8")
    assert [item["name"] for item in list_run_summaries(tmp_path)] == [newer.name, old.name]
