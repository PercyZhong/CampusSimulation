"""Replaceable, offline language layer for synthetic peer exchanges."""

from typing import Protocol

from .domain import unit


class LanguageBackend(Protocol):
    backend: str

    def peer_message(self, *, seed: int, day: int, actor: str) -> str: ...

    def peer_dialogue(self, *, seed: int, day: int, actor: str, partner: str) -> list[dict]: ...


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

    def peer_dialogue(self, *, seed, day, actor, partner):
        opening = self.peer_message(seed=seed, day=day, actor=actor)
        replies = (
            "We can compare notes after class.",
            "That sounds difficult. We can look for campus support together.",
            "Thanks for checking in. I will think about the next step.",
        )
        reply = replies[int(unit(seed, "peer_reply", day, actor, partner) * len(replies))]
        return [{"speaker": actor, "text": opening}, {"speaker": partner, "text": reply}]


def language_backend(name: str) -> LanguageBackend:
    if name == "offline":
        return OfflineLanguage()
    raise ValueError(f"unsupported language backend: {name}")
