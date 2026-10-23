from dataclasses import asdict
from pathlib import Path

import pytest

from campus_sim.domain import Config
from campus_sim.naming import run_stem
from campus_sim.population import make_population
from campus_sim.school_bullying import (
    aggressor_for_class,
    apply_incident,
    apply_teacher_response,
    incident_occurs,
    incident_type,
)
from campus_sim.web import build_batch_plan, build_run_config

PROJECT = Path(__file__).resolve().parents[1]


def test_designated_targets_and_aggressors_are_distinct():
    students, _ = make_population(Config(n_students=6, scenario="school_bullying"))
    targets = [student for student in students if student["bullying_target"]]
    assert [student["id"] for student in targets] == ["s004"]
    assert aggressor_for_class(students[:5]) == "s001"
    assert aggressor_for_class(students[5:]) is None


def test_incident_and_teacher_effects_are_bounded_and_auditable():
    target = make_population(Config(n_students=2, scenario="school_bullying"))[0][1]
    baseline = {key: target[key] for key in ("self_esteem", "safety", "belonging")}
    incident = apply_incident(target)
    assert incident["before"]["self_esteem"] == baseline["self_esteem"]
    assert target["self_esteem"] < baseline["self_esteem"]
    assert target["safety"] < baseline["safety"]
    check_in = apply_teacher_response(target, "private_check_in", 1)
    assert check_in["after"]["self_esteem"] > check_in["before"]["self_esteem"]
    assert target["bullying_protection_until"] == 0
    plan = apply_teacher_response(target, "safety_plan", 2)
    assert plan["after"]["safety"] > plan["before"]["safety"]
    assert target["bullying_protection_until"] == 5
    assert target["bullying_prevention_multiplier"] == 0.25
    assert apply_teacher_response(target, "none", 3) is None
    assert incident_type(11, 1, target["id"]) in {"verbal_harassment", "social_exclusion"}


def test_protection_can_only_remove_matched_seed_incidents():
    target = make_population(Config(n_students=2, scenario="school_bullying"))[0][1]
    baseline = [incident_occurs(11, day, target) for day in range(1, 31)]
    target["bullying_protection_until"] = 30
    target["bullying_prevention_multiplier"] = 0.25
    protected = [incident_occurs(11, day, target) for day in range(1, 31)]
    assert sum(protected) < sum(baseline)
    assert all(not protected[day] or baseline[day] for day in range(30))


@pytest.mark.parametrize("overrides", [
    {"scenario": "school_bullying", "n_students": 1},
    {"scenario": "school_bullying", "bullying_teacher_strategy": "unknown"},
    {"scenario": "support_access", "bullying_teacher_strategy": "safety_plan"},
])
def test_invalid_bullying_configuration_is_rejected(overrides):
    with pytest.raises(ValueError):
        Config.parse({**asdict(Config()), **overrides})


def test_web_accepts_bullying_strategy_and_run_name_identifies_it():
    data = {"condition": "baseline", "seed": 11, "n_students": 10, "n_staff": 2,
            "days": 10, "periods_per_day": 1, "capacity": 3, "open_every": 1,
            "access_barrier": 0.22, "outreach_day": 5,
            "scenario": "school_bullying", "bullying_teacher_strategy": "safety_plan"}
    raw = build_run_config(PROJECT, data)
    assert raw["bullying_teacher_strategy"] == "safety_plan"
    assert run_stem("baseline", 11, raw) == "bullying-safety_plan-baseline-rule-seed11"


def test_teacher_strategy_batch_uses_paired_seeds_and_shared_configuration():
    data = {"comparison_axis": "bullying_teacher", "scenario": "school_bullying",
            "teacher_strategies": ["none", "safety_plan"],
            "condition": "baseline", "seeds": [11, 22], "n_students": 10,
            "n_staff": 2, "days": 10, "periods_per_day": 1, "capacity": 3,
            "open_every": 1, "access_barrier": 0.22, "outreach_day": 5}
    plan = build_batch_plan(PROJECT, data)
    assert plan["comparison_axis"] == "bullying_teacher"
    assert len(plan["runs"]) == 4
    assert [item["config"]["bullying_teacher_strategy"] for item in plan["runs"]] == [
        "none", "none", "safety_plan", "safety_plan"]
    assert [item["config"]["seed"] for item in plan["runs"]] == [11, 22, 11, 22]


@pytest.mark.parametrize("change", [
    {"teacher_strategies": ["none"]},
    {"teacher_strategies": ["none", "none"]},
    {"scenario": "support_access"},
    {"comparison_axis": "unknown"},
])
def test_teacher_batch_rejects_invalid_axis_or_strategies(change):
    data = {"comparison_axis": "bullying_teacher", "scenario": "school_bullying",
            "teacher_strategies": ["none", "safety_plan"],
            "condition": "baseline", "seeds": [11], "n_students": 10,
            "n_staff": 2, "days": 10, "periods_per_day": 1, "capacity": 3,
            "open_every": 1, "access_barrier": 0.22, "outreach_day": 5}
    with pytest.raises(ValueError):
        build_batch_plan(PROJECT, {**data, **change})
