"""Trajectory counts use each student's events in order, including rejected choices."""

from campus_sim.analysis import ACTION_NAMES, trajectory_metrics


def event(ordinal, day, actor, action, status="completed", detail=None):
    return {"ordinal": ordinal, "day": day, "actor": actor, "action": action,
            "status": status, "detail": detail or {}}


def test_rule_paths_and_repeated_peer_relationships():
    events = [event(1, 1, "s000", "attend"),
              event(2, 1, "s001", "rest"),
              event(3, 2, "s000", "peer", detail={"partner": "s001"}),
              event(4, 2, "s001", "peer", detail={"partner": "s000"}),
              event(5, 3, "s000", "peer", detail={"partner": "s001"}),
              event(6, 3, "s001", "request", "rejected")]
    result = trajectory_metrics(events)
    matrix = result["trajectory_transition_matrix"]
    assert matrix["attend"]["peer"] == 1
    assert matrix["rest"]["peer"] == 1
    assert matrix["peer"]["peer"] == 1
    assert matrix["peer"]["request"] == 1
    assert sum(sum(row.values()) for row in matrix.values()) == 4
    assert result["trajectory_action_transitions"] == 3
    assert result["trajectory_longest_same_action_streak"] == 2
    assert result["trajectory_observed_students"] == 2
    assert result["peer_relationships_unique"] == 1
    assert result["peer_repeat_contacts"] == 2
    assert result["peer_repeat_contact_rate"] == 2 / 3


def test_llm_paths_use_decisions_and_empty_peer_rate_is_undefined():
    events = [event(1, 1, "s000", "model_decision", detail={"action": "rest"}),
              event(2, 1, "s000", "rest"),
              event(3, 2, "s000", "model_decision", detail={"action": "request"}),
              event(4, 2, "s000", "request", "rejected"),
              event(5, 1, "s001", "model_decision", detail={"action": "attend"})]
    result = trajectory_metrics(events)
    assert result["trajectory_transition_matrix"]["rest"]["request"] == 1
    assert result["trajectory_action_transitions"] == 1
    assert result["trajectory_observed_students"] == 2
    assert result["peer_repeat_contact_rate"] is None
    assert set(result["trajectory_transition_matrix"]) == set(ACTION_NAMES)
