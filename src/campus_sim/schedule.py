"""Daily Society0 phases; service settlement follows all actor actions."""

import json

from society0.kernel.plugins import Plugin
from society0.kernel.results import StepResult
from society0.kernel.runtime import Phase
from society0.kernel.schedule import SequenceSchedule, activate


def campus_schedule_plugin(config, actor_ids, staff_ids, teacher_ids):
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

        async def teacher_review(phase):
            results = await activate(phase, teacher_ids)
            return StepResult(metrics={"teacher_reviews": len(results)},
                              tables={"teacher_actions": (
                {"actor": item.actor_id, **item.result.value} for item in results)})

        async def adviser_review(phase):
            results = await activate(phase, staff_ids)
            return StepResult(metrics={"adviser_reviews": len(results)},
                              tables={"adviser_actions": (
                {"actor": item.actor_id, **item.result.value} for item in results)})

        async def decide(phase):
            results = await activate(phase, actor_ids)
            return StepResult(metrics={"decisions": len(results)}, tables={"actions": (
                {"actor": item.actor_id, **item.result.value} for item in results)})

        def period_decide(period):
            async def run(phase):
                campus.current_period = period
                try:
                    actors = (campus.afternoon_order(actor_ids) if period == "afternoon"
                              else actor_ids)
                    results = await activate(phase, actors)
                finally:
                    campus.current_period = None
                return StepResult(metrics={f"{period}_decisions": len(results)},
                                  tables={f"{period}_actions": (
                    {"actor": item.actor_id, "period": period, **item.result.value}
                    for item in results)})
            return run

        def settle(phase):
            day = phase.moment.time
            metrics = campus.settle(day, current["load"])
            events = campus.store.read(lambda view: [
                {"ordinal": ordinal, "day": d, "actor": actor, "action": action,
                 "status": status, "reason": reason, "detail": json.loads(detail)}
                for ordinal, d, actor, action, status, reason, detail in
                view.iter_query("SELECT ordinal,day,actor,action,status,reason,detail FROM campus_events WHERE day=? ORDER BY ordinal", (day,))])
            return StepResult(metrics=metrics, tables={"events": events})

        def social_round(phase):
            return StepResult(metrics=campus.social_round(phase.moment.time))

        decisions = ((Phase("morning", period_decide("morning")),
                      Phase("afternoon", period_decide("afternoon")),
                      Phase("evening", period_decide("evening")))
                     if config.periods_per_day == 3 else (Phase("decide", decide),))
        adviser_phases = ((Phase("adviser_review", adviser_review),)
                          if config.driver == "llm" else ())
        context.provide("schedule", SequenceSchedule(range(config.start, config.end + 1),
            (Phase("prepare", prepare), Phase("staff_shift", staff_shift),
             Phase("teacher_review", teacher_review),
             *decisions, *adviser_phases,
             *((Phase("social_round", social_round),)
               if config.scenario == "social_competition" else ()),
             Phase("settle", settle))))
    return Plugin("schedule", ("campus", "runtime"), install)
