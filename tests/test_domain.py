from dataclasses import replace

import pytest

from campus_sim.domain import (
    Config,
    academic_context,
    academic_event,
    action_choice,
    campus_calendar,
    decay_long_term_memory,
    intervention_effects,
    memory_context,
    next_mood,
    next_stress,
    peer_response,
    policy_components,
    policy_context,
    residence_context,
    unit,
    update_long_term_memory,
    update_long_term_state,
)


def test_campus_calendar_is_reproducible_and_has_weekly_opening_rules():
    first = campus_calendar(11, 1, "s000")
    assert first == campus_calendar(11, 1, "s000")
    assert first["course"]["open"] is True
    assert first["course"]["capacity"] == 5
    assert first["cafeteria"]["open"] is True
    assert campus_calendar(11, 6, "s000")["course"]["open"] is False
    assert campus_calendar(11, 7, "s000")["library"]["open"] is False


def test_peer_response_is_deterministic_and_auditable():
    first = peer_response(11, 1, "s000", "s001", {"stress": 0.3})
    assert first in {"accepted", "rejected", "timeout"}
    assert first == peer_response(11, 1, "s000", "s001", {"stress": 0.3})
from campus_sim.language import language_backend
from campus_sim.population import make_population, make_staff


def test_configuration_and_named_streams():
    base = Config.parse({})
    assert base.n_students == 40 and base.end == 30
    with pytest.raises(ValueError):
        Config.parse({"condition": "unknown"})
    with pytest.raises(ValueError):
        Config.parse({"n_students": True})
    with pytest.raises(ValueError):
        Config.parse({"access_barrier": -0.1})
    with pytest.raises(ValueError):
        Config.parse({"capacity": 7})
    with pytest.raises(ValueError):
        Config.parse({"language_backend": "remote"})
    assert unit(11, "choice", 1, "s000") == unit(11, "choice", 1, "s000")
    assert unit(11, "choice", 1, "s000") != unit(11, "academic_event", 1, "s000")
    components = policy_components(Config(condition="easier_access"))
    assert components["access"]["barrier_multiplier"] == 0.45
    assert components["service"] == {"capacity": 3, "open_every": 1}
    context = policy_context(Config(), make_population(Config())[0][0], 1)
    assert set(context["components"]) == {"information_delivery", "access", "service", "follow_up"}


def test_targeted_intervention_window_and_validation():
    config = Config.parse({"condition": "targeted_outreach", "policy_components": {
        "information_delivery": {"day": 5, "duration_days": 3, "target_group": "lower"},
        "access": {"barrier_multiplier": 0.5, "start_day": 6,
                   "duration_days": 2, "target_group": "lower"}}})
    lower = {"initial_group": "lower", "outreach_seen": True}
    higher = {"initial_group": "higher", "outreach_seen": False}
    assert not intervention_effects(config, lower, 4)["information_active"]
    assert intervention_effects(config, lower, 5)["information_active"]
    assert intervention_effects(config, lower, 6)["effective_access_barrier"] == 0.11
    assert not intervention_effects(config, lower, 8)["information_active"]
    assert intervention_effects(config, lower, 8)["effective_access_barrier"] == 0.22
    assert not intervention_effects(config, higher, 6)["information_active"]
    assert intervention_effects(config, higher, 6)["effective_access_barrier"] == 0.22
    with pytest.raises(ValueError, match="target_group"):
        Config.parse({"policy_components": {"information_delivery": {
            "target_group": "current_stress"}}})
    with pytest.raises(ValueError, match="duration_days"):
        Config.parse({"policy_components": {"access": {"duration_days": 0}}})


def test_synthetic_population_is_shared_across_conditions():
    base = Config()
    left, edges = make_population(base)
    for condition in ("outreach", "easier_access"):
        right, other_edges = make_population(replace(base, condition=condition))
        assert left == right and edges == other_edges
    assert len(left) == 40 and len(edges) == 40
    assert len(make_staff(base)) == base.n_staff
    assert sum(member["slots_per_day"] for member in make_staff(base)) >= base.capacity
    assert all(row["id"].startswith("s") for row in left)
    assert all(0 <= row[key] <= 1 for row in left
               for key in ("stress", "mood", "recovery", "connection", "willingness"))
    assert all(row["stress"] == row["initial_stress"] for row in left)
    assert all(row["recent_events"] == [] and row["social_memory"] == [] for row in left)


def test_policy_does_not_shift_external_schedule():
    base = Config()
    load = [academic_event(base.seed, day) for day in range(1, 31)]
    assert load == [academic_event(replace(base, condition="outreach").seed, day)
                    for day in range(1, 31)]
    student = make_population(base)[0][0]
    assert action_choice(student, 1, base) in {"attend", "rest", "peer", "request"}


def test_academic_context_is_deterministic_and_student_specific():
    first = academic_context(11, 2, "s000")
    assert first == academic_context(11, 2, "s000")
    assert set(first) == {"course_id", "venue", "section"}
    assert first == academic_context(11, 2, "s001")
    assert first != academic_context(11, 2, "s005")
    assert first != academic_context(11, 3, "s000")


def test_residence_context_groups_roommates():
    assert residence_context("s000") == residence_context("s002")
    assert residence_context("s000") != residence_context("s003")


