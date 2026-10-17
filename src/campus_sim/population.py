"""Synthetic identities, initial states and undirected ring social ties."""

from .domain import unit


def make_population(config):
    rows = []
    for number in range(config.n_students):
        sid = f"s{number:03d}"
        connection = round(0.2 + 0.55 * unit(config.seed, "connection", sid), 6)
        stress = round(0.25 + 0.4 * unit(config.seed, "stress", sid), 6)
        rows.append({"id": sid, "stress": stress,
                     "initial_stress": stress,
                     "mood": round(0.3 + 0.4 * unit(config.seed, "mood", sid), 6),
                     "recovery": round(0.35 + 0.5 * unit(config.seed, "recovery", sid), 6),
                     "connection": connection,
                     "willingness": round(0.2 + 0.6 * unit(config.seed, "willingness", sid), 6),
                     "academic_engagement": round(0.35 + 0.5 * unit(config.seed, "engagement", sid), 6),
                     "self_efficacy": round(0.3 + 0.55 * unit(config.seed, "efficacy", sid), 6),
                     "belonging": round(0.25 + 0.6 * unit(config.seed, "belonging", sid), 6),
                     "help_seeking_belief": round(0.25 + 0.6 * unit(config.seed, "help_belief", sid), 6),
                     "social_avoidance": round(0.1 + 0.7 * unit(config.seed, "avoidance", sid), 6),
                     "supported": False, "pending_day": None, "wait_days": 0,
                     "supported_day": None, "follow_up_sent": False,
                     "low_streak": 0, "ever_requested": False, "ever_supported": False,
                     "missed_course_streak": 0,
                     "outreach_seen": False, "recent_events": [], "social_memory": [],
                     "long_term_memory": {"action_counts": {}, "partners": {}},
                     "initial_group": "lower" if connection < 0.475 else "higher"})
    edges = [(rows[i]["id"], rows[(i + 1) % len(rows)]["id"]) for i in range(len(rows))]
    return rows, edges


def make_staff(config):
    """Synthetic support staff who own service slots, never student records."""
    return [{"id": f"c{i:03d}", "role": "counselor", "slots_per_day": 3}
            for i in range(config.n_staff)]
