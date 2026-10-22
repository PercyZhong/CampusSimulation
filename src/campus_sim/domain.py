"""Portable model rules. Values are synthetic model state, not clinical measures."""

import hashlib
import json
from dataclasses import dataclass, field

CLASS_SIZE = 5


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
    class_support_enabled: bool = False
    language_backend: str = "offline"
    driver: str = "rule"
    model_provider: str | None = None
    model_id: str | None = None
    max_model_calls: int = 0
    model_temperature: float = 0.2
    periods_per_day: int = 1
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
        if type(value.class_support_enabled) is not bool:
            raise TypeError("class_support_enabled must be boolean")
        if type(value.periods_per_day) is not int or value.periods_per_day not in (1, 3):
            raise ValueError("periods_per_day must be 1 or 3")
        if value.language_backend not in {"offline"}:
            raise ValueError("unsupported language backend")
        if value.driver not in {"rule", "llm"}:
            raise ValueError("unsupported driver")
        if value.driver == "llm":
            from .models import validate
            validate(value.model_provider, value.model_id, value.max_model_calls)
            minimum_calls = value.n_students * (value.end - value.start + 1) * value.periods_per_day
            if value.max_model_calls < minimum_calls:
                raise ValueError(
                    f"max_model_calls must be at least {minimum_calls} for this run"
                )
        elif value.model_provider is not None or value.model_id is not None or value.max_model_calls:
            raise ValueError("model settings require llm driver")
        if value.condition not in {"baseline", "outreach", "easier_access",
                                   "targeted_outreach"}:
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
        for name in ("information_delivery", "access"):
            component = value.policy_components.get(name, {})
            if "target_group" in component and (
                    type(component["target_group"]) is not str
                    or component["target_group"] not in {"all", "lower", "higher"}):
                raise ValueError(f"{name} target_group must be all, lower or higher")
            if ("duration_days" in component and component["duration_days"] is not None
                    and (type(component["duration_days"]) is not int
                         or component["duration_days"] < 1)):
                raise ValueError(f"{name} duration_days must be a positive integer")
        delivery = value.policy_components.get("information_delivery", {})
        if "day" in delivery and (type(delivery["day"]) is not int or delivery["day"] < 1):
            raise ValueError("information_delivery day must be a positive integer")
        if "enabled" in delivery and type(delivery["enabled"]) is not bool:
            raise TypeError("information_delivery enabled must be boolean")
        if "start_day" in access and (type(access["start_day"]) is not int
                                      or access["start_day"] < 1):
            raise ValueError("access start_day must be a positive integer")
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
            "enabled": config.condition in {"outreach", "targeted_outreach"},
            "day": config.outreach_day,
            "target_group": "lower" if config.condition == "targeted_outreach" else "all",
            "duration_days": None},
        "access": {
            "barrier_multiplier": 0.45 if config.condition == "easier_access" else 1.0,
            "start_day": 1, "duration_days": None, "target_group": "all"},
        "service": {"capacity": config.capacity, "open_every": config.open_every},
        "follow_up": {"enabled": False, "days_after_support": 3},
    }
    for name, values in config.policy_components.items():
        defaults[name].update(values)
    return defaults


def intervention_effects(config, student, day):
    """Compute actual exposure from fixed cohort and explicit time windows."""
    components = policy_components(config)
    delivery = components["information_delivery"]
    access = components["access"]

    def eligible(part):
        return (part["target_group"] == "all"
                or student.get("initial_group") == part["target_group"])
    information_active = (delivery["enabled"] and student.get("outreach_seen", False)
                          and eligible(delivery) and day >= delivery["day"]
                          and (delivery["duration_days"] is None
                               or day < delivery["day"] + delivery["duration_days"]))
    access_active = (access["barrier_multiplier"] < 1 and eligible(access)
                     and day >= access["start_day"]
                     and (access["duration_days"] is None
                          or day < access["start_day"] + access["duration_days"]))
    multiplier = access["barrier_multiplier"] if access_active else 1.0
    return {"information_active": information_active, "access_active": access_active,
            "effective_access_barrier": round(config.access_barrier * multiplier, 6)}


