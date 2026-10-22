import json
import threading
import time
import urllib.request
from datetime import UTC, datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from campus_sim import web
from campus_sim.web import (
    build_batch_plan,
    build_run_config,
    list_run_summaries,
    read_job_progress,
    read_run_detail,
    run_name,
    save_run_config,
)

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
    assert build_run_config(PROJECT, payload(class_support_enabled=True))[
        "class_support_enabled"] is True


def test_web_config_requires_boolean_class_support_switch():
    with pytest.raises(TypeError, match="class_support_enabled"):
        build_run_config(PROJECT, payload(class_support_enabled="true"))


@pytest.mark.parametrize("change", [
    {"n_students": 101}, {"n_staff": 11}, {"days": 31},
    {"capacity": 10}, {"periods_per_day": 2},
])
def test_web_config_rejects_out_of_bounds_settings(change):
    with pytest.raises(ValueError):
        build_run_config(PROJECT, payload(**change))


def test_batch_plan_uses_selected_policies_seeds_and_shared_controls():
    data = payload(conditions=["baseline", "targeted_outreach"], seeds=[7, 19])
    plan = build_batch_plan(PROJECT, data)
    assert [run["name"] for run in plan["runs"]] == [
        "baseline-rule-seed7", "baseline-rule-seed19",
        "targeted_outreach-rule-seed7", "targeted_outreach-rule-seed19",
    ]
    assert all(run["config"]["n_students"] == 12 for run in plan["runs"])
    assert all(run["config"]["end"] == 8 for run in plan["runs"])
    assert [run["config"]["seed"] for run in plan["runs"]] == [7, 19, 7, 19]


@pytest.mark.parametrize("overrides", [
    {"conditions": []}, {"conditions": ["baseline", "baseline"]},
    {"conditions": ["unknown"]}, {"seeds": []}, {"seeds": [11, 11]},
    {"seeds": list(range(11))}, {"seeds": [True]},
])
def test_batch_plan_rejects_invalid_dimensions(overrides):
    with pytest.raises(ValueError):
        build_batch_plan(PROJECT, payload(**{
            "conditions": ["baseline"], "seeds": [11], **overrides,
        }))


