from pathlib import Path

import pytest

from campus_sim.web import build_run_config


PROJECT = Path(__file__).resolve().parents[1]


def payload(**overrides):
    data = {"condition": "targeted_outreach", "seed": 7, "n_students": 12,
            "n_staff": 3, "days": 8, "periods_per_day": 3, "capacity": 6,
            "open_every": 2, "access_barrier": 0.3, "outreach_day": 4}
    return {**data, **overrides}


def test_web_config_combines_template_and_bounded_controls():
    config = build_run_config(PROJECT, payload())
    assert (config["n_students"], config["n_staff"], config["end"]) == (12, 3, 8)
    assert config["policy_components"]["service"] == {"capacity": 6, "open_every": 2}
    assert config["policy_components"]["information_delivery"]["day"] == 4
    assert config["periods_per_day"] == 3


@pytest.mark.parametrize("change", [
    {"n_students": 101}, {"n_staff": 11}, {"days": 31},
    {"capacity": 10}, {"periods_per_day": 2},
])
def test_web_config_rejects_out_of_bounds_settings(change):
    with pytest.raises(ValueError):
        build_run_config(PROJECT, payload(**change))
