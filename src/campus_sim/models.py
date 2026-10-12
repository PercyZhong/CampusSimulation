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
OPENAI_COMPATIBLE = {
    "deepseek": "https://api.deepseek.com/chat/completions",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
    "openai": "https://api.openai.com/v1/chat/completions",
}


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
        raise RuntimeError(f"model API connection failed: {error.reason if hasattr(error, 'reason') else error}") from None


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


def _action(text):
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
    return action


class ModelClient:
    def __init__(self, provider, model, max_calls):
        validate(provider, model, max_calls)
        self.provider, self.model, self.max_calls = provider, model, max_calls
        self.key = require_key(provider)
        self.calls = 0
        self.lock = threading.Lock()

    def decide(self, *, day, student, course, service, peers):
        with self.lock:
            if self.calls >= self.max_calls:
                raise RuntimeError("model call limit reached")
            self.calls += 1
        system = ("You act as one fictional student in a synthetic campus simulation. "
                  "Choose one action from attend, rest, peer, request, accept_support. "
                  "Return only JSON with an action field. No diagnosis or risk prediction. "
                  "If pending_day is set, choose accept_support. If supported is true, "
                  "do not choose request. The service access_barrier is lower when access "
                  "is easier; outreach_seen records general information received. "
                  "Service rules are enforced by the simulator.")
        user = json.dumps({"day": day, "student": student, "course": course,
                           "service": service, "peers": peers}, ensure_ascii=False)
        provider = self.provider
        if provider in OPENAI_COMPATIBLE:
            response = _post(OPENAI_COMPATIBLE[provider], {"Authorization": f"Bearer {self.key}"},
                             {"model": self.model, "temperature": 0, "max_tokens": 80,
                              "messages": [{"role": "system", "content": system},
                                           {"role": "user", "content": user}]})
        elif provider == "anthropic":
            response = _post("https://api.anthropic.com/v1/messages",
                             {"x-api-key": self.key, "anthropic-version": "2023-06-01"},
                             {"model": self.model, "max_tokens": 80, "temperature": 0,
                              "system": system, "messages": [{"role": "user", "content": user}]})
        else:
            response = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
                             {"x-goog-api-key": self.key},
                             {"systemInstruction": {"parts": [{"text": system}]},
                              "contents": [{"role": "user", "parts": [{"text": user}]}],
                              "generationConfig": {"temperature": 0, "maxOutputTokens": 80,
                                                   "responseMimeType": "application/json"}})
        content, usage = _content(response, provider)
        return _action(content), usage if isinstance(usage, dict) else {}