def policy_context(config, student, day):
    """Explain the active intervention without exposing hidden student state."""
    components = policy_components(config)
    effects = intervention_effects(config, student, day)
    informed = effects["information_active"]
    if student["pending_day"] is not None or student["supported"]:
        actions = ["attend", "rest", "peer"]
        if student["pending_day"] is not None:
            actions.append("accept_support")
        opportunity = False
    else:
        barrier = effects["effective_access_barrier"]
        propensity = 0.14 + 0.48 * student["willingness"] + 0.14 * (1 - student["connection"])
        if informed:
            propensity += 0.16
        opportunity = unit(config.seed, "request_opportunity", day, student["id"]) < clamp(
            propensity - barrier
        ) and day % components["service"]["open_every"] == 0
        actions = ["attend", "rest", "peer"]
        if opportunity:
            actions.append("request")
    barrier = effects["effective_access_barrier"]
    common = {"request_available_today": "request" in actions,
              "available_actions": actions,
              "effective_access_barrier": barrier,
              "intervention_effects": effects}
    if informed:
        return {**common, "name": "outreach",
                "intervention": "已收到主动关怀提供的一般支持服务信息。",
                "guidance": "收到信息后，可根据需要考虑校园支持服务。",
                "information_received": True, "components": components}
    if effects["access_active"] and components["access"]["barrier_multiplier"] < 1:
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


def signed_clamp(value):
    """Bound a confidence balance while preserving positive/negative evidence."""
    return round(min(1.0, max(-1.0, value)), 6)


def memory_context(events, day, limit=5):
    """Return auditable recency-weighted memory without deleting raw events."""
    weighted = []
    for event in events[-limit:]:
        event_day = event.get("day", day)
        age = max(0, day - event_day)
        weighted.append({**event, "recency_weight": round(1 / (1 + age), 6)})
    return weighted


def update_long_term_memory(state, event):
    """Update factual counts plus bounded, weighted social/event memories.

    Response history is retained in a small bounded list so conflicting
    responses remain auditable instead of being overwritten by ``last_response``.
    """
    memory = state.setdefault("long_term_memory", {"action_counts": {}, "partners": {}, "events": {}})
    memory.setdefault("events", {})
    action = event.get("action")
    if action:
        counts = memory["action_counts"]
        counts[action] = counts.get(action, 0) + 1
    partner = event.get("partner")
    if partner:
        partners = memory["partners"]
        trace = partners.setdefault(partner, {"contacts": 0, "last_day": event["day"],
                                               "salience": 0.0, "confidence": 0.0,
                                               "response_history": []})
        trace.setdefault("response_history", [])
        elapsed = max(0, event["day"] - trace.get("salience_day", trace["last_day"]))
        response = event.get("response")
        trace["salience"] = clamp(trace["salience"] * 0.9**elapsed
                                  + (0.3 if action == "peer" else 0.15))
        if action == "peer":
            trace["contacts"] += 1
        if response:
            trace["last_response"] = response
            trace["response_history"] = (trace["response_history"] +
                                           [{"day": event["day"], "response": response}])[-5:]
            trace["confidence"] = signed_clamp(trace.get("confidence", 0.0) * 0.75 +
                                                (0.25 if response == "accepted" else -0.25))
        trace["last_day"] = event["day"]
        trace["salience_day"] = event["day"]
    event_type = event.get("event_type")
    if event_type:
        item = memory["events"].setdefault(event_type, {"strength": 0.0,
                                                         "last_day": event["day"],
                                                         "count": 0})
        elapsed = max(0, event["day"] - item.get("last_day", event["day"]))
        item["strength"] = clamp(item["strength"] * 0.92**elapsed +
                                  float(event.get("impact", 0.2)))
        item["last_day"] = event["day"]
        item["count"] += 1
    return memory


