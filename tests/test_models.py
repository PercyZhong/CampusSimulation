"""Provider requests use a local fake; tests never contact a paid API."""

import pytest

from campus_sim.domain import Config
from campus_sim.models import HandshakeTimeout, ModelClient, _action, choices


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
                                  service={}, peers={})
    assert action == "rest" and usage
    assert len(seen) == 1
    expected_header = ("Authorization", "Bearer local-test-key") if provider in {
        "deepseek", "qwen", "openai"
    } else (("x-api-key", "local-test-key") if provider == "anthropic" else
            ("x-goog-api-key", "local-test-key"))
    assert seen[0][1][expected_header[0]] == expected_header[1]
    with pytest.raises(RuntimeError, match="limit reached"):
        client.decide(day=2, student={}, course={}, service={}, peers={})
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
    action, _ = client.decide(day=1, student={}, course={}, service={}, peers={})
    assert action == "rest" and client.calls == len(attempts) == 2


def test_llm_actor_run_records_decisions_without_live_network(monkeypatch, tmp_path):
    import asyncio

    from society0.kernel.runner import run_plan

    from campus_sim.analysis import export
    from campus_sim.plan import build

    monkeypatch.setenv("DEEPSEEK_API_KEY", "local-test-key")
    requests = []

    def fake_post(url, headers, payload):
        requests.append(payload)
        return {"choices": [{"message": {"content": '{"action":"request"}'}}],
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
