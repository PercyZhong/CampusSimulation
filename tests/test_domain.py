from dataclasses import replace

import pytest

from campus_sim.domain import (
    Config,
    academic_event,
    action_choice,
    memory_context,
    next_mood,
    next_stress,
    policy_components,
    policy_context,
    unit,
)
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
