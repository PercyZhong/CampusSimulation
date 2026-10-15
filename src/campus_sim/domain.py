"""Portable model rules. Values are synthetic model state, not clinical measures."""

import hashlib
import json
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Config:
    start: int = 1
    end: int = 30
    n_students: int = 40
    seed: int = 11
    condition: str = "baseline"
    capacity: int = 3
    open_every: int = 1
    access_barrier: float = 0.22
    outreach_day: int = 5
    n_staff: int = 2
    language_backend: str = "offline"
    driver: str = "rule"
    model_provider: str | None = None
    model_id: str | None = None
    max_model_calls: int = 0
    model_temperature: float = 0.2
    policy_components: dict = field(default_factory=dict)

    @classmethod
    def parse(cls, raw):
        allowed = set(cls.__dataclass_fields__) | {"release"}
        if set(raw) - allowed:
            raise ValueError(f"unknown config keys: {sorted(set(raw) - allowed)}")
        value = cls(**{key: raw[key] for key in cls.__dataclass_fields__ if key in raw})
        for key in ("start", "end", "n_students", "seed", "capacity", "open_every", "outreach_day", "n_staff"):
            if type(getattr(value, key)) is not int:
                raise ValueError(f"{key} must be integer")
        if value.start != 1 or value.end < value.start or value.n_students < 1:
            raise ValueError("invalid time range or population")
        if value.capacity < 1 or value.open_every < 1 or value.outreach_day < 1:
            raise ValueError("invalid service settings")
        if value.n_staff < 1 or value.capacity > value.n_staff * 3:
            raise ValueError("capacity exceeds configured staff slots")
        if value.language_backend not in {"offline"}:
            raise ValueError("unsupported language backend")
        if value.driver not in {"rule", "llm"}:
            raise ValueError("unsupported driver")
        if value.driver == "llm":
            from .models import validate
            validate(value.model_provider, value.model_id, value.max_model_calls)
            minimum_calls = value.n_students * (value.end - value.start + 1)
            if value.max_model_calls < minimum_calls:
                raise ValueError(
                    f"max_model_calls must be at least {minimum_calls} for this run"
                )
        elif value.model_provider is not None or value.model_id is not None or value.max_model_calls:
            raise ValueError("model settings require llm driver")
        if value.condition not in {"baseline", "outreach", "easier_access"}:
            raise ValueError("unknown condition")
        if type(value.access_barrier) not in (int, float) or not 0 <= value.access_barrier <= 1:
            raise ValueError("access_barrier must be in [0,1]")
        if type(value.model_temperature) not in (int, float) or not 0 <= value.model_temperature <= 2:
            raise ValueError("model_temperature must be in [0,2]")
        if not isinstance(value.policy_components, dict):
            raise TypeError("policy_components must be an object")
        unknown = set(value.policy_components) - {"information_delivery", "access", "service", "follow_up"}
        if unknown:
            raise ValueError(f"unknown policy components: {sorted(unknown)}")
        for name, component in value.policy_components.items():
            if not isinstance(component, dict):
                raise TypeError(f"policy component {name} must be an object")
        access = value.policy_components.get("access", {})
        if "barrier_multiplier" in access and (
                type(access["barrier_multiplier"]) not in (int, float)
                or not 0 <= access["barrier_multiplier"] <= 1):
            raise ValueError("barrier_multiplier must be in [0,1]")
        service = value.policy_components.get("service", {})
        for key in ("capacity", "open_every"):
            if key in service and (type(service[key]) is not int or service[key] < 1):
                raise ValueError(f"policy service {key} must be a positive integer")
        if service.get("capacity", value.capacity) > value.n_staff * 3:
            raise ValueError("policy service capacity exceeds configured staff slots")
        follow_up = value.policy_components.get("follow_up", {})
        if "days_after_support" in follow_up and (
                type(follow_up["days_after_support"]) is not int
                or follow_up["days_after_support"] < 1):
            raise ValueError("follow_up days_after_support must be a positive integer")
        return value


