"""Portable model rules. Values are synthetic model state, not clinical measures."""

import hashlib
import json
from dataclasses import dataclass


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
        if value.condition not in {"baseline", "outreach", "easier_access"}:
            raise ValueError("unknown condition")
        if type(value.access_barrier) not in (int, float) or not 0 <= value.access_barrier <= 1:
            raise ValueError("access_barrier must be in [0,1]")
        return value


def unit(seed, stream, *parts):
    """Independent named draws prevent policy branches shifting external events."""
    key = json.dumps([seed, stream, *parts], separators=(",", ":"), ensure_ascii=True)
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big") / 2**64


def clamp(value):
    return round(min(1.0, max(0.0, value)), 6)


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
