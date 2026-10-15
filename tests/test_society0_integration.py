"""Run on Linux local filesystem after the pinned engine is installed."""

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Society0 POSIX run contract")


def test_two_day_completed_action_and_export(tmp_path):
    import asyncio

    from society0.kernel.observation import Observation
    from society0.kernel.runner import run_plan

    from campus_sim.analysis import export
    from campus_sim.plan import build

    raw = json.loads(Path("configs/baseline.json").read_text(encoding="utf-8"))
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
        shifts = observation.reader.read(lambda view: list(view.iter_query(
            "SELECT day,actor,slots FROM campus_staff_shifts ORDER BY day,actor")))
    assert any(status == "completed" for _, _, status in actions)
    assert all(used <= raw["capacity"] for _, used in usage)
    assert len(shifts) == raw["end"] * raw["n_staff"]
    assert all(slots == 3 for _, _, slots in shifts)
    assert export(path)["complete_steps"] == 2
    summary = export(path)
    assert summary["n_staff"] == 2
    assert summary["staff_shift_count"] == raw["end"] * raw["n_staff"]
    assert summary["available_slots_total"] == raw["end"] * raw["capacity"]
    assert -1 <= summary["stress_change_distribution"]["mean"] <= 1
    assert 0 <= summary["mood_distribution"]["mean"] <= 1


def test_repetition_and_completed_step_resume(tmp_path):
    import asyncio

    from society0.kernel.runner import run_plan

    from campus_sim.analysis import summarize, verify_resume
    from campus_sim.plan import build

    raw = json.loads(Path("configs/baseline.json").read_text(encoding="utf-8"))
    raw.update(end=5, n_students=5)
    first, second, resumed = (tmp_path / name for name in ("first", "second", "resumed"))
    asyncio.run(run_plan(first, build(raw)))
    asyncio.run(run_plan(second, build(raw)))
    assert summarize(first)[1:] == summarize(second)[1:]
    asyncio.run(run_plan(resumed, build(raw), source=first, step=3))
    assert verify_resume(first, resumed)["equivalent"]


def test_actor_visibility_and_action_rechecks(tmp_path):
    import asyncio

    from society0.kernel.composition import compose
    from society0.kernel.interaction import InteractionScope, Moment, Ref, Unavailable

    from campus_sim.plan import build

    raw = json.loads(Path("configs/baseline.json").read_text(encoding="utf-8"))
    raw.update(end=2, n_students=4, capacity=1,
               policy_components={**raw["policy_components"], "service": {"capacity": 1, "open_every": 1}})

    async def exercise():
        async with compose(tmp_path / "permissions", build(raw).plugins) as host:
            information = host.service("interaction", "information")
            actions = host.service("interaction", "actions")
            with InteractionScope("c000", Moment(1, "staff_shift")) as scope:
                own_shift = await actions.bound(scope).invoke(
                    "campus.staff_shift", Ref("campus", "staff", "c000"), {})
                repeat_shift = await actions.bound(scope).invoke(
                    "campus.staff_shift", Ref("campus", "staff", "c000"), {})
                other_shift = await actions.bound(scope).invoke(
                    "campus.staff_shift", Ref("campus", "staff", "c001"), {})
                assert own_shift.status == "completed"
                assert repeat_shift.status == "rejected"
                assert other_shift.status == "rejected"
            with InteractionScope("s000", Moment(1, "decide")) as scope:
                own = await information.bound(scope).read("/students/s000")
                assert json.loads(own.data)["id"] == "s000"
                with pytest.raises(Unavailable):
                    await information.bound(scope).read("/students/s001")
                with pytest.raises(Unavailable):
                    await information.bound(scope).read("/peers/s001")
                other = await actions.bound(scope).invoke(
                    "campus.request", Ref("campus", "student", "s001"), {})
                assert other.status == "rejected"
                first = await actions.bound(scope).invoke(
                    "campus.request", Ref("campus", "student", "s000"), {})
                repeat = await actions.bound(scope).invoke(
                    "campus.request", Ref("campus", "student", "s000"), {})
                assert first.status == "completed"
                assert repeat.status == "rejected" and repeat.value["reason"] == "already_pending"
            for actor in ("s001", "s002", "s003"):
                with InteractionScope(actor, Moment(1, "decide")) as scope:
                    result = await actions.bound(scope).invoke(
                        "campus.request", Ref("campus", "student", actor), {})
                    assert result.status == "completed"
            with InteractionScope("c000", Moment(2, "staff_shift")) as scope:
                shift = await actions.bound(scope).invoke(
                    "campus.staff_shift", Ref("campus", "staff", "c000"), {})
                assert shift.status == "completed"
            outcomes = []
            for actor in ("s000", "s001", "s002", "s003"):
                with InteractionScope(actor, Moment(2, "decide")) as scope:
                    outcomes.append(await actions.bound(scope).invoke(
                        "campus.accept_support", Ref("campus", "student", actor), {}))
            assert sum(item.status == "completed" for item in outcomes) == 1
            store = host.service("storage", "store")
            assert store.read(lambda view: view.query(
                "SELECT used FROM campus_service_usage WHERE day=2"))[0][0] == 1

    asyncio.run(exercise())


