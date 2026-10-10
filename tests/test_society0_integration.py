"""Run on Linux local filesystem after the pinned engine is installed."""

import json
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Society0 POSIX run contract")


def test_two_day_completed_action_and_export(tmp_path):
    import asyncio
    from campus_sim.analysis import export
    from campus_sim.plan import build
    from society0.kernel.observation import Observation
    from society0.kernel.runner import run_plan

    raw = json.loads(open("configs/baseline.json", encoding="utf-8").read())
    raw.update(end=2, n_students=4)
    path = tmp_path / "two-day"
    result = asyncio.run(run_plan(path, build(raw)))
    assert result["complete_step"] == 2
    with Observation(path) as observation:
        assert observation.status()["complete"]["step"] == 2
        actions = observation.reader.read(lambda view: list(view.iter_query(
            "SELECT actor,action,status FROM campus_events WHERE day<=2 ORDER BY ordinal")))
        usage = observation.reader.read(lambda view: list(view.iter_query(
            "SELECT day,used FROM campus_service_usage ORDER BY day")))
    assert any(status == "completed" for _, _, status in actions)
    assert all(used <= raw["capacity"] for _, used in usage)
    assert export(path)["complete_steps"] == 2


def test_repetition_and_completed_step_resume(tmp_path):
    import asyncio
    from campus_sim.analysis import summarize, verify_resume
    from campus_sim.plan import build
    from society0.kernel.runner import run_plan

    raw = json.loads(open("configs/baseline.json", encoding="utf-8").read())
    raw.update(end=5, n_students=5)
    first, second, resumed = (tmp_path / name for name in ("first", "second", "resumed"))
    asyncio.run(run_plan(first, build(raw)))
    asyncio.run(run_plan(second, build(raw)))
    assert summarize(first)[1:] == summarize(second)[1:]
    asyncio.run(run_plan(resumed, build(raw), source=first, step=3))
    assert verify_resume(first, resumed)["equivalent"]
