"""Importable synchronous Society0 plan factory."""

import hashlib
import json
import subprocess
import sys
from dataclasses import asdict

from society0.kernel.actors import ActorRecord, actor_plugin
from society0.kernel.drivers import rule_driver_plugin
from society0.kernel.interaction import Ref, interaction_plugin
from society0.kernel.results import results_plugin
from society0.kernel.runner import RunContract, RunPlan
from society0.kernel.runtime import DriverResult, runtime_plugin

from . import SOCIETY0_SHA
from .domain import (
    Config,
    action_choice,
    period_action_choice,
    period_available_actions,
    policy_context,
    scenario_context,
)
from .mechanism import campus_plugin
from .models import (
    DECISION_PROMPT_VERSION,
    DIALOGUE_PROMPT_VERSION,
    TEACHER_PROMPT_VERSION,
    ModelClient,
)
from .population import CLASS_SIZE
from .schedule import campus_schedule_plugin


def _git_sha():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return "uncommitted"


def build(raw):
    config = Config.parse(raw)
    ids = tuple(f"s{i:03d}" for i in range(config.n_students))
    staff_ids = tuple(f"c{i:03d}" for i in range(config.n_staff))
    teacher_ids = tuple(f"t{i:03d}" for i in range(
        (config.n_students + CLASS_SIZE - 1) // CLASS_SIZE))
    model_client = (ModelClient(config.model_provider, config.model_id, config.max_model_calls,
                                config.model_temperature)
                    if config.driver == "llm" else None)

    async def rule(session):
        actor = session.actor.id
        if actor in staff_ids:
            result = await session.actions.invoke("campus.staff_shift",
                Ref("campus", "staff", actor), {})
            return DriverResult("completed", {"action": "staff_shift",
                "status": result.status}, "rule")
        if actor in teacher_ids:
            args = {}
            if model_client is not None:
                context = json.loads((await session.information.read(
                    "/teachers/" + actor)).data)
                if context["candidates"]:
                    selected, approach, usage = model_client.teacher_decide(
                        day=session.moment.time, teacher=actor,
                        class_id=context["class_id"], candidates=context["candidates"])
                    args = {"student_id": selected, "approach": approach,
                            "provider": config.model_provider,
                            "model": config.model_id, "prompt_version": TEACHER_PROMPT_VERSION,
                            "usage": usage}
            result = await session.actions.invoke("campus.teacher_review",
                Ref("campus", "teacher", actor), args)
            return DriverResult("completed", {"action": "teacher_review",
                "status": result.status}, "rule")
        course = await session.information.read("/course")
        service = await session.information.read("/service")
        peers = await session.information.read("/peers/" + actor)
        service_data = json.loads(service.data)
        period = service_data["period"] if config.periods_per_day == 3 else None
        if int(actor[1:]) % CLASS_SIZE == 0 and period in (None, "morning"):
            await session.actions.invoke("campus.committee_check_in",
                Ref("campus", "student", actor), {})
        chunk = await session.information.read("/students/" + actor)
        state = json.loads(chunk.data)
        calendar = json.loads(course.data)
        if model_client is None:
            action = (period_action_choice(state, session.moment.time, period, config,
                                           service_data, calendar["course_available_slots"])
                      if period else
                      action_choice(state, session.moment.time, config))
        else:
            policy = policy_context(config, state, session.moment.time)
            if period:
                policy["available_actions"] = period_available_actions(
                    state, session.moment.time, period, config, service_data,
                    calendar["course_available_slots"])
                policy["period"] = period
            elif (not calendar["calendar"]["course"]["open"]
                    or calendar["course_available_slots"] == 0):
                policy["available_actions"] = [action for action in policy["available_actions"]
                                               if action != "attend"]
            action, usage = model_client.decide(
                day=session.moment.time, student=state, course=calendar,
                service=service_data, peers=json.loads(peers.data), policy=policy)
            await session.actions.invoke("campus.model_decision", Ref("campus", "student", actor),
                {"action": action, "provider": config.model_provider,
                 "model": config.model_id, "temperature": config.model_temperature,
                 "prompt_version": DECISION_PROMPT_VERSION,
                 "decision_context": {
                     "available_actions": policy["available_actions"],
                     "connection": state["connection"], "stress": state["stress"],
                     "mood": state["mood"], "pending_request": state["pending_day"] is not None,
                     "recent_peer_contacts": sum(
                         event["action"] == "peer"
                         for event in state["recent_events"]),
                     "social_memory_count": len(state["social_memory"]),
                     "long_term_memory": state["long_term_memory"],
                     "scenario": scenario_context(config.seed, session.moment.time, state),
                     "long_term_state": {key: state[key] for key in (
                         "academic_engagement", "self_efficacy", "belonging",
                         "help_seeking_belief", "social_avoidance")},
                 },
                 "policy": policy, "usage": usage,
                 **({"period": period} if period else {})})
        args = {}
        if action == "peer" and model_client is not None:
            invitation = await session.actions.invoke(
                "campus.peer_invite", Ref("campus", "student", actor), {})
            if invitation.status != "completed":
                return DriverResult("completed", {"action": action,
                    "status": invitation.status,
                    "reason": (invitation.value or {}).get("reason"),
                    **({"period": period} if period else {})}, "rule")
            peer_details = await session.information.read("/peer_dialogue/" + actor)
            peer_context = json.loads(peer_details.data)
            partner = invitation.value["partner"]
            if peer_context["partner"] != partner:
                raise ValueError("peer invitation partner does not match dialogue context")
            shared_memory = state["memory_context"]["social_memory"]
            partner_trace = state["long_term_memory"]["partners"].get(partner)
            if partner_trace:
                shared_memory = [*shared_memory,
                                 {"partner": partner, "long_term_trace": partner_trace}]
            turns, dialogue_usage = model_client.peer_dialogue(
                day=session.moment.time, actor=actor, partner=partner,
                actor_state=state, partner_state=peer_context["partner_state"],
                shared_memory=shared_memory)
            args = {"partner": partner, "turns": turns, "usage": dialogue_usage,
                    "prompt_version": DIALOGUE_PROMPT_VERSION}
        result = await session.actions.invoke("campus." + action,
                                               Ref("campus", "student", actor), args)
        return DriverResult("completed", {"action": action, "status": result.status,
                                           "reason": (result.value or {}).get("reason"),
                                           **({"period": period} if period else {})}, "rule")

    def allows(scope, operation, target):
        if target.namespace != "campus":
            return False
        if target.kind in {"service", "course"}:
            return operation in {"discover", "read"}
        return target.kind in {"student", "peer", "staff", "teacher"} and target.key == scope.actor

    plugins = [interaction_plugin(allows,
               access_dependencies=("campus_students", "campus_staff", "campus_teachers")),
               results_plugin(),
               rule_driver_plugin(rule),
               actor_plugin({"rule": ("rule_driver", "factory")},
                            records=[ActorRecord(actor, "rule") for actor in
                                     (*staff_ids, *teacher_ids, *ids)]),
               campus_plugin(config),
               runtime_plugin(actor_service=("actors", "actors"),
                              information=("interaction", "information"),
                              actions=("interaction", "actions"), store=("storage", "store"),
                              results=("results", "results"),
                              max_activations=config.n_students * config.periods_per_day +
                              config.n_staff + len(teacher_ids)),
               campus_schedule_plugin(config, ids, staff_ids, teacher_ids)]
    public = asdict(config)
    if "periods_per_day" not in raw:
        public.pop("periods_per_day")
    public["release"] = raw.get("release", {"scenario": "0.6.0"})
    config_hash = hashlib.sha256(json.dumps(public, sort_keys=True,
                                separators=(",", ":")).encode()).hexdigest()
    return RunPlan(plugins, RunContract(
        release={"scenario": "0.6.0", "scenario_git": _git_sha(), "society0_git": SOCIETY0_SHA},
        dependencies={"python": sys.version.split()[0], "society0_git": SOCIETY0_SHA},
        configuration={"plugins": {"campus": public},
                       "actors": [*staff_ids, *teacher_ids, *ids], "models": (
                           {config.model_provider: config.model_id} if model_client else {}),
                       "config_sha256": config_hash, "driver": config.driver},
        time={"start": config.start, "end": config.end, "unit": "day"},
        budgets={"max_activations": config.n_students * config.periods_per_day +
                 config.n_staff + len(teacher_ids),
                 "http_requests": config.max_model_calls,
                 "paid_model_calls": config.max_model_calls}))