def test_peer_action_records_partner_dialogue_and_updates_partner(tmp_path):
    import asyncio

    from society0.kernel.composition import compose
    from society0.kernel.interaction import InteractionScope, Moment, Ref

    from campus_sim.plan import build

    raw = json.loads(Path("configs/baseline.json").read_text(encoding="utf-8"))
    raw.update(end=1, n_students=3)

    async def exercise():
        async with compose(tmp_path / "peer", build(raw).plugins) as host:
            actions = host.service("interaction", "actions")
            with InteractionScope("s000", Moment(1, "decide")) as scope:
                result = await actions.bound(scope).invoke(
                    "campus.peer", Ref("campus", "student", "s000"), {})
                assert result.status == "completed"
            store = host.service("storage", "store")
            event = store.read(lambda view: view.query(
                "SELECT detail FROM campus_events WHERE action='peer' AND status='completed'",
                max_rows=1))[0][0]
            detail = json.loads(event)
            assert detail["partner"] in {"s001", "s002"}
            assert len(detail["turns"]) == 4
            student = store.read(lambda view: json.loads(view.query(
                "SELECT body FROM campus_students WHERE id='s000'", max_rows=1)[0][0]))
            partner = store.read(lambda view: json.loads(view.query(
                "SELECT body FROM campus_students WHERE id=?", (detail["partner"],), max_rows=1)[0][0]))
            assert student["recent_events"][-1]["action"] == "peer"
            assert student["social_memory"][-1]["partner"] == detail["partner"]
            assert partner["social_memory"][-1]["partner"] == "s000"

    asyncio.run(exercise())


def test_follow_up_component_records_once_and_updates_student(tmp_path):
    import asyncio

    from society0.kernel.composition import compose
    from society0.kernel.interaction import InteractionScope, Moment, Ref

    from campus_sim.plan import build

    raw = json.loads(Path("configs/baseline.json").read_text(encoding="utf-8"))
    raw.update(end=6, n_students=1, policy_components={
        **raw["policy_components"], "service": {"capacity": 1, "open_every": 1},
        "follow_up": {"enabled": True, "days_after_support": 2}})

    async def exercise():
        async with compose(tmp_path / "follow-up", build(raw).plugins) as host:
            actions = host.service("interaction", "actions")
            with InteractionScope("s000", Moment(1, "decide")) as scope:
                requested = await actions.bound(scope).invoke(
                    "campus.request", Ref("campus", "student", "s000"), {})
                assert requested.status == "completed"
            with InteractionScope("c000", Moment(2, "staff_shift")) as scope:
                shift = await actions.bound(scope).invoke(
                    "campus.staff_shift", Ref("campus", "staff", "c000"), {})
                assert shift.status == "completed"
            with InteractionScope("s000", Moment(2, "decide")) as scope:
                accepted = await actions.bound(scope).invoke(
                    "campus.accept_support", Ref("campus", "student", "s000"), {})
                assert accepted.status == "completed"
            campus = host.service("campus", "campus")
            store = host.service("storage", "store")
            store.transaction(lambda writer: writer.execute(
                "INSERT INTO campus_service_usage VALUES(?,?)", (4, 1)))
            campus.prepare(4)
            with InteractionScope("c000", Moment(4, "staff_shift")) as scope:
                shift = await actions.bound(scope).invoke(
                    "campus.staff_shift", Ref("campus", "staff", "c000"), {})
                assert shift.status == "completed"
            campus.settle(4, 0.0)
            state = store.read(lambda view: json.loads(view.query(
                "SELECT body FROM campus_students WHERE id='s000'", max_rows=1)[0][0]))
            assert state["follow_up_sent"] is False
            with InteractionScope("c000", Moment(5, "staff_shift")) as scope:
                shift = await actions.bound(scope).invoke(
                    "campus.staff_shift", Ref("campus", "staff", "c000"), {})
                assert shift.status == "completed"
            campus.prepare(5)
            campus.settle(5, 0.0)
            campus.prepare(6)
            campus.settle(6, 0.0)
            events = store.read(lambda view: view.query(
                "SELECT day,action FROM campus_events WHERE action='staff_follow_up'"))
            assert events == [(5, "staff_follow_up")]
            state = store.read(lambda view: json.loads(view.query(
                "SELECT body FROM campus_students WHERE id='s000'", max_rows=1)[0][0]))
            assert state["follow_up_sent"] is True
            assert state["recent_events"][-1]["action"] == "staff_follow_up"
            assert store.read(lambda view: view.query(
                "SELECT used FROM campus_follow_up_usage WHERE day=5")) == [(1,)]

    asyncio.run(exercise())


