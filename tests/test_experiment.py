import json
from pathlib import Path

from campus_sim.domain import Config, academic_event
from campus_sim.population import make_population


def test_declared_conditions_only_change_policy_name():
    configs = {name: json.loads((Path("configs") / f"{name}.json").read_text(encoding="utf-8"))
               for name in ("baseline", "outreach", "easier_access")}
    common = {name: {key: value for key, value in raw.items()
                     if key not in {"condition", "policy_components"}}
              for name, raw in configs.items()}
    assert common["baseline"] == common["outreach"] == common["easier_access"]
    assert configs["baseline"]["policy_components"]["access"]["barrier_multiplier"] == 1.0
    assert configs["easier_access"]["policy_components"]["access"]["barrier_multiplier"] == 0.45
    assert configs["outreach"]["policy_components"]["information_delivery"]["enabled"]
    for seed in (11, 22, 33):
        populations = []
        for raw in configs.values():
            config = Config.parse({**raw, "seed": seed})
            populations.append((make_population(config),
                                [academic_event(seed, day) for day in range(1, 31)]))
        assert populations[0] == populations[1] == populations[2]
