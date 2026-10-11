"""Replaceable, offline language layer for synthetic peer exchanges."""

from typing import Protocol

from .domain import unit


class LanguageBackend(Protocol):
    backend: str

    def peer_message(self, *, seed: int, day: int, actor: str) -> str: ...


class OfflineLanguage:
    backend = "offline"
    templates = (
        "这周任务有点多，我打算先休息一下。",
        "谢谢关心，我会考虑找校园支持服务聊聊。",
        "今天想和朋友聊聊课程安排。",
    )

    def peer_message(self, *, seed, day, actor):
        choice = int(unit(seed, "peer_language", day, actor) * len(self.templates))
        return self.templates[choice]


def language_backend(name: str) -> LanguageBackend:
    if name == "offline":
        return OfflineLanguage()
    raise ValueError(f"unsupported language backend: {name}")