def test_follow_up_capacity_prioritizes_earliest_support(tmp_path):
    import asyncio

    from society0.kernel.composition import compose
    from society0.kernel.interaction import InteractionScope, Moment, Ref

    from campus_sim.plan import build

    raw = json.loads(Path("configs/baseline.json").read_text(encoding="utf-8"))
    raw.update(end=6, n_students=2, policy_components={
        **raw["policy_components"], "service": {"capacity": 1, "open_every": 1},
        "follow_up": {"enabled": True, "days_after_support": 2}})

    async def exercise():
        async with compose(tmp_path / "follow-up-priority", build(raw).plugins) as host:
            actions = host.service("interaction", "actions")
            for actor in ("s000", "s001"):
                with InteractionScope(actor, Moment(1, "decide")) as scope:
                    assert (await actions.bound(scope).invoke(
                        "campus.request", Ref("campus", "student", actor), {})).status == "completed"
            with InteractionScope("c000", Moment(2, "staff_shift")) as scope:
                assert (await actions.bound(scope).invoke(
                    "campus.staff_shift", Ref("campus", "staff", "c000"), {})).status == "completed"
            with InteractionScope("s000", Moment(2, "decide")) as scope:
                assert (await actions.bound(scope).invoke(
                    "campus.accept_support", Ref("campus", "student", "s000"), {})).status == "completed"
            with InteractionScope("c000", Moment(3, "staff_shift")) as scope:
                assert (await actions.bound(scope).invoke(
                    "campus.staff_shift", Ref("campus", "staff", "c000"), {})).status == "completed"
            with InteractionScope("s001", Moment(3, "decide")) as scope:
                assert (await actions.bound(scope).invoke(
                    "campus.accept_support", Ref("campus", "student", "s001"), {})).status == "completed"
            campus = host.service("campus", "campus")
            for day in (4, 5, 6):
                with InteractionScope("c000", Moment(day, "staff_shift")) as scope:
                    await actions.bound(scope).invoke(
                        "campus.staff_shift", Ref("campus", "staff", "c000"), {})
                campus.prepare(day)
                campus.settle(day, 0.0)
            store = host.service("storage", "store")
            assert store.read(lambda view: view.query(
                "SELECT actor,day FROM campus_events WHERE action='staff_follow_up' ORDER BY day")) == [
                    ("s000", 4), ("s001", 5)]

    asyncio.run(exercise())


def test_failure_restores_only_complete_step(tmp_path):
    import asyncio

    from society0.kernel.observation import Observation
    from society0.kernel.plugins import Plugin
    from society0.kernel.runner import run_plan
    from society0.kernel.runtime import Phase
    from society0.kernel.schedule import StepPlan

    from campus_sim.analysis import verify_resume
    from campus_sim.plan import build

    raw = json.loads(Path("configs/baseline.json").read_text(encoding="utf-8"))
    raw.update(end=3, n_students=4)
    failed, full, recovered = (tmp_path / name for name in ("failed", "full", "recovered"))
    plan = build(raw)

    def install(context):
        original = context.require("schedule", "schedule")

        class FaultSchedule:
            async def next_step(self, completed_step):
                planned = await original.next_step(completed_step)
                if planned is None:
                    return None

                def fault(phase):
                    if phase.moment.time == 2:
                        raise RuntimeError("planned step failure")

                return StepPlan(planned.time, (*planned.phases, Phase("fault", fault)))

        context.provide("schedule", FaultSchedule())

    plan.plugins.append(Plugin("fault_schedule", ("schedule",), install))
    plan.schedule = ("fault_schedule", "schedule")
    with pytest.raises(RuntimeError, match="planned step failure"):
        asyncio.run(run_plan(failed, plan))
    with Observation(failed) as observation:
        assert observation.status()["complete"]["step"] == 1
    asyncio.run(run_plan(full, build(raw)))
    asyncio.run(run_plan(recovered, build(raw), source=failed, step=1))
    assert verify_resume(full, recovered)["equivalent"]
