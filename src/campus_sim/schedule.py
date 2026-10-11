"""Daily Society0 phases; service settlement follows all actor actions."""

import json

from society0.kernel.plugins import Plugin
from society0.kernel.results import StepResult
from society0.kernel.runtime import Phase
from society0.kernel.schedule import SequenceSchedule, activate


def campus_schedule_plugin(config, actor_ids, staff_ids):
    def install(context):
        campus = context.require("campus", "campus")
        current = {}

        def prepare(phase):
            day = phase.moment.time
            current["load"] = campus.prepare(day)
            return StepResult(metrics={"academic_load": current["load"]})

        async def staff_shift(phase):
            results = await activate(phase, staff_ids)
            return StepResult(metrics={"staff_shifts": len(results)}, tables={"staff_actions": (
                {"actor": item.actor_id, **item.result.value} for item in results)})

        async def decide(phase):
            results = await activate(phase, actor_ids)
            return StepResult(metrics={"decisions": len(results)}, tables={"actions": (
                {"actor": item.actor_id, **item.result.value} for item in results)})

        def settle(phase):
            day = phase.moment.time
            metrics = campus.settle(day, current["load"])
            events = campus.store.read(lambda view: [
                {"ordinal": ordinal, "day": d, "actor": actor, "action": action,
                 "status": status, "reason": reason, "detail": json.loads(detail)}
                for ordinal, d, actor, action, status, reason, detail in
                view.iter_query("SELECT ordinal,day,actor,action,status,reason,detail FROM campus_events WHERE day=? ORDER BY ordinal", (day,))])
            return StepResult(metrics=metrics, tables={"events": events})

        context.provide("schedule", SequenceSchedule(range(config.start, config.end + 1),
            (Phase("prepare", prepare), Phase("staff_shift", staff_shift),
             Phase("decide", decide), Phase("settle", settle))))
    return Plugin("schedule", ("campus", "runtime"), install)
