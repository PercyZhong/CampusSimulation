"""Provider selection and bounded JSON action requests for synthetic actors."""

import json
import os
import threading
import urllib.error
import urllib.request

PROVIDERS = {
    "deepseek": {"env": "DEEPSEEK_API_KEY", "models": ("deepseek-chat", "deepseek-reasoner")},
    "qwen": {"env": "DASHSCOPE_API_KEY", "models": ("qwen-plus", "qwen-turbo", "qwen-max")},
    "openai": {"env": "OPENAI_API_KEY", "models": ("gpt-4.1", "gpt-4.1-mini")},
    "gemini": {"env": "GEMINI_API_KEY", "models": ("gemini-2.5-flash", "gemini-2.5-pro")},
    "anthropic": {"env": "ANTHROPIC_API_KEY", "models": ("claude-sonnet-4-5", "claude-haiku-4-5")},
}
ACTION_NAMES = frozenset(("attend", "rest", "peer", "request", "accept_support"))
DECISION_PROMPT_VERSION = "peer-context-v1"
OPENAI_COMPATIBLE = {
    "deepseek": "https://api.deepseek.com/chat/completions",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    "openai": "https://api.openai.com/v1/chat/completions",
}


class HandshakeTimeout(RuntimeError):
    """The TLS handshake failed before an HTTP request could be sent."""


def choices():
    return {name: {"models": info["models"], "key_env": info["env"],
                   "configured": bool(os.environ.get(info["env"]))}
            for name, info in PROVIDERS.items()}


def validate(provider, model, max_calls):
    if provider not in PROVIDERS:
        raise ValueError("unknown model provider")
    if not isinstance(model, str) or not model or len(model) > 100 or not all(
        char.isalnum() or char in "._-" for char in model
    ):
        raise ValueError("invalid model id")
    if type(max_calls) is not int or max_calls < 1:
        raise ValueError("max_model_calls must be a positive integer")


def require_key(provider):
    name = PROVIDERS[provider]["env"]
    key = os.environ.get(name)
    if not key:
        raise ValueError(f"{name} is required for {provider}")
    return key


def _post(url, headers, payload):
    request = urllib.request.Request(url, json.dumps(payload).encode("utf-8"),
                                     {"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"model API returned HTTP {error.code}") from None
    except (urllib.error.URLError, TimeoutError) as error:
        reason = error.reason if hasattr(error, "reason") else error
        if "handshake" in str(reason).lower() and "timed out" in str(reason).lower():
            raise HandshakeTimeout("model API TLS handshake timed out") from None
        raise RuntimeError(f"model API connection failed: {reason}") from None


def _content(response, provider):
    try:
        if provider == "anthropic":
            return "".join(block["text"] for block in response["content"]
                           if block["type"] == "text"), response.get("usage", {})
        if provider == "gemini":
            parts = response["candidates"][0]["content"]["parts"]
            return "".join(part.get("text", "") for part in parts), response.get("usageMetadata", {})
        return response["choices"][0]["message"]["content"], response.get("usage", {})
    except (KeyError, IndexError, TypeError) as error:
        raise RuntimeError("model API response has no text") from error


def _action(text, allowed=None):
    if not isinstance(text, str):
        raise TypeError("model returned no text")
    value = text.strip()
    if value.startswith("```"):
        value = value.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        data = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError("model response is not JSON") from error
    action = data.get("action") if isinstance(data, dict) else None
    if action not in ACTION_NAMES:
        raise ValueError("model returned unsupported action")
    if allowed is not None and action not in allowed:
        raise ValueError("model returned unavailable action")
    return action


class ModelClient:
    def __init__(self, provider, model, max_calls, temperature=0.2):
        validate(provider, model, max_calls)
        if type(temperature) not in (int, float) or not 0 <= temperature <= 2:
            raise ValueError("model_temperature must be in [0,2]")
        self.provider, self.model, self.max_calls = provider, model, max_calls
        self.temperature = temperature
        self.key = require_key(provider)
        self.calls = 0
        self.lock = threading.Lock()

    def _reserve_call(self):
        with self.lock:
            if self.calls >= self.max_calls:
                raise RuntimeError("model call limit reached")
            self.calls += 1

    def decide(self, *, day, student, course, service, peers, policy):
        self._reserve_call()
        system = ("You act as one fictional student in a synthetic campus simulation. "
                  "Choose exactly one action from policy.available_actions. "
                  "Return only JSON with an action field. No diagnosis or risk prediction. "
                  "Consider your current needs and recent_events, social_memory and "
                  "memory_context before choosing. Attend means participating in campus "
                  "activities. Rest means taking time to recover. Peer means starting a "
                  "specific four-turn conversation with a connected student; it can help "
                  "maintain social connection. When connection is low or recent peer contact "
                  "is absent, consider peer alongside other available actions. Do not default "
                  "to attend or choose peer by quota; choose what fits this student's state. "
                  "If pending_day is set, consider accept_support when available. "
                  "The service access_barrier affects whether request is available today; "
                  "outreach_seen records information actually received. "
                  "Policy is an experimental condition, not a diagnosis. Follow its "
                  "guidance when choosing among actions, while keeping actions plausible. "
                  "Service rules are enforced by the simulator.")
        user = json.dumps({"day": day, "student": student, "course": course,
                           "service": service, "peers": peers, "policy": policy}, ensure_ascii=False)
        provider = self.provider
        def send():
            if provider in OPENAI_COMPATIBLE:
                return _post(OPENAI_COMPATIBLE[provider],
                             {"Authorization": f"Bearer {self.key}"},
                             {"model": self.model, "temperature": self.temperature, "max_tokens": 80,
                              "messages": [{"role": "system", "content": system},
                                           {"role": "user", "content": user}]})
            if provider == "anthropic":
                return _post("https://api.anthropic.com/v1/messages",
                             {"x-api-key": self.key, "anthropic-version": "2023-06-01"},
                             {"model": self.model, "max_tokens": 80, "temperature": self.temperature,
                              "system": system, "messages": [{"role": "user", "content": user}]})
            return _post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
                {"x-goog-api-key": self.key},
                {"systemInstruction": {"parts": [{"text": system}]},
                 "contents": [{"role": "user", "parts": [{"text": user}]}],
                 "generationConfig": {"temperature": self.temperature, "maxOutputTokens": 80,
                                      "responseMimeType": "application/json"}})

        try:
            response = send()
        except HandshakeTimeout:
            self._reserve_call()
            response = send()
        content, usage = _content(response, provider)
        return _action(content, policy["available_actions"]), usage if isinstance(usage, dict) else {}