def decay_long_term_memory(state, day):
    """Decay recall strength once per day while retaining auditable counts."""
    memory = state.setdefault("long_term_memory", {"action_counts": {}, "partners": {}, "events": {}})
    memory.setdefault("events", {})
    for trace in memory["partners"].values():
        elapsed = max(0, day - trace.get("salience_day", trace["last_day"]))
        trace["salience"] = clamp(trace["salience"] * 0.9**elapsed)
        trace["salience_day"] = day
    memory["partners"] = {partner: trace for partner, trace in memory["partners"].items()
                          if trace["salience"] >= 0.05}
    for item in memory["events"].values():
        elapsed = max(0, day - item.get("last_day", day))
        item["strength"] = clamp(item.get("strength", 0.0) * 0.92**elapsed)
        item["last_day"] = day
    memory["events"] = {key: item for key, item in memory["events"].items()
                        if item.get("strength", 0.0) >= 0.03}
    return memory


def update_long_term_state(state, *, action=None, outcome=None, day=None):
    """Apply coupled recovery/setback dynamics with bounded increments."""
    success = (outcome in {"accepted", "supported"} or
               (outcome == "completed" and action in {"attend", "peer", "accept_support"}))
    setback = outcome in {"rejected", "timeout", "failed"} or action in {"course_absence", "miss"}
    if success:
        state["recovery_streak"] = state.get("recovery_streak", 0) + 1
        state["setback_streak"] = 0
        state["support_failure_streak"] = 0 if action in {"accept_support", "peer"} else state.get("support_failure_streak", 0)
    elif setback:
        state["setback_streak"] = state.get("setback_streak", 0) + 1
        state["recovery_streak"] = 0
        if action in {"peer", "request"}:
            state["support_failure_streak"] = state.get("support_failure_streak", 0) + 1
    if action == "attend" and success:
        state["academic_success_streak"] = state.get("academic_success_streak", 0) + 1
    elif action == "course_absence":
        state["academic_success_streak"] = 0
    if action == "peer" and success:
        state["social_contact_streak"] = state.get("social_contact_streak", 0) + 1
    elif action in {"rest", "course_absence"}:
        state["social_contact_streak"] = 0
    recovery = min(0.006, 0.0015 * state.get("recovery_streak", 0))
    setback_amount = min(0.008, 0.0015 * state.get("setback_streak", 0))
    state["academic_engagement"] = clamp(state["academic_engagement"] +
        (recovery if action == "attend" else -setback_amount if action == "course_absence" else 0))
    state["self_efficacy"] = clamp(state["self_efficacy"] +
        (0.004 if action == "attend" and state.get("academic_success_streak", 0) >= 2 else -0.003 if action == "course_absence" else 0))
    state["belonging"] = clamp(state["belonging"] +
        (0.003 * (1 - state["belonging"]) if action == "peer" and success
         and state.get("social_contact_streak", 0) >= 2
         else -0.003 if action == "course_absence" and state.get("low_streak", 0) >= 2 else 0))
    state["help_seeking_belief"] = clamp(state["help_seeking_belief"] +
        (0.008 if action == "accept_support" and success else
         -0.006 if action in {"request", "peer"} and outcome in {"rejected", "timeout"} else 0))
    state["social_avoidance"] = clamp(state["social_avoidance"] +
        (-0.006 * state["social_avoidance"] if action == "peer" and success
         else 0.004 * (1 - state["social_avoidance"]) if setback else 0))
    if day is not None:
        state.setdefault("long_term_memory", {}).setdefault("state_updates", []).append(
            {"day": day, "action": action, "outcome": outcome})
        state["long_term_memory"]["state_updates"] = state["long_term_memory"]["state_updates"][-10:]
    return state


def academic_event(seed, day):
    return round(0.04 + 0.12 * unit(seed, "academic_event", day), 6)


def academic_context(seed, day, student_id):
    """Five-student cohorts share a daily course and place."""
    cohort = int(student_id[1:]) // CLASS_SIZE
    course = 100 + cohort * 4 + (day - 1) % 4
    venue = 1 + int(unit(seed, "course_venue", course) * 6)
    return {"course_id": f"course-{course}", "venue": f"building-{venue}",
            "section": f"S{cohort:02d}"}


