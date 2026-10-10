from dataclasses import replace

import pytest

from campus_sim.domain import Config, academic_event, action_choice, next_stress, unit
from campus_sim.population import make_population


def test_configuration_and_named_streams():
    base = Config.parse({})
    assert base.n_students == 40 and base.end == 30
    with pytest.raises(ValueError):
        Config.parse({"condition": "unknown"})
    with pytest.raises(ValueError):
        Config.parse({"n_students": True})
    with pytest.raises(ValueError):
        Config.parse({"access_barrier": -0.1})
    assert unit(11, "choice", 1, "s000") == unit(11, "choice", 1, "s000")
    assert unit(11, "choice", 1, "s000") != unit(11, "academic_event", 1, "s000")


def test_synthetic_population_is_shared_across_conditions():
    base = Config()
    left, edges = make_population(base)
    for condition in ("outreach", "easier_access"):
        right, other_edges = make_population(replace(base, condition=condition))
        assert left == right and edges == other_edges
    assert len(left) == 40 and len(edges) == 40
    assert all(row["id"].startswith("s") for row in left)
    assert all(0 <= row[key] <= 1 for row in left
               for key in ("stress", "recovery", "connection", "willingness"))


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