def test_online_key_is_used_only_for_validation_and_not_saved(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    data = payload(conditions=["baseline"], seeds=[11], provider="deepseek",
                   model="deepseek-chat", max_model_calls=300)
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        build_batch_plan(PROJECT, data)
    plan = build_batch_plan(PROJECT, data, api_key="secret-for-this-run")
    assert "secret-for-this-run" not in json.dumps(plan)
    assert plan["runs"][0]["config"]["model_provider"] == "deepseek"
    assert plan["runs"][0]["name"] == "baseline-llm-deepseek-deepseek-chat-seed11"


def test_online_key_reaches_only_child_environment_not_saved_configs(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    captured = []
    started = threading.Event()

    def fake_run(command, **kwargs):
        captured.append((command, kwargs["env"]["DEEPSEEK_API_KEY"]))
        started.set()
        return SimpleNamespace(returncode=1, stderr="failed: secret-for-this-run")

    monkeypatch.setattr(web.subprocess, "run", fake_run)
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.create_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        data = payload(conditions=["baseline"], seeds=[11], provider="deepseek",
                       model="deepseek-chat", max_model_calls=300,
                       api_key="secret-for-this-run")
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/batches",
            json.dumps(data).encode("utf-8"), {"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            name = json.load(response)["name"]
        assert started.wait(5)
        plan = json.loads((tmp_path / "configs" / f"{name}.json").read_text(encoding="utf-8"))
        assert "secret-for-this-run" not in json.dumps(plan)
        assert captured[0][1] == "secret-for-this-run"
        assert "secret-for-this-run" not in str(captured[0][0])
        status_file = tmp_path / f"{name}.status.json"
        for _ in range(100):
            if '"status": "failed"' in status_file.read_text(encoding="utf-8"):
                break
            time.sleep(0.01)
        assert "secret-for-this-run" not in status_file.read_text(encoding="utf-8")
        started.clear()
        single = payload(provider="deepseek", model="deepseek-chat",
                         max_model_calls=300, api_key="secret-for-this-run")
        request = urllib.request.Request(
            f"http://127.0.0.1:{server.server_port}/api/runs",
            json.dumps(single).encode("utf-8"), {"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            single_name = json.load(response)["name"]
        assert started.wait(5)
        saved = (tmp_path / "configs" / f"{single_name}.json").read_text(encoding="utf-8")
        assert "secret-for-this-run" not in saved
        assert captured[1][1] == "secret-for-this-run"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_single_run_progress_uses_completed_society0_steps(tmp_path):
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "single.json").write_text('{"end": 10}', encoding="utf-8")
    run = tmp_path / "single"
    run.mkdir()
    assert read_job_progress(tmp_path, "single", "running")["percent"] == 0
    (run / "runner-status.json").write_text('{"complete_step": 3}', encoding="utf-8")
    progress = read_job_progress(tmp_path, "single", "running")
    assert (progress["percent"], progress["completed"], progress["total"]) == (30, 3, 10)
    (run / "runner-status.json").write_text('{"complete_step": 10}', encoding="utf-8")
    assert read_job_progress(tmp_path, "single", "running")["percent"] == 99
    assert read_job_progress(tmp_path, "single", "complete")["percent"] == 100


def test_batch_progress_aggregates_completed_days_across_runs(tmp_path):
    configs = tmp_path / "configs"
    configs.mkdir()
    plan = {"runs": [{"name": "baseline-seed11", "config": {"end": 4}},
                     {"name": "outreach-seed11", "config": {"end": 6}}]}
    (configs / "batch-demo.json").write_text(json.dumps(plan), encoding="utf-8")
    for name, done in (("baseline-seed11", 4), ("outreach-seed11", 2)):
        run = tmp_path / "batch-demo" / name
        run.mkdir(parents=True)
        (run / "runner-status.json").write_text(
            json.dumps({"complete_step": done}), encoding="utf-8"
        )
    progress = read_job_progress(tmp_path, "batch-demo", "running", batch=True)
    assert progress == {"percent": 60, "completed": 6, "total": 10,
                        "finished_runs": 1, "run_count": 2}


def test_runs_endpoint_exposes_live_progress(tmp_path):
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "single.json").write_text('{"end": 10}', encoding="utf-8")
    (tmp_path / "single.status.json").write_text('{"status": "running"}', encoding="utf-8")
    run = tmp_path / "single"
    run.mkdir()
    (run / "runner-status.json").write_text('{"complete_step": 4}', encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.create_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/api/runs", timeout=5
        ) as response:
            data = json.load(response)
        assert data["jobs"][0]["progress"]["percent"] == 40
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_deleted_record_stays_hidden_while_experiment_file_remains(tmp_path):
    name = "baseline-rule-seed11-202610201103"
    run = tmp_path / name
    run.mkdir()
    summary = run / "summary.json"
    summary.write_text('{"condition": "baseline"}', encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.create_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with urllib.request.urlopen(base + "/api/runs", timeout=5) as response:
            assert json.load(response)["runs"][0]["name"] == name
        request = urllib.request.Request(
            base + "/api/hidden-runs", json.dumps({"names": [name]}).encode("utf-8"),
            {"Content-Type": "application/json", "Host": "127.0.0.1:18766",
             "Origin": "http://127.0.0.1:18766"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            assert json.load(response)["hidden_count"] == 1
        with urllib.request.urlopen(base + "/api/runs", timeout=5) as response:
            assert json.load(response)["runs"] == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert summary.is_file()
    assert web.read_hidden_names(tmp_path) == {name}


def test_llm_model_name_with_dot_is_a_valid_run_route(tmp_path):
    name = "baseline-llm-openai-gpt-4.1-seed11-202610201103"
    run = tmp_path / name
    run.mkdir()
    (run / "summary.json").write_text('{"condition": "baseline"}', encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.create_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/api/runs/{name}", timeout=5
        ) as response:
            assert json.load(response)["summary"]["condition"] == "baseline"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_hiding_batch_parent_also_hides_its_child_runs(tmp_path):
    child = tmp_path / "batch-demo" / "baseline-rule-seed11"
    child.mkdir(parents=True)
    (child / "summary.json").write_text('{"condition": "baseline"}', encoding="utf-8")
    (tmp_path / "batch-demo.status.json").write_text(
        '{"status": "complete"}', encoding="utf-8"
    )
    web.hide_record_names(tmp_path, ["batch-demo"])
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.create_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{server.server_port}/api/runs", timeout=5
        ) as response:
            data = json.load(response)
        assert data["runs"] == []
        assert data["batches"] == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert (child / "summary.json").is_file()


def test_run_names_use_shanghai_minute_and_reserve_collisions(tmp_path):
    when = datetime(2026, 10, 20, 3, 3, tzinfo=UTC)
    assert run_name("baseline", 11, when) == "baseline-rule-seed11-202610201103"
    data = {"condition": "baseline", "seed": 11}
    first, first_path = save_run_config(tmp_path, data, when)
    second, second_path = save_run_config(tmp_path, data, when)
    assert first == "baseline-rule-seed11-202610201103"
    assert second == "baseline-rule-seed11-202610201103-2"
    assert json.loads(first_path.read_text(encoding="utf-8")) == data
    assert second_path.is_file()
    llm = {"driver": "llm", "model_provider": "openai", "model_id": "gpt-4.1"}
    assert run_name("baseline", 11, when, config=llm) == (
        "baseline-llm-openai-gpt-4.1-seed11-202610201103"
    )


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


def test_run_detail_accepts_blank_daily_metrics(tmp_path):
    (tmp_path / "summary.json").write_text('{"condition":"baseline"}', encoding="utf-8")
    (tmp_path / "daily_metrics.csv").write_text(
        "day,mean_stress,optional,source\n1,0.25,,rule\n", encoding="utf-8"
    )
    detail = read_run_detail(tmp_path)
    assert detail["summary"] == {"condition": "baseline"}
    assert detail["days"] == [{"day": 1.0, "mean_stress": 0.25,
                               "optional": None, "source": "rule"}]


def test_run_detail_keeps_summary_when_daily_metrics_are_missing(tmp_path):
    (tmp_path / "summary.json").write_text('{"condition":"baseline"}', encoding="utf-8")
    detail = read_run_detail(tmp_path)
    assert detail["summary"] == {"condition": "baseline"}
    assert detail["days"] == []
    assert "daily_metrics_error" in detail