def campus_calendar(seed, day, student_id):
    """Synthetic daily venue schedule; presence still requires an action."""
    course = academic_context(seed, day, student_id)
    weekday = (day - 1) % 7
    return {"day": day,
            "course": {**course, "slot": "morning", "open": weekday < 5,
                       "capacity": 5},
            "library": {"venue": "library-main", "slot": "afternoon",
                        "open": weekday < 6, "capacity": 20},
            "cafeteria": {"venue": "cafeteria-main", "slot": "midday",
                          "open": True, "capacity": 30},
            "club": {**club_context(student_id), "slot": "afternoon",
                     "open": weekday in (2, 5), "capacity": 5},
            "residence": {**residence_context(student_id), "slot": "evening",
                          "open": True, "capacity": 3}}


def residence_context(student_id):
    """Stable synthetic three-student residence groups."""
    return {"venue": f"residence-{int(student_id[1:]) // 3:02d}"}


def club_context(student_id):
    """Stable synthetic student club membership and meeting place."""
    club = int(student_id[1:]) // CLASS_SIZE
    return {"club_id": f"club-{club:02d}", "venue": f"student_center-{club % 4:02d}"}


def relationship_type(actor, partner):
    """Classify a stable synthetic social tie by shared campus context."""
    if residence_context(actor)["venue"] == residence_context(partner)["venue"]:
        return "roommate"
    if club_context(actor)["club_id"] == club_context(partner)["club_id"]:
        return "club_member"
    if academic_context(0, 1, actor)["section"] == academic_context(0, 1, partner)["section"]:
        return "study_partner"
    return "acquaintance"


def peer_response(seed, day, actor, partner, partner_state, relation="acquaintance"):
    """Return an auditable synthetic partner response before dialogue starts."""
    availability = 1.0 - 0.2 * max(0.0, partner_state.get("stress", 0.5) - 0.65)
    if partner_state.get("pending_day") is not None:
        availability -= 0.2
    availability += {"roommate": 0.08, "club_member": 0.04,
                     "study_partner": 0.03, "friend": 0.12}.get(relation, 0)
    draw = unit(seed, "peer_response", day, actor, partner)
    if draw >= clamp(availability):
        return "rejected" if draw < clamp(availability) + 0.08 else "timeout"
    return "accepted"


def scenario_context(seed, day, student):
    """Deterministic continuous campus pressures exposed to every decision."""
    recent = student.get("recent_events", [])
    attend_streak = 0
    for event in reversed(recent):
        if event.get("action") == "attend":
            attend_streak += 1
        else:
            break
    peer_recent = sum(event.get("action") == "peer" for event in recent)
    exam_days = (4 - ((day - 1) % 7)) % 7
    return {"exam_days_remaining": exam_days, "exam_period": exam_days <= 2,
            "consecutive_attendance": attend_streak,
            "missed_course_streak": student.get("missed_course_streak", 0),
            "recent_social_contacts": peer_recent,
            "low_social_signal": student.get("connection", 0.5) < 0.3 and peer_recent == 0,
            "help_waiting": student.get("pending_day") is not None,
            "post_support_recovery": (student.get("supported_day") is not None
                                       and day - student["supported_day"] <= 3)}


def next_stress(stress, recovery, load, supported):
    """Daily workload and recovery with bounded adaptation to sustained load."""
    # Keep a small floor while damping accumulated stress.  This avoids both
    # rapid collapse under light workload and saturation under sustained load.
    return clamp(stress + load - 0.055 * recovery -
                 (0.025 if supported else 0) - 0.15 * stress + 0.03)


def next_mood(mood, stress, recovery, connected):
    """Synthetic affect variable; not a clinical score."""
    positive = 0.035 * recovery + (0.025 if connected else 0)
    return clamp(mood + positive * (1 - mood) - 0.06 * stress * mood
                 + 0.04 * (0.5 - mood))


