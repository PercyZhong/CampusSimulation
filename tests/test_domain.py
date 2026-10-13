from dataclasses import replace

import pytest

from campus_sim.domain import Config, academic_event, action_choice, next_mood, next_stress, unit
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


def test_offline_language_is_reproducible():
    backend = language_backend("offline")
    assert backend.peer_message(seed=11, day=1, actor="s000") == backend.peer_message(
        seed=11, day=1, actor="s000")
    dialogue = backend.peer_dialogue(seed=11, day=1, actor="s000", partner="s001")
    assert [turn["speaker"] for turn in dialogue] == ["s000", "s001"]
    assert dialogue == backend.peer_dialogue(seed=11, day=1, actor="s000", partner="s001")
