"""Replaceable, offline language layer for synthetic peer exchanges."""

from typing import Protocol

from .domain import unit


class LanguageBackend(Protocol):
    backend: str

    def peer_message(self, *, seed: int, day: int, actor: str) -> str: ...

    def peer_dialogue(self, *, seed: int, day: int, actor: str, partner: str,
                      actor_state: dict | None = None, partner_state: dict | None = None,
                      shared_memory: list[dict] | None = None) -> list[dict]: ...


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

    def peer_dialogue(self, *, seed, day, actor, partner, actor_state=None,
                      partner_state=None, shared_memory=None):
        actor_state = actor_state or {}
        partner_state = partner_state or {}
        shared_memory = shared_memory or []
        if actor_state.get("stress", 0.5) >= 0.65:
            opening = "最近压力有点大，我想找个办法把今天的任务拆开。"
        elif actor_state.get("connection", 0.5) < 0.3:
            opening = "我这几天和大家联系不多，想先聊聊课程和安排。"
        else:
            opening = self.peer_message(seed=seed, day=day, actor=actor)
        if partner_state.get("stress", 0.5) >= 0.65:
            reply = "我也有点忙乱，我们可以一起列出下一步。"
        elif partner_state.get("mood", 0.5) >= 0.7:
            reply = "可以，我们先比较一下笔记，再决定下一步。"
        else:
            reply = "谢谢你来聊聊。我们可以一起看看校园支持服务。"
        follow = ("上次我们聊过类似的安排，我记得你想先完成最紧急的一项。"
                  if shared_memory else "那我们先约好课后再互相确认一次。")
        closing = "好，我会先做这一小步，之后再告诉你进展。"
        return [{"speaker": actor, "text": opening}, {"speaker": partner, "text": reply},
                {"speaker": actor, "text": follow}, {"speaker": partner, "text": closing}]


def language_backend(name: str) -> LanguageBackend:
    if name == "offline":
        return OfflineLanguage()
    raise ValueError(f"unsupported language backend: {name}")
