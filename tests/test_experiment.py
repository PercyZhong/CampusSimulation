import json
from pathlib import Path

from campus_sim.domain import Config, academic_event
from campus_sim.population import make_population


def test_declared_conditions_only_change_policy_name():
    configs = {name: json.loads((Path("configs") / f"{name}.json").read_text(encoding="utf-8"))
               for name in ("baseline", "outreach", "easier_access")}
    common = {name: {key: value for key, value in raw.items() if key != "condition"}
              for name, raw in configs.items()}
    assert common["baseline"] == common["outreach"] == common["easier_access"]
    for seed in (11, 22, 33):
        populations = []
        for raw in configs.values():
            config = Config.parse({**raw, "seed": seed})
            populations.append((make_population(config),
                                [academic_event(seed, day) for day in range(1, 31)]))
        assert populations[0] == populations[1] == populations[2]
