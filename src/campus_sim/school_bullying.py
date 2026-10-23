"""Synthetic peer-harm exposure and bounded teacher responses."""

from .domain import clamp, unit

STRATEGIES = ("none", "private_check_in", "classroom_norms", "safety_plan")
INCIDENT_TYPES = ("verbal_harassment", "social_exclusion")


def aggressor_for_class(group):
    """Pick a stable synthetic peer distinct from the designated target."""
    if len(group) < 2:
        return None
    return group[1]["id"] if len(group) >= 3 else group[0]["id"]


def incident_occurs(seed, day, target):
    multiplier = (target["bullying_prevention_multiplier"]
                  if day <= target["bullying_protection_until"] else 1.0)
    return unit(seed, "bullying_incident", day, target["id"]) < 0.58 * multiplier


def incident_type(seed, day, target_id):
    return INCIDENT_TYPES[int(unit(seed, "bullying_type", day, target_id) * 2)]


def _apply(state, deltas):
    before = {key: state[key] for key in deltas}
    for key, delta in deltas.items():
        state[key] = round(clamp(state[key] + delta), 6)
    return {"before": before, "after": {key: state[key] for key in deltas}}


def apply_incident(state):
    return _apply(state, {"self_esteem": -0.055, "safety": -0.07,
                          "belonging": -0.045, "stress": 0.04, "mood": -0.025})


def apply_teacher_response(state, strategy, day):
    """Apply a rule-defined hypothetical response, never a clinical treatment."""
    effects = {
        "private_check_in": {"self_esteem": 0.025, "safety": 0.018,
                             "belonging": 0.018, "stress": -0.008},
        "classroom_norms": {"self_esteem": 0.018, "safety": 0.03,
                            "belonging": 0.026, "stress": -0.012},
        "safety_plan": {"self_esteem": 0.035, "safety": 0.055,
                        "belonging": 0.025, "stress": -0.02},
    }
    if strategy == "none":
        return None
    change = _apply(state, effects[strategy])
    if strategy in {"classroom_norms", "safety_plan"}:
        state["bullying_protection_until"] = day + 3
        state["bullying_prevention_multiplier"] = (
            0.55 if strategy == "classroom_norms" else 0.25)
    return change
