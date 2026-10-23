"""Deterministic synthetic social choices, not a validated SVO assessment."""

from .domain import clamp, unit


def svo_group(score):
    """Model-defined orientation bins; not a questionnaire classification."""
    if score >= 0.6:
        return "prosocial"
    if score < 0.4:
        return "competitive"
    return "individualistic"


def social_choice(seed, day, student, intervention):
    """Use matched random draws so an intervention can be compared by seed."""
    prosociality = student["svo_prosociality"]
    stress = student["stress"]
    malicious = clamp(0.04 + 0.16 * (1 - prosociality) + 0.07 * stress)
    cooperative = clamp(0.25 + 0.55 * prosociality - 0.08 * stress)
    if intervention == "anti_malicious":
        diverted = malicious * 0.75
        malicious *= 0.25
        cooperative = clamp(cooperative + diverted + 0.12)
    draw = unit(seed, "social_choice", day, student["id"])
    if draw < malicious:
        return "undermine"
    if draw < malicious + cooperative:
        return "cooperate"
    return "compete"


def apply_social_effect(student, choice):
    """Small, bounded model-state effects of a simulated daily choice."""
    delta = {
        "cooperate": {"connection": 0.018, "belonging": 0.012,
                      "mood": 0.012, "stress": -0.008},
        "compete": {"self_efficacy": 0.008, "stress": 0.008},
        "undermine": {"belonging": -0.02, "mood": -0.015,
                      "stress": 0.025, "social_avoidance": 0.012},
    }[choice]
    before = {key: student[key] for key in delta}
    for key, change in delta.items():
        student[key] = round(clamp(student[key] + change), 6)
    return {"before": before, "after": {key: student[key] for key in delta}}


def apply_partner_effect(partner, choice):
    """Cooperation helps a classmate; undermining harms the targeted classmate."""
    delta = {
        "cooperate": {"connection": 0.01, "belonging": 0.006,
                      "mood": 0.006, "stress": -0.004},
        "undermine": {"belonging": -0.012, "mood": -0.01,
                      "stress": 0.015, "social_avoidance": 0.008},
    }.get(choice)
    if delta is None:
        return None
    before = {key: partner[key] for key in delta}
    for key, change in delta.items():
        partner[key] = round(clamp(partner[key] + change), 6)
    return {"before": before, "after": {key: partner[key] for key in delta}}


def election_candidate(seed, day, voter, candidates, intervention):
    """Simulate a class ballot; intervention weakens strategic voting noise."""
    if len(candidates) == 1:
        return candidates[0]
    bias = 0.16 * (1 - voter["svo_prosociality"])
    if intervention == "anti_malicious":
        bias *= 0.25
    draw = unit(seed, "election_vote", day, voter["id"])
    return candidates[0] if draw < 0.5 + bias else candidates[1]