def action_choice(student, day, config):
    sid = student["id"]
    if student["pending_day"] is not None:
        return "accept_support"
    scenario = scenario_context(config.seed, day, student)
    propensity = 0.14 + 0.48 * student["willingness"] + 0.14 * (1 - student["connection"])
    propensity += 0.08 * (student.get("help_seeking_belief", 0.5) - 0.5)
    event_memory = student.get("long_term_memory", {}).get("events", {})
    setback_memory = event_memory.get("academic_setback", {}).get("strength", 0.0)
    support_memory = event_memory.get("support_success", {}).get("strength", 0.0)
    propensity += 0.04 * support_memory - 0.03 * setback_memory
    if scenario["exam_period"]:
        propensity += 0.025
    effects = intervention_effects(config, student, day)
    if effects["information_active"]:
        propensity += 0.16
    barrier = effects["effective_access_barrier"]
    if unit(config.seed, "choice", day, sid) < clamp(propensity - barrier):
        return "request"
    draw = unit(config.seed, "other_choice", day, sid)
    peer_limit = clamp(0.24 + 0.08 * (student.get("belonging", 0.5) - 0.5)
                       - 0.1 * (student.get("social_avoidance", 0.5) - 0.5)
                       + (0.03 if scenario["low_social_signal"] else 0))
    traces = student.get("long_term_memory", {}).get("partners", {}).values()
    memory_signal = sum(
        trace["salience"] * trace.get("confidence", (1 if trace.get("last_response") == "accepted" else -1))
        for trace in traces if trace.get("last_response") in {"accepted", "rejected", "timeout"})
    peer_limit = clamp(peer_limit + 0.04 * max(-1.0, min(1.0, memory_signal)))
    rest_limit = clamp(peer_limit + 0.22 + (0.04 if scenario["exam_period"] else 0))
    other = "peer" if draw < peer_limit else "rest" if draw < rest_limit else "attend"
    if other == "attend" and not campus_calendar(config.seed, day, sid)["course"]["open"]:
        return "rest"
    return other


def period_available_actions(student, day, period, config, service, course_slots=None):
    """Expose only actions compatible with the current campus time block."""
    actions = ["rest", "peer"]
    if period == "morning":
        course = campus_calendar(config.seed, day, student["id"])["course"]
        if course["open"] and (course_slots is None or course_slots > 0):
            actions.insert(0, "attend")
    elif period == "afternoon":
        if service["open"]:
            if (student["pending_day"] is not None and service["queue_turn"]
                    and service["slots_remaining"] > 0):
                actions.append("accept_support")
            elif (student["pending_day"] is None and not student["supported"]
                    and policy_context(config, student, day)["request_available_today"]):
                actions.append("request")
    elif period != "evening":
        raise ValueError(f"unknown period: {period}")
    if student.get("peer_attempt_day") == day:
        actions.remove("peer")
    if student.get("service_attempt_day") == day:
        actions = [item for item in actions if item not in {"request", "accept_support"}]
    return actions


def period_action_choice(student, day, period, config, service, course_slots=None):
    """Choose one feasible action per period without blocking daily life in a queue."""
    available = period_available_actions(student, day, period, config, service,
                                         course_slots)
    if period == "morning":
        if "attend" in available and unit(config.seed, "morning_attend", day, student["id"]) < clamp(
                0.55 + 0.3 * student["academic_engagement"] - 0.15 * student["stress"]):
            return "attend"
        return "rest" if student["stress"] >= 0.6 or "peer" not in available else "peer"
    if period == "afternoon":
        if "accept_support" in available:
            return "accept_support"
        if ("request" in available and unit(
                config.seed, "afternoon_request", day, student["id"]) < clamp(
                    0.22 + 0.45 * student["willingness"] +
                    0.18 * student["help_seeking_belief"])):
            return "request"
        return "rest" if student["stress"] >= 0.65 or "peer" not in available else "peer"
    if period == "evening":
        return "rest" if student["stress"] >= 0.45 or "peer" not in available else "peer"
    raise ValueError(f"unknown period: {period}")
