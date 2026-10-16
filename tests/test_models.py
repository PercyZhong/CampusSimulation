"""Provider requests use a local fake; tests never contact a paid API."""

import json

import pytest

from campus_sim.domain import Config, policy_context
from campus_sim.models import (
    DECISION_PROMPT_VERSION,
    HandshakeTimeout,
    ModelClient,
    _action,
    choices,
)


@pytest.mark.parametrize("provider,key_name", [
    ("deepseek", "DEEPSEEK_API_KEY"),
    ("qwen", "DASHSCOPE_API_KEY"),
    ("openai", "OPENAI_API_KEY"),
    ("gemini", "GEMINI_API_KEY"),
    ("anthropic", "ANTHROPIC_API_KEY"),
])
def test_provider_request_and_limit(monkeypatch, provider, key_name):
    monkeypatch.setenv(key_name, "local-test-key")
    seen = []

    def fake_post(url, headers, payload):
        seen.append((url, headers, payload))
        if provider == "gemini":
            return {"candidates": [{"content": {"parts": [{"text": '{"action":"rest"}'}]}}],
                    "usageMetadata": {"totalTokenCount": 12}}
        if provider == "anthropic":
            return {"content": [{"type": "text", "text": '{"action":"rest"}'}],
                    "usage": {"input_tokens": 10, "output_tokens": 2}}
        return {"choices": [{"message": {"content": '{"action":"rest"}'}}],
                "usage": {"total_tokens": 12}}

    monkeypatch.setattr("campus_sim.models._post", fake_post)
    client = ModelClient(provider, choices()[provider]["models"][0], 1)
    action, usage = client.decide(day=1, student={"id": "s000"}, course={},
                                  service={}, peers={},
                                  policy={"name": "baseline", "available_actions": ["rest"]})
    assert action == "rest" and usage
    assert len(seen) == 1
    expected_header = ("Authorization", "Bearer local-test-key") if provider in {
        "deepseek", "qwen", "openai"
    } else (("x-api-key", "local-test-key") if provider == "anthropic" else
            ("x-goog-api-key", "local-test-key"))
    assert seen[0][1][expected_header[0]] == expected_header[1]
    with pytest.raises(RuntimeError, match="limit reached"):
        client.decide(day=2, student={}, course={}, service={}, peers={}, policy={})
    assert len(seen) == 1


def test_model_configuration_and_response(monkeypatch):
    with pytest.raises(ValueError, match="max_model_calls"):
        Config.parse({"driver": "llm", "model_provider": "qwen", "model_id": "qwen-plus"})
    with pytest.raises(ValueError, match="require llm driver"):
        Config.parse({"model_provider": "qwen"})
    with pytest.raises(ValueError, match="unsupported action"):
        _action('{"action":"delete_database"}')
    assert _action('```json\n{"action":"peer"}\n```') == "peer"
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        ModelClient("deepseek", "deepseek-chat", 1)
    with pytest.raises(ValueError, match="at least 200"):
        Config.parse({"end": 5, "n_students": 40, "driver": "llm",
                      "model_provider": "qwen", "model_id": "qwen-plus",
                      "max_model_calls": 199})
    with pytest.raises(ValueError, match="model_temperature"):
        Config.parse({"driver": "llm", "model_provider": "qwen", "model_id": "qwen-plus",
                      "max_model_calls": 30, "n_students": 1, "model_temperature": 2.1})
    student = {"id": "s000", "willingness": 0.5, "connection": 0.5,
               "outreach_seen": False, "pending_day": None, "supported": False}
    before = policy_context(Config(condition="outreach"), student, 1)
    assert not before["information_received"]
    assert before["effective_access_barrier"] == policy_context(Config(condition="baseline"), student, 1)[
        "effective_access_barrier"
    ]
    assert before["components"]["information_delivery"]["enabled"]
    student["outreach_seen"] = True
    assert policy_context(Config(condition="outreach"), student, 5)["information_received"]
    assert "更容易" in policy_context(Config(condition="easier_access"), student, 1)["intervention"]
    assert policy_context(Config(condition="baseline"), student, 1)["effective_access_barrier"] == 0.22
    with pytest.raises(ValueError, match="unavailable action"):
        _action('{"action":"request"}', ["attend", "rest"])