def policy_components(config):
    """Return explicit, auditable policy components with condition defaults."""
    defaults = {
        "information_delivery": {
            "enabled": config.condition == "outreach", "day": config.outreach_day},
        "access": {
            "barrier_multiplier": 0.45 if config.condition == "easier_access" else 1.0},
        "service": {"capacity": config.capacity, "open_every": config.open_every},
        "follow_up": {"enabled": False, "days_after_support": 3},
    }
    for name, values in config.policy_components.items():
        defaults[name].update(values)
    return defaults


def policy_context(config, student, day):
    """Explain the active intervention without exposing hidden student state."""
    components = policy_components(config)
    informed = components["information_delivery"]["enabled"] and student["outreach_seen"]
    if student["pending_day"] is not None or student["supported"]:
        actions = ["attend", "rest", "peer"]
        if student["pending_day"] is not None:
            actions.append("accept_support")
        opportunity = False
    else:
        barrier = config.access_barrier * components["access"]["barrier_multiplier"]
        propensity = 0.14 + 0.48 * student["willingness"] + 0.14 * (1 - student["connection"])
        if informed:
            propensity += 0.16
        opportunity = unit(config.seed, "request_opportunity", day, student["id"]) < clamp(
            propensity - barrier
        ) and day % components["service"]["open_every"] == 0
        actions = ["attend", "rest", "peer"]
        if opportunity:
            actions.append("request")
    barrier = config.access_barrier * components["access"]["barrier_multiplier"]
    common = {"request_available_today": "request" in actions,
              "available_actions": actions,
              "effective_access_barrier": round(barrier, 6)}
    if informed:
        return {**common, "name": "outreach",
                "intervention": "已收到主动关怀提供的一般支持服务信息。",
                "guidance": "收到信息后，可根据需要考虑校园支持服务。",
                "information_received": True, "components": components}
    if config.condition == "easier_access":
        return {**common, "name": "easier_access", "intervention": "咨询服务更容易获得；求助门槛降低。",
                "guidance": "当支持有帮助且门槛较低时，可以提出请求；也可以选择其他行动。",
                "information_received": False, "components": components}
    return {**common, "name": "baseline", "intervention": "现状规则；没有额外主动关怀，求助门槛保持基线。",
            "guidance": "根据当前状态自行判断是否请求支持或选择其他行动。",
            "information_received": False, "components": components}


def unit(seed, stream, *parts):
    """Independent named draws prevent policy branches shifting external events."""
    key = json.dumps([seed, stream, *parts], separators=(",", ":"), ensure_ascii=True)
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big") / 2**64


def clamp(value):
    return round(min(1.0, max(0.0, value)), 6)


def memory_context(events, day, limit=5):
    """Return auditable recency-weighted memory without deleting raw events."""
    weighted = []
    for event in events[-limit:]:
        event_day = event.get("day", day)
        age = max(0, day - event_day)
        weighted.append({**event, "recency_weight": round(1 / (1 + age), 6)})
    return weighted


def academic_event(seed, day):
    return round(0.04 + 0.12 * unit(seed, "academic_event", day), 6)


def next_stress(stress, recovery, load, supported):
    """Daily workload and recovery with bounded adaptation to sustained load."""
    return clamp(stress + load - 0.055 * recovery -
                 (0.025 if supported else 0) - 0.15 * stress)


def next_mood(mood, stress, recovery, connected):
    """Synthetic affect variable; not a clinical score."""
    return clamp(mood + 0.035 * recovery + (0.025 if connected else 0) - 0.06 * stress)


def action_choice(student, day, config):
    sid = student["id"]
    if student["pending_day"] is not None:
        return "accept_support"
    propensity = 0.14 + 0.48 * student["willingness"] + 0.14 * (1 - student["connection"])
    if config.condition == "outreach" and student["outreach_seen"]:
        propensity += 0.16
    barrier = config.access_barrier * (0.45 if config.condition == "easier_access" else 1)
    if unit(config.seed, "choice", day, sid) < clamp(propensity - barrier):
        return "request"
    draw = unit(config.seed, "other_choice", day, sid)
    return "peer" if draw < 0.24 else "rest" if draw < 0.46 else "attend"
