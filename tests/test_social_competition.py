import pytest

from campus_sim.domain import Config
from campus_sim.naming import run_stem
from campus_sim.population import make_population
from campus_sim.social_competition import (
    apply_partner_effect,
    apply_social_effect,
    election_candidate,
    social_choice,
    svo_group,
)
from campus_sim.web import build_run_config


def test_social_intervention_uses_matched_draws_and_reduces_undermining():
    student = make_population(Config(n_students=2, seed=11))[0][0]
    baseline = [social_choice(11, day, student, "none") for day in range(1, 501)]
    intervention = [social_choice(11, day, student, "anti_malicious")
                    for day in range(1, 501)]
    assert intervention.count("undermine") < baseline.count("undermine")
    assert intervention.count("cooperate") > baseline.count("cooperate")
    assert set(baseline) == {"cooperate", "compete", "undermine"}


def test_model_defined_orientation_bins():
    assert [svo_group(score) for score in (0.3, 0.4, 0.6)] == [
        "competitive", "individualistic", "prosocial"]


def test_social_effects_stay_bounded_and_ballot_handles_singleton_class():
    student = make_population(Config(n_students=2))[0][0]
    student["stress"] = 0.99
    effect = apply_social_effect(student, "undermine")
    assert effect["before"]["stress"] == 0.99
    assert student["stress"] == 1
    impact = apply_partner_effect(student, "undermine")
    assert impact["before"]["stress"] == 1
    assert student["stress"] == 1
    assert election_candidate(11, 5, student, ["s005"], "none") == "s005"


@pytest.mark.parametrize("overrides", [
    {"scenario": "unknown"},
    {"scenario": "support_access", "competition_intervention": "anti_malicious"},
    {"scenario": "social_competition", "competition_intervention": "unknown"},
    {"scenario": "social_competition", "n_students": 1},
])
def test_invalid_social_configuration_is_rejected(overrides):
    from dataclasses import asdict

    with pytest.raises(ValueError):
        Config.parse({**asdict(Config()), **overrides})


def test_web_builds_social_scenario_from_existing_template():
    from pathlib import Path

    project = Path(__file__).resolve().parents[1]
    data = {"condition": "baseline", "seed": 11, "n_students": 6, "n_staff": 2,
            "days": 3, "periods_per_day": 1, "capacity": 3, "open_every": 1,
            "access_barrier": 0.22, "outreach_day": 5,
            "scenario": "social_competition", "competition_intervention": "anti_malicious"}
    config = build_run_config(project, data)
    assert config["scenario"] == "social_competition"
    assert config["competition_intervention"] == "anti_malicious"
    assert run_stem("baseline", 11, config) == "social-baseline-rule-seed11"