def test_handshake_timeout_retries_once_within_call_limit(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "local-test-key")
    attempts = []

    def fake_post(url, headers, payload):
        attempts.append(url)
        if len(attempts) == 1:
            raise HandshakeTimeout("model API TLS handshake timed out")
        return {"choices": [{"message": {"content": '{"action":"rest"}'}}]}

    monkeypatch.setattr("campus_sim.models._post", fake_post)
    client = ModelClient("qwen", "qwen-plus", 2)
    action, _ = client.decide(day=1, student={}, course={}, service={}, peers={},
                              policy={"available_actions": ["rest"]})
    assert action == "rest" and client.calls == len(attempts) == 2


def test_policy_changes_request_opportunity_with_shared_draws():
    student = {"id": "s000", "willingness": 0.5, "connection": 0.5,
               "outreach_seen": False, "pending_day": None, "supported": False}
    baseline = Config(condition="baseline")
    easier = Config(condition="easier_access")
    outreach = Config(condition="outreach")
    assert any(
        policy_context(easier, {**student, "id": f"s{number:03d}"}, day)["request_available_today"]
        and not policy_context(baseline, {**student, "id": f"s{number:03d}"}, day)["request_available_today"]
        for number in range(40) for day in range(1, 6)
    )
    for day in range(1, 31):
        normal = policy_context(baseline, student, day)
        easier_today = policy_context(easier, student, day)
        assert not normal["request_available_today"] or easier_today["request_available_today"]
        assert policy_context(outreach, student, day)["request_available_today"] == normal[
            "request_available_today"
        ]
    student["outreach_seen"] = True
    assert any(
        policy_context(outreach, {**student, "id": f"s{number:03d}"}, day)["request_available_today"]
        and not policy_context(baseline, {**student, "id": f"s{number:03d}"}, day)["request_available_today"]
        for number in range(40) for day in range(5, 10)
    )
    for day in range(5, 31):
        normal = policy_context(baseline, student, day)
        informed = policy_context(outreach, student, day)
        assert not normal["request_available_today"] or informed["request_available_today"]


def test_llm_actor_run_records_decisions_without_live_network(monkeypatch, tmp_path):
    import asyncio

    from society0.kernel.runner import run_plan

    from campus_sim.analysis import export
    from campus_sim.plan import build

    monkeypatch.setenv("DEEPSEEK_API_KEY", "local-test-key")
    requests = []

    def fake_post(url, headers, payload):
        requests.append(payload)
        return {"choices": [{"message": {"content": '{"action":"attend"}'}}],
                "usage": {"total_tokens": 9}}

    monkeypatch.setattr("campus_sim.models._post", fake_post)
    raw = {"end": 1, "n_students": 2, "driver": "llm", "model_provider": "deepseek",
           "model_id": "deepseek-chat", "max_model_calls": 2}
    output = tmp_path / "llm-run"
    asyncio.run(run_plan(output, build(raw)))
    summary = export(output)
    assert summary["complete_steps"] == 1
    assert summary["driver"] == "llm"
    assert summary["model_provider"] == "deepseek"
    assert summary["action_counts"]["model_decision:completed"] == 2
    assert summary["network_calls"] == summary["paid_model_calls"] == 2
    assert len(requests) == 2
    for payload in requests:
        assert "four-turn conversation" in payload["messages"][0]["content"]
        assert "Do not default to attend" in payload["messages"][0]["content"]
    events = [json.loads(line) for line in (output / "events.jsonl").read_text(
        encoding="utf-8").splitlines()]
    decisions = [event for event in events if event["action"] == "model_decision"]
    assert len(decisions) == 2
    for event in decisions:
        detail = event["detail"]
        assert detail["prompt_version"] == DECISION_PROMPT_VERSION
        assert "peer" in detail["decision_context"]["available_actions"]
        assert detail["decision_context"]["recent_peer_contacts"] == 0
        assert "student" not in detail["decision_context"]
