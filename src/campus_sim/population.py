"""Synthetic identities, initial states and undirected ring social ties."""

from .domain import unit


def make_population(config):
    rows = []
    for number in range(config.n_students):
        sid = f"s{number:03d}"
        connection = round(0.2 + 0.55 * unit(config.seed, "connection", sid), 6)
        rows.append({"id": sid, "stress": round(0.25 + 0.4 * unit(config.seed, "stress", sid), 6),
                     "recovery": round(0.35 + 0.5 * unit(config.seed, "recovery", sid), 6),
                     "connection": connection,
                     "willingness": round(0.2 + 0.6 * unit(config.seed, "willingness", sid), 6),
                     "supported": False, "pending_day": None, "wait_days": 0,
                     "low_streak": 0, "ever_requested": False, "ever_supported": False,
                     "outreach_seen": False, "initial_group": "lower" if connection < 0.475 else "higher"})
    edges = [(rows[i]["id"], rows[(i + 1) % len(rows)]["id"]) for i in range(len(rows))]
    return rows, edges
