import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from campus_sim import web
from campus_sim.analysis_agent import (
    batch_evidence,
    generate_report,
    run_evidence,
    save_report,
)
from campus_sim.models import ModelClient


def make_run(path, *, condition="baseline", seed=11, stress=0.3):
    path.mkdir(parents=True)
    summary = {"complete_steps": 2, "condition": condition, "seed": seed,
               "driver": "rule", "scenario_git": "abc", "config_sha256": "hash",
               "n_students": 2, "n_staff": 1, "requested_count": 1,
               "requested_rate": 0.5, "supported_count": 1, "supported_rate": 0.5,
               "low_connection_7d_rate": 0.0, "completed_wait_mean_days": 1.0,
               "stress_distribution": {"mean": stress},
               "mood_distribution": {"mean": 0.6},
               "trajectory_profiles": {"trajectory_type_counts": {
                   "mixed_or_connected": 1, "no_social_contact": 1}}}
    (path / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (path / "runner-status.json").write_text('{"complete_step": 2}', encoding="utf-8")
    (path / "daily_metrics.csv").write_text(
        f"day,mean_stress,mean_mood\n1,0.4,0.5\n2,{stress},0.6\n", encoding="utf-8")
    profiles = [{"student_id": "s000", "trajectory_type": "mixed_or_connected",
                 "isolation_status": "contact_observed"},
                {"student_id": "s001", "trajectory_type": "no_social_contact",
                 "isolation_status": "no_contact_despite_opportunity"}]
    (path / "trajectory_profiles.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in profiles), encoding="utf-8")
    events = [{"actor": actor, "day": day, "action": "student_state",
               "status": "completed", "detail": {"stress": stress, "mood": 0.6,
                                                  "connection": 0.4}}
              for day in (1, 2) for actor in ("s000", "s001")]
    events.append({"actor": "s000", "day": 1, "action": "peer",
                   "status": "completed", "detail": {}})
    (path / "events.jsonl").write_text(
        "".join(json.dumps(item) + "\n" for item in events), encoding="utf-8")
    return summary


def test_run_evidence_checks_daily_values_and_includes_contrasting_paths(tmp_path):
    path = tmp_path / "baseline-seed11"
    make_run(path)
    packet = run_evidence(path)
    assert packet["metadata"]["seed"] == 11
    assert {item["label"] for item in packet["evidence"]} >= {
        "求助率", "逐日平均压力首末变化", "学生 s000 的轨迹片段",
        "学生 s001 的轨迹片段"}
    (path / "daily_metrics.csv").write_text(
        "day,mean_stress,mean_mood\n1,0.4,0.5\n2,0.8,0.6\n", encoding="utf-8")
    with pytest.raises(ValueError, match="disagrees"):
        run_evidence(path)


def test_run_evidence_includes_class_role_examples(tmp_path):
    path = tmp_path / "class-support"
    summary = make_run(path)
    summary.update(class_support_enabled=True, class_count=1, teacher_count=1,
                   committee_count=1, academic_check_in_count=1,
                   peer_support_offer_count=1, professional_referral_offer_count=1)
    (path / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    events = [
        {"actor": "t000", "day": 2, "action": "teacher_review", "status": "completed",
         "detail": {"student_id": "s001"}},
        {"actor": "s000", "day": 2, "action": "committee_check_in", "status": "completed",
         "detail": {"student_id": "s001"}},
        {"actor": "c000", "day": 2, "action": "professional_referral_offer",
         "status": "offered", "detail": {"student_id": "s001"}},
    ]
    with (path / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.writelines(json.dumps(event) + "\n" for event in events)
    packet = run_evidence(path)
    examples = [item for item in packet["evidence"]
                if item["label"].endswith("实例")]
    assert len(examples) == 3
    assert all(item["value"]["student_id"] == "s001" for item in examples)


def test_batch_evidence_requires_comparable_complete_pairs(tmp_path):
    batch = tmp_path / "batch-12345678"
    make_run(batch / "baseline-rule-seed11")
    make_run(batch / "outreach-rule-seed11", condition="outreach", stress=0.2)
    shared = {"seed": 11, "n_students": 2, "n_staff": 1, "end": 2,
              "capacity": 1, "open_every": 1, "access_barrier": 0.22,
              "outreach_day": 2, "periods_per_day": 1, "driver": "rule"}
    plan = {"conditions": ["baseline", "outreach"], "seeds": [11],
            "runs": [{"name": f"{name}-rule-seed11",
                      "config": {**shared, "condition": name}}
                     for name in ("baseline", "outreach")]}
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    packet = batch_evidence(batch, plan_path)
    delta = next(item for item in packet["evidence"]
                 if item["label"] == "outreach 相对 baseline：末日平均压力同种子配对差")
    assert delta["value"]["mean"] == -0.1
    assert any("trajectory_profiles.jsonl" in item["source"]
               for item in packet["evidence"])
    plan["runs"][1]["config"]["n_students"] = 3
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(ValueError, match="planned configuration"):
        batch_evidence(batch, plan_path)


def test_report_marks_unsupported_claims_and_never_saves_key(tmp_path, monkeypatch):
    packet = {
        "kind": "run", "name": "example", "metadata": {},
        "evidence": [{"id": "E1", "label": "求助率", "value": 0.5,
                      "source": "summary.json"}]}
    draft = {"findings": [
        {"title": "求助行为存在", "interpretation": "证据记录了求助行为。",
         "evidence_ids": ["E1"]},
        {"title": "第2天改善", "interpretation": "这个数字未经证实。",
         "evidence_ids": ["E99"]}],
        "recommendations": [{"action": "测试服务触达", "rationale": "可检验触达变化。",
                             "verification": "比较后续实验。", "evidence_ids": ["E1"]}],
        "limitations": ["只有合成数据。"]}
    def fake_request(self, *_):
        self.calls += 1
        return json.dumps(draft, ensure_ascii=False), {"tokens": 20}

    monkeypatch.setattr(ModelClient, "_request", fake_request)
    report = generate_report(packet, "qwen", "qwen-plus", api_key="private-key")
    assert report["findings"][0]["status"] == "evidence_linked"
    assert report["findings"][1]["status"] == "hypothesis"
    assert report["findings"][1]["evidence_ids"] == []
    saved = save_report(tmp_path, report)
    assert "private-key" not in saved.read_text(encoding="utf-8")
    assert report["analysis_model"]["calls"] == 1


def test_web_analysis_endpoint_saves_report_without_key(tmp_path, monkeypatch):
    make_run(tmp_path / "baseline-seed11")
    monkeypatch.setattr(web, "generate_report", lambda packet, provider, model, **kwargs: {
        "scope": {"kind": packet["kind"], "name": packet["name"]},
        "analysis_model": {"provider": provider, "model": model},
        "evidence": packet["evidence"], "findings": [], "recommendations": [],
        "limitations": [], "prompt_version": "test", "audit_note": "test"})
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.create_handler(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        body = json.dumps({"kind": "run", "name": "baseline-seed11",
                           "provider": "qwen", "model": "qwen-plus",
                           "api_key": "private-key"}).encode()
        request = urllib.request.Request(base + "/api/analysis", data=body,
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request) as response:
            assert response.status == 202
        for _ in range(40):
            with urllib.request.urlopen(base + "/api/analysis/run/baseline-seed11") as response:
                status = json.load(response)
            if status["status"] == "complete":
                break
            time.sleep(0.05)
        assert status["status"] == "complete"
        assert status["result"]["scope"]["name"] == "baseline-seed11"
        assert "private-key" not in json.dumps(status)
        assert (tmp_path / status["report"]).is_file()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