def test_sustained_workload_retains_stress_range():
    stress = 0.5
    for _ in range(30):
        stress = next_stress(stress, recovery=0.3, load=0.1, supported=False)
    assert 0.4 < stress < 0.9
    assert 0 <= next_mood(0.5, stress, 0.3, True) <= 1


def test_action_feedback_is_bounded_and_directional():
    assert next_stress(0.8, 0.5, 0.0, True) < 0.8
    assert next_mood(0.5, 0.2, 0.8, True) > 0.5
    assert next_stress(0.99, 0.0, 1.0, False) <= 1
    assert next_mood(0.01, 1.0, 0.0, False) >= 0


def test_memory_context_applies_recency_weights_without_mutating_events():
    events = [{"day": 1, "action": "rest"}, {"day": 4, "action": "peer"}]
    weighted = memory_context(events, day=5)
    assert [item["recency_weight"] for item in weighted] == [0.2, 0.5]
    assert events == [{"day": 1, "action": "rest"}, {"day": 4, "action": "peer"}]


def test_long_term_partner_memory_decays_and_keeps_factual_counts():
    state = {"long_term_memory": {"action_counts": {}, "partners": {}}}
    update_long_term_memory(state, {"day": 1, "action": "peer_invitation",
                                    "partner": "s001", "response": "accepted"})
    update_long_term_memory(state, {"day": 1, "action": "peer", "partner": "s001"})
    trace = state["long_term_memory"]["partners"]["s001"]
    assert trace["contacts"] == 1
    assert trace["last_response"] == "accepted"
    assert state["long_term_memory"]["action_counts"] == {
        "peer_invitation": 1, "peer": 1}
    before = trace["salience"]
    decay_long_term_memory(state, 2)
    assert state["long_term_memory"]["partners"]["s001"]["salience"] < before
    decay_long_term_memory(state, 50)
    assert state["long_term_memory"]["partners"] == {}
    assert state["long_term_memory"]["action_counts"]["peer"] == 1


def test_long_term_memory_retains_conflicting_responses_and_event_strength():
    state = {"long_term_memory": {"action_counts": {}, "partners": {}, "events": {}}}
    update_long_term_memory(state, {"day": 1, "action": "peer_response",
                                    "partner": "s001", "response": "accepted"})
    update_long_term_memory(state, {"day": 2, "action": "peer_response",
                                    "partner": "s001", "response": "rejected"})
    trace = state["long_term_memory"]["partners"]["s001"]
    assert [item["response"] for item in trace["response_history"]] == ["accepted", "rejected"]
    assert trace["confidence"] < 0
    update_long_term_memory(state, {"day": 2, "action": "course_absence",
                                    "event_type": "academic_setback", "impact": 0.3})
    assert state["long_term_memory"]["events"]["academic_setback"]["strength"] == 0.3


def test_long_term_state_recovery_and_setback_are_bounded_and_coupled():
    state = {"academic_engagement": 0.5, "self_efficacy": 0.5,
             "belonging": 0.5, "help_seeking_belief": 0.5,
             "social_avoidance": 0.5, "low_streak": 0}
    update_long_term_state(state, action="attend", outcome="completed", day=1)
    update_long_term_state(state, action="attend", outcome="completed", day=2)
    assert state["academic_success_streak"] == 2
    assert state["self_efficacy"] > 0.5
    update_long_term_state(state, action="course_absence", outcome="completed", day=3)
    assert state["setback_streak"] == 1
    assert all(0 <= state[key] <= 1 for key in ("academic_engagement", "self_efficacy",
                                                 "belonging", "help_seeking_belief",
                                                 "social_avoidance"))


def test_partner_memory_changes_rule_peer_choice(monkeypatch):
    base = Config()
    student = make_population(base)[0][0]
    student["willingness"] = 0
    student["connection"] = 1
    student["belonging"] = 0.5
    student["social_avoidance"] = 0.5

    def draw(seed, stream, *parts):
        return 1 if stream == "choice" else 0.25

    monkeypatch.setattr("campus_sim.domain.unit", draw)
    assert action_choice(student, 1, base) == "rest"
    student["long_term_memory"]["partners"]["s001"] = {
        "contacts": 2, "last_day": 1, "salience": 0.5, "last_response": "accepted"}
    assert action_choice(student, 1, base) == "peer"


def test_offline_language_is_reproducible():
    backend = language_backend("offline")
    assert backend.peer_message(seed=11, day=1, actor="s000") == backend.peer_message(
        seed=11, day=1, actor="s000")
    dialogue = backend.peer_dialogue(seed=11, day=1, actor="s000", partner="s001")
    assert [turn["speaker"] for turn in dialogue] == ["s000", "s001", "s000", "s001"]
    assert dialogue == backend.peer_dialogue(seed=11, day=1, actor="s000", partner="s001")


def test_peer_dialogue_uses_agent_state_and_memory():
    backend = language_backend("offline")
    dialogue = backend.peer_dialogue(
        seed=11, day=1, actor="s000", partner="s001",
        actor_state={"stress": 0.8, "connection": 0.6},
        partner_state={"stress": 0.4, "mood": 0.8},
        shared_memory=[{"partner": "s001", "day": 1}],
    )
    assert "压力" in dialogue[0]["text"]
    assert "笔记" in dialogue[1]["text"]
    assert "上次" in dialogue[2]["text"]
