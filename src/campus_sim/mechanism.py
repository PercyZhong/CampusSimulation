"""Society0 owned campus facts and actor bound actions."""

import json

from society0.kernel.interaction import Action, ActionResult, DocumentChunk, Ref
from society0.kernel.plugins import Plugin

from .domain import (
    academic_context,
    academic_event,
    campus_calendar,
    clamp,
    decay_long_term_memory,
    intervention_effects,
    memory_context,
    next_mood,
    next_stress,
    peer_response,
    period_available_actions,
    policy_components,
    policy_context,
    relationship_type,
    residence_context,
    scenario_context,
    unit,
    update_long_term_memory,
    update_long_term_state,
)
from .language import language_backend
from .models import _dialogue
from .population import make_population, make_staff

SCHEMA = (
    "CREATE TABLE campus_students(id TEXT NOT NULL PRIMARY KEY, body TEXT NOT NULL)",
    "CREATE TABLE campus_staff(id TEXT NOT NULL PRIMARY KEY, body TEXT NOT NULL)",
    ("CREATE TABLE campus_staff_shifts(day INTEGER NOT NULL,actor TEXT NOT NULL,"
     "slots INTEGER NOT NULL,PRIMARY KEY(day,actor))"),
    "CREATE TABLE campus_edges(a TEXT NOT NULL,b TEXT NOT NULL,PRIMARY KEY(a,b))",
    ("CREATE TABLE campus_venue_bookings(day INTEGER NOT NULL,actor TEXT NOT NULL,"
     "venue TEXT NOT NULL,slot TEXT NOT NULL,activity TEXT NOT NULL,"
     "PRIMARY KEY(day,actor,slot))"),
    ("CREATE TABLE campus_peer_invitations(day INTEGER NOT NULL,actor TEXT NOT NULL,"
     "partner TEXT NOT NULL,response TEXT NOT NULL,PRIMARY KEY(day,actor))"),
    ("CREATE TABLE campus_peer_response_queue(day INTEGER NOT NULL,actor TEXT NOT NULL,"
     "partner TEXT NOT NULL,response TEXT NOT NULL,PRIMARY KEY(day,actor))"),
    ("CREATE TABLE campus_relationships(a TEXT NOT NULL,b TEXT NOT NULL,strength REAL NOT NULL,"
     "first_day INTEGER,last_day INTEGER,contacts INTEGER NOT NULL,relationship_type TEXT NOT NULL,"
     "PRIMARY KEY(a,b))"),
    "CREATE TABLE campus_queue(actor TEXT NOT NULL PRIMARY KEY, requested_day INTEGER NOT NULL, ordinal INTEGER NOT NULL UNIQUE)",
    "CREATE TABLE campus_service_usage(day INTEGER PRIMARY KEY, used INTEGER NOT NULL)",
    "CREATE TABLE campus_follow_up_usage(day INTEGER PRIMARY KEY, used INTEGER NOT NULL)",
    "CREATE TABLE campus_events(ordinal INTEGER PRIMARY KEY,day INTEGER NOT NULL,actor TEXT NOT NULL,action TEXT NOT NULL,status TEXT NOT NULL,reason TEXT,detail TEXT NOT NULL)",
    "CREATE TABLE campus_days(day INTEGER PRIMARY KEY,body TEXT NOT NULL)",
    "CREATE TABLE campus_interactions(ordinal INTEGER PRIMARY KEY,day INTEGER NOT NULL,actor TEXT NOT NULL,partner TEXT,interaction TEXT NOT NULL,context TEXT NOT NULL)",
)


def dumps(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class Campus:
    def __init__(self, store, config):
        self.store, self.config = store, config
        self.current_period = None
        self.language = language_backend(config.language_backend)

    def student(self, actor):
        def read(view):
            rows = view.query("SELECT body FROM campus_students WHERE id=?", (actor,), max_rows=1)
            return json.loads(rows[0][0]) if rows else None
        return self.store.read(read)

    def students(self):
        return self.store.read(lambda view: [json.loads(row[0]) for row in
            view.iter_query("SELECT body FROM campus_students ORDER BY id")])

    def afternoon_order(self, actor_ids):
        """Offer afternoon service to queued students in FIFO order first."""
        queued = self.store.read(lambda view: [row[0] for row in view.iter_query(
            "SELECT actor FROM campus_queue ORDER BY requested_day,ordinal")])
        return (*queued, *(actor for actor in actor_ids if actor not in queued))

    @staticmethod
    def remember(state, event):
        state.setdefault("recent_events", []).append(event)
        state["recent_events"] = state["recent_events"][-5:]
        enriched = dict(event)
        if enriched.get("action") == "course_absence":
            enriched["event_type"] = "academic_setback"
            enriched["impact"] = 0.22
        elif enriched.get("action") == "accept_support":
            enriched["event_type"] = "support_success"
            enriched["impact"] = 0.28
        update_long_term_memory(state, enriched)

    @staticmethod
    def remember_social(state, event):
        state.setdefault("social_memory", []).append(event)
        state["social_memory"] = state["social_memory"][-5:]

    def available_slots(self, view, day):
        rows = view.query("SELECT COALESCE(SUM(slots),0) FROM campus_staff_shifts WHERE day=?",
                          (day,))
        return min(policy_components(self.config)["service"]["capacity"], rows[0][0])

    @staticmethod
    def follow_up_slots_used(view, day):
        rows = view.query("SELECT used FROM campus_follow_up_usage WHERE day=?", (day,))
        return rows[0][0] if rows else 0

    @staticmethod
    def next_staff(view, day):
        """Select an on-shift staff member with an unused individual slot."""
        shifts = view.query(
            "SELECT actor,slots FROM campus_staff_shifts WHERE day=? ORDER BY actor", (day,))
        usage = {actor: 0 for actor, _ in shifts}
        for detail, in view.iter_query(
                "SELECT detail FROM campus_events WHERE day=? AND status='completed' "
                "AND action IN ('accept_support','staff_follow_up') ORDER BY ordinal", (day,)):
            staff_id = json.loads(detail).get("staff_id")
            if staff_id in usage:
                usage[staff_id] += 1
        return next((actor for actor, slots in shifts if usage[actor] < slots), None)

    def staff_shift(self, scope, target):
        day, actor = scope.moment.time, scope.actor
        if target.key != actor:
            return ActionResult("rejected", {"reason": "wrong_actor"})

        def write(writer):
            rows = writer.query("SELECT body FROM campus_staff WHERE id=?", (actor,), max_rows=1)
            if not rows:
                return {"status": "rejected", "reason": "unknown_staff"}
            existing = writer.query(
                "SELECT 1 FROM campus_staff_shifts WHERE day=? AND actor=?", (day, actor))
            if existing:
                return {"status": "rejected", "reason": "already_on_shift"}
            slots = json.loads(rows[0][0])["slots_per_day"]
            writer.execute("INSERT INTO campus_staff_shifts VALUES(?,?,?)", (day, actor, slots))
            self.event(writer, day, actor, "staff_shift", "completed", detail={"slots": slots})
            return {"status": "completed", "slots": slots}

        result = self.store.transaction(write)
        return ActionResult(result["status"], result)

    @staticmethod
    def event(writer, day, actor, action, status, reason=None, detail=None):
        writer.execute("INSERT INTO campus_events(day,actor,action,status,reason,detail) VALUES(?,?,?,?,?,?)",
                       (day, actor, action, status, reason, dumps(detail or {})))

    def prepare(self, day):
        load = academic_event(self.config.seed, day)
        def write(writer):
            components = policy_components(self.config)
            delivery = components["information_delivery"]
            if delivery["enabled"] and day == delivery["day"]:
                # Assignment uses only the fixed initial cohort, never current private state.
                for sid, body in list(writer.iter_query("SELECT id,body FROM campus_students ORDER BY id")):
                    state = json.loads(body)
                    target = delivery["target_group"]
                    if target != "all" and state["initial_group"] != target:
                        continue
                    state["outreach_seen"] = True
                    writer.execute("UPDATE campus_students SET body=? WHERE id=?", (dumps(state), sid))
                    self.event(writer, day, sid, "outreach_information", "completed",
                               detail={"target_group": target, "start_day": day,
                                       "duration_days": delivery["duration_days"]})
            for sid, body in writer.iter_query("SELECT id,body FROM campus_students ORDER BY id"):
                state = json.loads(body)
                effects = intervention_effects(self.config, state, day)
                if effects["information_active"] or (effects["access_active"] and
                        components["access"]["barrier_multiplier"] < 1):
                    self.event(writer, day, sid, "intervention_exposure", "completed",
                               detail=effects)
                context = scenario_context(self.config.seed, day, state)
                for name in ("exam_period", "low_social_signal", "help_waiting",
                             "post_support_recovery"):
                    if context[name]:
                        self.event(writer, day, sid, "scenario_trigger", "completed",
                                   detail={"name": name, "context": context})
            for actor, partner in writer.iter_query(
                    "SELECT actor,partner FROM campus_peer_invitations WHERE day=? AND response='timeout'",
                    (day - 1,)):
                if writer.query("SELECT 1 FROM campus_peer_response_queue "
                                "WHERE day=? AND actor=?", (day, actor), max_rows=1):
                    continue
                response = "accepted" if unit(self.config.seed, "peer_delayed_response",
                                               day, actor, partner) < 0.72 else "rejected"
                writer.execute("INSERT INTO campus_peer_response_queue VALUES(?,?,?,?)",
                               (day, actor, partner, response))
                self.event(writer, day, partner, "peer_response", response,
                           detail={"inviter": actor, "partner": partner,
                                   "delayed": True, "response_day": day,
                                   "basis": "delayed_timeout_followup_v1"})
                for sid, other in ((actor, partner), (partner, actor)):
                    body = writer.query("SELECT body FROM campus_students WHERE id=?",
                                        (sid,), max_rows=1)[0][0]
                    state = json.loads(body)
                    update_long_term_memory(state, {"day": day, "action": "peer_response",
                                                    "partner": other, "response": response})
                    writer.execute("UPDATE campus_students SET body=? WHERE id=?",
                                   (dumps(state), sid))
                if response == "accepted":
                    self.event(writer, day, partner, "peer_callback", "completed",
                               detail={"inviter": actor, "partner": partner,
                                       "source_day": day - 1,
                                       "relationship_type": relationship_type(actor, partner)})
        self.store.transaction(write)
        return load

    def model_decision(self, scope, target, detail):
        if target.key != scope.actor:
            return ActionResult("rejected", {"reason": "wrong_actor"})
        self.store.transaction(lambda writer: self.event(
            writer, scope.moment.time, scope.actor, "model_decision", "completed",
            detail={**detail, "period": self.current_period} if self.current_period else detail))
        return ActionResult("completed", {"action": detail["action"]})

    def peer_invite(self, scope, target):
        day, actor = scope.moment.time, scope.actor
        if target.key != actor:
            return ActionResult("rejected", {"reason": "wrong_actor"})

        def write(writer):
            period = self.current_period if self.config.periods_per_day == 3 else None
            if period:
                state_row = writer.query("SELECT body FROM campus_students WHERE id=?",
                                         (actor,), max_rows=1)
                state = json.loads(state_row[0][0])
                if "peer" not in period_available_actions(
                        state, day, period, self.config,
                        {"open": False, "queue_turn": False, "slots_remaining": 0}):
                    return {"status": "rejected", "reason": "period_unavailable"}
            existing = writer.query(
                "SELECT partner,response FROM campus_peer_invitations WHERE day=? AND actor=?",
                (day, actor), max_rows=1)
            if existing:
                partner, response = existing[0]
                if response == "accepted" and writer.query(
                        "SELECT 1 FROM campus_events WHERE day=? AND actor=? "
                        "AND action='peer' AND status='completed'", (day, actor), max_rows=1):
                    return {"status": "rejected", "partner": partner,
                            "response": response, "reason": "already_contacted_today"}
                return {"status": "completed" if response == "accepted" else "rejected",
                        "partner": partner, "response": response}
            delayed = writer.query(
                "SELECT partner,response FROM campus_peer_response_queue "
                "WHERE day=? AND actor=? AND response='accepted'",
                (day, actor), max_rows=1)
            if delayed:
                partner, response = delayed[0]
                writer.execute("INSERT INTO campus_peer_invitations VALUES(?,?,?,?)",
                               (day, actor, partner, response))
                self.event(writer, day, actor, "peer_invitation", "completed",
                           detail={"partner": partner, "response": response,
                                   "delayed": True, "basis": "delayed_timeout_followup_v1",
                                   "relationship_type": relationship_type(actor, partner)})
                return {"status": "completed", "partner": partner,
                        "response": response, "delayed": True}
            peers = writer.query(
                "SELECT CASE WHEN a=? THEN b ELSE a END FROM campus_edges "
                "WHERE a=? OR b=? ORDER BY 1", (actor, actor, actor))
            if not peers:
                self.event(writer, day, actor, "peer", "rejected", "no_peer_edge",
                           {"period": self.current_period} if self.current_period else None)
                return {"status": "rejected", "reason": "no_peer_edge"}
            recent = writer.query(
                "SELECT partner,response FROM campus_peer_invitations "
                "WHERE actor=? AND day>=? AND day<? AND response IN ('rejected','timeout')",
                (actor, day - 2, day))
            excluded = {partner for partner, _ in recent}
            peers = [row for row in peers if row[0] not in excluded]
            if not peers:
                self.event(writer, day, actor, "peer", "rejected", "invitation_cooldown",
                           {"period": self.current_period} if self.current_period else None)
                return {"status": "rejected", "reason": "invitation_cooldown"}
            partner = peers[int(unit(self.config.seed, "peer_partner", day, actor)
                                * len(peers))][0]
            rows = writer.query("SELECT body FROM campus_students WHERE id=?", (partner,),
                                max_rows=1)
            partner_state = json.loads(rows[0][0])
            relation = relationship_type(actor, partner)
            response = peer_response(self.config.seed, day, actor, partner, partner_state, relation)
            actor_state = json.loads(writer.query(
                "SELECT body FROM campus_students WHERE id=?", (actor,), max_rows=1)[0][0])
            actor_state["peer_attempt_day"] = day
            update_long_term_memory(actor_state,
                {"day": day, "action": "peer_invitation", "partner": partner,
                 "response": response})
            update_long_term_memory(partner_state,
                                    {"day": day, "action": "peer_response", "partner": actor,
                                     "response": response})
            writer.execute("UPDATE campus_students SET body=? WHERE id=?", (dumps(actor_state), actor))
            writer.execute("UPDATE campus_students SET body=? WHERE id=?",
                           (dumps(partner_state), partner))
            writer.execute("INSERT INTO campus_peer_invitations VALUES(?,?,?,?)",
                           (day, actor, partner, response))
            basis = "partner_state_availability_v2"
            self.event(writer, day, actor, "peer_invitation", "completed",
                       detail={"partner": partner, "response": response, "basis": basis,
                               "relationship_type": relation})
            self.event(writer, day, partner, "peer_response", response,
                       detail={"inviter": actor, "partner": partner, "basis": basis,
                               "relationship_type": relation})
            if response != "accepted":
                self.event(writer, day, actor, "peer", "rejected", response,
                           detail={"partner": partner, "partner_response": response,
                                   "response_reason": ("partner_unavailable" if response == "rejected"
                                                       else "no_reply_before_deadline"),
                                   **({"period": self.current_period}
                                      if self.current_period else {})})
            return {"status": "completed" if response == "accepted" else "rejected",
                    "partner": partner, "response": response,
                    "reason": response if response != "accepted" else None}

        result = self.store.transaction(write)
        return ActionResult(result["status"], result)

    def act(self, scope, target, action, args=None):
        day, actor = scope.moment.time, scope.actor
        args = args or {}
        if target.key != actor:
            return ActionResult("rejected", {"reason": "wrong_actor"})
        if action == "peer":
            invitation = self.peer_invite(scope, target)
            if invitation.status != "completed":
                return invitation
        if action == "peer" and self.config.driver == "llm":
            turns = _dialogue(json.dumps({"turns": args.get("turns")}), actor,
                              args.get("partner"))
            if not isinstance(args.get("usage"), dict) or not isinstance(
                    args.get("prompt_version"), str):
                raise ValueError("peer dialogue metadata is missing")
        def write(writer):
            rows = writer.query("SELECT body FROM campus_students WHERE id=?", (actor,), max_rows=1)
            if not rows:
                return {"status": "rejected", "reason": "unknown_actor"}
            state = json.loads(rows[0][0])
            period = self.current_period if self.config.periods_per_day == 3 else None
            if period:
                already = any(json.loads(detail).get("period") == period
                              for (detail,) in writer.iter_query(
                    "SELECT detail FROM campus_events WHERE day=? AND actor=? "
                    "AND action IN ('attend','rest','peer','request','accept_support')",
                    (day, actor)))
                if already:
                    return {"status": "rejected", "reason": "period_already_used"}
                service_open = day % policy_components(self.config)["service"]["open_every"] == 0
                head = writer.query(
                    "SELECT actor FROM campus_queue ORDER BY requested_day,ordinal LIMIT 1")
                used = writer.query("SELECT used FROM campus_service_usage WHERE day=?", (day,))
                remaining = min(self.available_slots(writer, day),
                                policy_components(self.config)["service"]["capacity"]) - (
                    used[0][0] if used else 0) - self.follow_up_slots_used(writer, day)
                available_actions = period_available_actions(
                    state, day, period, self.config,
                    {"open": service_open, "queue_turn": bool(head and head[0][0] == actor),
                     "slots_remaining": max(0, remaining)})
                if action == "peer" and state.get("peer_attempt_day") == day:
                    invitation = writer.query(
                        "SELECT response FROM campus_peer_invitations WHERE day=? AND actor=?",
                        (day, actor), max_rows=1)
                    if (invitation and invitation[0][0] == "accepted"
                            and "peer" not in available_actions):
                        available_actions.append("peer")
                if action not in available_actions:
                    self.event(writer, day, actor, action, "rejected", "period_unavailable",
                               {"period": period})
                    return {"status": "rejected", "reason": "period_unavailable"}
            before = {key: state[key] for key in ("stress", "mood", "recovery", "connection",
                                                   "academic_engagement", "self_efficacy",
                                                   "belonging", "help_seeking_belief", "social_avoidance")}
            reason = None
            if action == "request" and state["pending_day"] is not None:
                reason = "already_pending"
            if action == "request" and state["supported"]:
                reason = "already_supported"
            if action == "request" and period and state.get("service_attempt_day") == day:
                reason = "service_attempt_today"
            open_every = policy_components(self.config)["service"]["open_every"]
            if action == "request" and day % open_every != 0:
                reason = "service_closed"
            if (self.config.driver == "llm" and action == "request"
                    and not policy_context(self.config, state, day)["request_available_today"]):
                reason = "policy_unavailable"
            if action == "accept_support":
                first = writer.query("SELECT actor FROM campus_queue ORDER BY requested_day,ordinal LIMIT 1")
                used = writer.query("SELECT used FROM campus_service_usage WHERE day=?", (day,))
                assigned_staff = self.next_staff(writer, day)
                if state["pending_day"] is None:
                    reason = "no_pending_request"
                elif period and state.get("service_attempt_day") == day:
                    reason = "service_attempt_today"
                elif day % open_every != 0:
                    reason = "service_closed"
                elif not first or first[0][0] != actor:
                    reason = "queue_turn"
                elif (assigned_staff is None
                      or (used[0][0] if used else 0) + self.follow_up_slots_used(writer, day)
                      >= min(self.available_slots(writer, day),
                             policy_components(self.config)["service"]["capacity"])):
                    reason = "capacity_full"
            if action == "peer":
                peers = writer.query(
                    "SELECT CASE WHEN a=? THEN b ELSE a END FROM campus_edges "
                    "WHERE a=? OR b=? ORDER BY 1", (actor, actor, actor))
                if not peers:
                    reason = "no_peer_edge"
            if action == "attend":
                schedule = campus_calendar(self.config.seed, day, actor)["course"]
                context = {key: schedule[key] for key in ("course_id", "venue", "section")}
                bookings = writer.query(
                    "SELECT COUNT(*) FROM campus_venue_bookings "
                    "WHERE day=? AND venue=? AND slot=? AND activity=?",
                    (day, schedule["venue"], schedule["slot"],
                     schedule["course_id"]))[0][0]
                conflict = writer.query(
                    "SELECT 1 FROM campus_venue_bookings WHERE day=? AND actor=? AND slot=?",
                    (day, actor, schedule["slot"]), max_rows=1)
                if not schedule["open"]:
                    reason = "course_closed"
                elif conflict:
                    reason = "schedule_conflict"
                elif bookings >= schedule["capacity"]:
                    reason = "capacity_full"
            if reason:
                detail = ({"context": context, "schedule": schedule,
                           "booked_slots": bookings} if action == "attend" else None)
                self.event(writer, day, actor, action, "rejected", reason,
                           {**(detail or {}), "period": period} if period else detail)
                return {"status": "rejected", "reason": reason}
            if period and action in {"request", "accept_support"}:
                state["service_attempt_day"] = day
            if action == "request":
                ordinal = writer.query("SELECT COALESCE(MAX(ordinal),0)+1 FROM campus_queue")[0][0]
                writer.execute("INSERT INTO campus_queue VALUES(?,?,?)", (actor, day, ordinal))
                state["pending_day"] = day
                state["ever_requested"] = True
                state["help_seeking_belief"] = clamp(state["help_seeking_belief"] + 0.04)
            elif action == "accept_support":
                wait = day - state["pending_day"]
                state["pending_day"] = None
                state["wait_days"] += 1 if wait else 0
                state["supported"] = True
                state["ever_supported"] = True
                state["supported_day"] = day
                state["stress"] = clamp(state["stress"] - 0.1)
                state["mood"] = clamp(state["mood"] + 0.04)
                writer.execute("DELETE FROM campus_queue WHERE actor=?", (actor,))
                writer.execute("INSERT INTO campus_service_usage VALUES(?,1) ON CONFLICT(day) DO UPDATE SET used=used+1", (day,))
            elif action == "rest":
                state["recovery"] = clamp(state["recovery"] + 0.09)
                state["stress"] = clamp(state["stress"] - 0.025)
                state["mood"] = clamp(state["mood"] + 0.015)
            elif action == "peer":
                invitation = writer.query(
                    "SELECT partner,response FROM campus_peer_invitations WHERE day=? AND actor=?",
                    (day, actor), max_rows=1)
                partner = invitation[0][0]
                if self.config.driver == "llm" and args.get("partner") != partner:
                    raise ValueError("peer dialogue partner does not match selected partner")
                partner_rows = writer.query("SELECT body FROM campus_students WHERE id=?",
                                            (partner,), max_rows=1)
                partner_state = json.loads(partner_rows[0][0])
                left, right = sorted((actor, partner))
                rel = writer.query("SELECT strength,first_day,last_day,contacts,relationship_type FROM campus_relationships "
                                   "WHERE a=? AND b=?", (left, right))
                relation = relationship_type(actor, partner)
                if rel:
                    strength, first_day, _, contacts, relation = rel[0]
                    contacts += 1
                    strength = clamp(strength + {"roommate": 0.10, "club_member": 0.09,
                                                 "study_partner": 0.08, "friend": 0.12,
                                                 "acquaintance": 0.06}.get(relation, 0.06))
                else:
                    strength, first_day, contacts = {"roommate": 0.66, "club_member": 0.62,
                                                     "study_partner": 0.60, "friend": 0.70,
                                                     "acquaintance": 0.54}.get(relation, 0.54), day, 1
                writer.execute("INSERT INTO campus_relationships VALUES(?,?,?,?,?,?,?) "
                               "ON CONFLICT(a,b) DO UPDATE SET strength=?,last_day=?,contacts=?,relationship_type=?",
                               (left, right, strength, first_day, day, contacts, relation,
                                strength, day, contacts, relation))
                state["connection"] = clamp(state["connection"] + 0.045)
                state["mood"] = clamp(state["mood"] + 0.02)
                state["belonging"] = clamp(state["belonging"] + 0.025)
                state["social_avoidance"] = clamp(state["social_avoidance"] - 0.02)
            elif action == "attend":
                writer.execute("INSERT INTO campus_venue_bookings VALUES(?,?,?,?,?)",
                               (day, actor, schedule["venue"], schedule["slot"],
                                schedule["course_id"]))
                state["willingness"] = clamp(state["willingness"] + 0.005)
                state["academic_engagement"] = clamp(state["academic_engagement"] + 0.012)
                state["self_efficacy"] = clamp(state["self_efficacy"] + 0.006)
            else:
                reason = "unknown_action"
                self.event(writer, day, actor, action, "rejected", reason)
                return {"status": "rejected", "reason": reason}
            update_long_term_state(state, action=action, outcome="completed", day=day)
            writer.execute("UPDATE campus_students SET body=? WHERE id=?", (dumps(state), actor))
            if action == "peer":
                partner_state["connection"] = clamp(partner_state["connection"] + 0.02)
                partner_state["mood"] = clamp(partner_state["mood"] + 0.01)
                writer.execute("UPDATE campus_students SET body=? WHERE id=?",
                               (dumps(partner_state), partner))
            classmate = None
            if action == "attend":
                context = academic_context(self.config.seed, day, actor)
                arrivals = writer.query(
                    "SELECT actor,context FROM campus_interactions "
                    "WHERE day=? AND interaction='class_attendance' ORDER BY actor", (day,))
                classmates = [sid for sid, encoded in arrivals
                              if sid != actor and json.loads(encoded) == context]
                if classmates:
                    classmate = classmates[int(unit(self.config.seed, "classmate", day, actor)
                                              * len(classmates))]
            detail = ({"wait_days": wait, "staff_id": assigned_staff}
                      if action == "accept_support" else
                      {"partner": partner,
                       "turns": turns if self.config.driver == "llm" else
                           self.language.peer_dialogue(
                               seed=self.config.seed, day=day, actor=actor, partner=partner,
                               actor_state=state, partner_state=partner_state,
                               shared_memory=memory_context(
                                   state.get("social_memory", []), day)),
                       "backend": "llm" if self.config.driver == "llm" else self.language.backend,
                       **({"provider": self.config.model_provider,
                           "model": self.config.model_id,
                           "prompt_version": args["prompt_version"],
                           "usage": args["usage"]} if self.config.driver == "llm" else {})}
                      if action == "peer" else
                      {"context": academic_context(self.config.seed, day, actor),
                       "classmate": classmate,
                       "contact_rule": "same_course_prior_attendance_v1",
                       "schedule": schedule}
                      if action == "attend" else None)
            if action == "peer":
                detail["partner_response"] = "accepted"
                detail["delayed_response"] = bool(writer.query(
                    "SELECT 1 FROM campus_peer_response_queue "
                    "WHERE day=? AND actor=? AND partner=? AND response='accepted'",
                    (day, actor, partner), max_rows=1))
                detail["response_basis"] = "partner_edge_and_dialogue_completed_v1"
                detail["partner_state_before"] = {
                    key: partner_state[key] for key in ("mood", "connection", "stress")}
                detail["relationship"] = {"first_day": first_day, "last_day": day,
                                           "contacts": contacts, "strength": strength,
                                           "relationship_type": relation}
            memory = {"day": day, "action": action, "status": "completed"}
            if action == "peer":
                memory["partner"] = partner
                self.remember_social(state, {"day": day, "partner": partner, "action": "peer"})
                self.remember_social(partner_state, {"day": day, "partner": actor, "action": "peer"})
                update_long_term_memory(partner_state,
                                        {"day": day, "action": "peer", "partner": actor})
                writer.execute("UPDATE campus_students SET body=? WHERE id=?",
                               (dumps(partner_state), partner))
            self.remember(state, memory)
            group_detail = None
            residence_detail = None
            if action == "attend":
                context = academic_context(self.config.seed, day, actor)
                ordinal = writer.query(
                    "SELECT COALESCE(MAX(ordinal),0)+1 FROM campus_interactions")[0][0]
                writer.execute("INSERT INTO campus_interactions VALUES(?,?,?,?,?,?)",
                               (ordinal, day, actor, classmate, "class_attendance", dumps(context)))
                prior = writer.query(
                    "SELECT actor,context FROM campus_interactions "
                    "WHERE day=? AND interaction='class_attendance' AND actor!=? ORDER BY ordinal",
                    (day, actor))
                group = [sid for sid, encoded in prior if json.loads(encoded) == context]
                if len(group) == 2:
                    participants = [*group, actor]
                    group_detail = {"participants": participants, "context": context,
                                    "trigger": "third_same_course_arrival_v1"}
                    writer.execute("INSERT INTO campus_interactions VALUES(?,?,?,?,?,?)",
                                   (ordinal + 1, day, actor, None, "study_group",
                                    dumps(group_detail)))
                if classmate:
                    partner_rows = writer.query("SELECT body FROM campus_students WHERE id=?",
                                                (classmate,), max_rows=1)
                    partner_state = json.loads(partner_rows[0][0])
                    partner_state["connection"] = clamp(partner_state["connection"] + 0.004)
                    writer.execute("UPDATE campus_students SET body=? WHERE id=?",
                                   (dumps(partner_state), classmate))
            elif action == "rest" and (not period or period == "evening"):
                residence = campus_calendar(self.config.seed, day, actor)["residence"]
                arrivals = writer.query(
                    "SELECT actor,context FROM campus_interactions "
                    "WHERE day=? AND interaction='residence_presence' ORDER BY ordinal", (day,))
                roommates = [sid for sid, encoded in arrivals
                             if sid != actor and json.loads(encoded).get("venue") == residence["venue"]]
                ordinal = writer.query(
                    "SELECT COALESCE(MAX(ordinal),0)+1 FROM campus_interactions")[0][0]
                writer.execute("INSERT INTO campus_interactions VALUES(?,?,?,?,?,?)",
                               (ordinal, day, actor, None, "residence_presence",
                                dumps(residence_context(actor))))
                residence_detail = {"context": residence_context(actor), "roommates": roommates,
                                    "trigger": "shared_residence_presence_v1"}
            writer.execute("UPDATE campus_students SET body=? WHERE id=?", (dumps(state), actor))
            if action == "peer":
                feedback = {key: round(state[key] - before[key], 6)
                            for key in before if state[key] != before[key]}
                feedback["partner_connection_delta"] = 0.02
                feedback["partner_mood_delta"] = 0.01
            else:
                feedback = {key: round(state[key] - before[key], 6)
                            for key in before if state[key] != before[key]}
            if detail is None:
                detail = {}
            if period:
                detail["period"] = period
            if residence_detail is not None:
                detail.update(residence_detail)
            detail["feedback"] = feedback
            self.event(writer, day, actor, action, "completed",
                       detail=detail)
            if group_detail is not None:
                self.event(writer, day, actor, "study_group", "completed",
                           detail=group_detail)
            return {"status": "completed", "action": action}
        result = self.store.transaction(write)
        return ActionResult(result["status"], result)

    def settle(self, day, load):
        def write(writer):
            usage = writer.query("SELECT used FROM campus_service_usage WHERE day=?", (day,))
            served = usage[0][0] if usage else 0
            available = self.available_slots(writer, day)
            if served + self.follow_up_slots_used(writer, day) > available:
                raise ValueError("staff usage exceeds registered staff slots")
            follow_up = policy_components(self.config)["follow_up"]
            follow_used = self.follow_up_slots_used(writer, day)
            if follow_up["enabled"]:
                candidates = []
                for sid, body in writer.iter_query("SELECT id,body FROM campus_students"):
                    state = json.loads(body)
                    due = (state["supported_day"] is not None
                           and not state["follow_up_sent"]
                           and day - state["supported_day"] >= follow_up["days_after_support"])
                    if due:
                        candidates.append((state["supported_day"], sid, state))
                for _, sid, state in sorted(candidates):
                    if served + follow_used >= available:
                        break
                    assigned_staff = self.next_staff(writer, day)
                    if assigned_staff is None:
                        break
                    state["follow_up_sent"] = True
                    state["stress"] = clamp(state["stress"] - 0.04)
                    state["mood"] = clamp(state["mood"] + 0.03)
                    self.remember(state, {"day": day, "action": "staff_follow_up", "status": "completed"})
                    writer.execute("UPDATE campus_students SET body=? WHERE id=?", (dumps(state), sid))
                    follow_used += 1
                    self.event(writer, day, sid, "staff_follow_up", "completed",
                               detail={"days_after_support": follow_up["days_after_support"],
                                       "service_slot": served + follow_used,
                                       "staff_id": assigned_staff})
                if follow_used != self.follow_up_slots_used(writer, day):
                    writer.execute("INSERT INTO campus_follow_up_usage VALUES(?,?) ON CONFLICT(day) DO UPDATE SET used=?",
                                   (day, follow_used, follow_used))
            states = []
            before_states = []
            missed_courses = 0
            for actor, body in list(writer.iter_query("SELECT id,body FROM campus_students ORDER BY id")):
                state = json.loads(body)
                before_states.append(state.copy())
                course = campus_calendar(self.config.seed, day, actor)["course"]
                absence_streak = state.get("missed_course_streak", 0)
                if course["open"]:
                    present = writer.query(
                        "SELECT 1 FROM campus_venue_bookings WHERE day=? AND actor=? "
                        "AND activity=?", (day, actor, course["course_id"]), max_rows=1)
                    if present:
                        absence_streak = 0
                    else:
                        absence_streak += 1
                        missed_courses += 1
                        self.event(writer, day, actor, "course_absence", "completed",
                                   detail={"course_id": course["course_id"],
                                           "missed_course_streak": absence_streak,
                                           "venue": course["venue"]})
                        update_long_term_memory(state, {"day": day, "action": "course_absence",
                                                        "event_type": "academic_setback",
                                                        "impact": 0.22})
                state["missed_course_streak"] = absence_streak
                if state["pending_day"] is not None and day > state["pending_day"]:
                    state["wait_days"] += 1
                state["stress"] = next_stress(state["stress"], state["recovery"],
                                               load, state["supported"])
                state["mood"] = next_mood(state["mood"], state["stress"],
                                          state["recovery"], state["connection"] >= 0.3)
                state["recovery"] = clamp(state["recovery"] - 0.025)
                state["connection"] = clamp(state["connection"] - 0.012)
                state["academic_engagement"] = clamp(state["academic_engagement"] +
                                                      (0.004 if state["recent_events"] and
                                                       state["recent_events"][-1].get("action") == "attend" else -0.003))
                state["self_efficacy"] = clamp(state["self_efficacy"] +
                                                (0.002 if state["academic_engagement"] >= 0.5 else -0.002))
                state["belonging"] = clamp(state["belonging"] +
                                            (0.003 if state["connection"] >= 0.3 else -0.004))
                state["help_seeking_belief"] = clamp(state["help_seeking_belief"] +
                                                      (0.004 if state["supported"] else -0.001))
                state["social_avoidance"] = clamp(state["social_avoidance"] +
                                                   (0.004 if state["connection"] < 0.3 else -0.002))
                update_long_term_state(state, action=("course_absence" if absence_streak else
                                                      state.get("recent_events", [{}])[-1].get("action")),
                                       outcome="completed", day=day)
                state["low_streak"] = state["low_streak"] + 1 if state["connection"] < 0.3 else 0
                decay_long_term_memory(state, day)
                writer.execute("UPDATE campus_students SET body=? WHERE id=?", (dumps(state), actor))
                self.event(writer, day, actor, "student_state", "completed", detail={
                    "phase": "end_of_day_v1", "stress": state["stress"],
                    "mood": state["mood"], "connection": state["connection"],
                    "academic_engagement": state["academic_engagement"],
                    "self_efficacy": state["self_efficacy"],
                    "belonging": state["belonging"],
                    "help_seeking_belief": state["help_seeking_belief"],
                    "social_avoidance": state["social_avoidance"],
                    "memory_action_counts": state["long_term_memory"]["action_counts"],
                    "memory_partner_traces": state["long_term_memory"]["partners"],
                    "missed_course_streak": state["missed_course_streak"],
                    "supported": state["supported"]})
                states.append(state)
            rel_decay = 0
            for left, right, strength, first_day, last_day, contacts, relation_kind in writer.iter_query(
                    "SELECT a,b,strength,first_day,last_day,contacts,relationship_type FROM campus_relationships"):
                if last_day is not None and day > last_day + 1:
                    decay = {"roommate": 0.012, "club_member": 0.018,
                             "study_partner": 0.02, "friend": 0.015,
                             "acquaintance": 0.025}.get(relation_kind, 0.025)
                    updated = clamp(strength - decay * (day - last_day - 1))
                    if updated != strength:
                        rel_decay += 1
                        writer.execute("UPDATE campus_relationships SET strength=? WHERE a=? AND b=?",
                                       (updated, left, right))
            metrics = {
                "day": day, "academic_load": load, "requested": sum(s["ever_requested"] for s in states),
                "supported": sum(s["ever_supported"] for s in states), "served_today": served,
                "available_slots": available,
                "follow_ups_today": follow_used,
                "pending": sum(s["pending_day"] is not None for s in states),
                "missed_courses_today": missed_courses,
                "low_connection_7d": sum(s["low_streak"] >= 7 for s in states),
                "mean_stress": round(sum(s["stress"] for s in states) / len(states), 6),
                "mean_mood": round(sum(s["mood"] for s in states) / len(states), 6),
                "long_term_state_means": {key: round(sum(s[key] for s in states) / len(states), 6)
                                          for key in ("academic_engagement", "self_efficacy",
                                                      "belonging", "help_seeking_belief",
                                                      "social_avoidance")},
                "mean_stress_change": round(sum(s["stress"] - s["initial_stress"]
                                                 for s in states) / len(states), 6),
                "mean_stress_before": round(sum(s["stress"] for s in before_states) / len(states), 6),
                "mean_mood_before": round(sum(s["mood"] for s in before_states) / len(states), 6),
                "mean_stress_delta": round(sum(s["stress"] - b["stress"]
                                                for s, b in zip(states, before_states)) / len(states), 6),
                "mean_mood_delta": round(sum(s["mood"] - b["mood"]
                                              for s, b in zip(states, before_states)) / len(states), 6),
                "relationship_decay_updates": rel_decay,
                "scenario": {"exam_period": 4 - ((day - 1) % 7) <= 2,
                              "exam_days_remaining": 4 - ((day - 1) % 7),
                              "help_waiting_students": sum(s["pending_day"] is not None for s in states),
                              "post_support_recovery_students": sum(
                                  s["supported_day"] is not None and day - s["supported_day"] <= 3
                                  for s in states)},
            }
            writer.execute("INSERT INTO campus_days VALUES(?,?)", (day, dumps(metrics)))
            return metrics
        return self.store.transaction(write)


class CampusInformation:
    def __init__(self, campus):
        self.campus = campus

    def ref(self, path):
        parts = path.strip("/").split("/")
        if len(parts) == 2 and parts[0] == "students":
            return Ref("campus", "student", parts[1])
        if path == "/service":
            return Ref("campus", "service", "main")
        if path == "/course":
            return Ref("campus", "course", "main")
        if len(parts) == 2 and parts[0] == "peers":
            return Ref("campus", "peer", parts[1])
        if len(parts) == 2 and parts[0] == "peer_dialogue":
            return Ref("campus", "student", parts[1])
        return None

    def read(self, scope, path, *, offset=0, size=65536):
        ref = self.ref(path)
        if ref is None:
            raise LookupError(path)
        if path.startswith("/peer_dialogue/") and ref.key != scope.actor:
            raise PermissionError("peer dialogue context belongs to another actor")
        if path.startswith("/peer_dialogue/"):
            def partner_context(view):
                invitation = view.query(
                    "SELECT partner FROM campus_peer_invitations "
                    "WHERE day=? AND actor=? AND response='accepted'",
                    (scope.moment.time, ref.key), max_rows=1)
                if not invitation:
                    raise ValueError("peer dialogue requires an accepted invitation")
                partner = invitation[0][0]
                body = view.query("SELECT body FROM campus_students WHERE id=?", (partner,),
                                  max_rows=1)[0][0]
                state = json.loads(body)
                return {"partner": partner, "partner_state": {
                    key: state[key] for key in ("stress", "mood", "connection", "recent_events",
                                             "social_memory", "long_term_memory")}}
            data = self.campus.store.read(partner_context)
        elif ref.kind == "student":
            state = self.campus.student(ref.key)
            data = {key: state[key] for key in ("id", "stress", "mood", "recovery", "connection", "willingness",
                                                  "academic_engagement", "self_efficacy", "belonging",
                                                  "help_seeking_belief", "social_avoidance",
                                                  "missed_course_streak",
                                                  "supported", "pending_day", "outreach_seen",
                                                  "initial_group", "recent_events",
                                                  "social_memory", "long_term_memory",
                                                  "supported_day", "follow_up_sent")}
            data["memory_context"] = {
                "recent_events": memory_context(state["recent_events"], scope.moment.time),
                "social_memory": memory_context(state["social_memory"], scope.moment.time),
            }
        elif ref.kind == "service":
            components = policy_components(self.campus.config)
            barrier = intervention_effects(self.campus.config, self.campus.student(scope.actor),
                                           scope.moment.time)["effective_access_barrier"]
            def queue_status(view):
                first = view.query("SELECT actor FROM campus_queue ORDER BY requested_day,ordinal LIMIT 1")
                used = view.query("SELECT used FROM campus_service_usage WHERE day=?",
                                  (scope.moment.time,))
                remaining = min(components["service"]["capacity"],
                                self.campus.available_slots(view, scope.moment.time)) - (
                    used[0][0] if used else 0) - self.campus.follow_up_slots_used(
                        view, scope.moment.time)
                return {"queue_turn": bool(first and first[0][0] == scope.actor),
                        "slots_remaining": max(0, remaining)}
            data = {"open": scope.moment.time % components["service"]["open_every"] == 0,
                    "period": self.campus.current_period,
                    "capacity": components["service"]["capacity"],
                    "access_barrier": barrier,
                    "available_slots": self.campus.store.read(lambda view:
                        self.campus.available_slots(view, scope.moment.time)),
                    **self.campus.store.read(queue_status)}
        elif ref.kind == "course":
            calendar = campus_calendar(self.campus.config.seed, scope.moment.time, scope.actor)
            scheduled = calendar["course"]
            occupied = self.campus.store.read(lambda view: view.query(
                "SELECT COUNT(*) FROM campus_venue_bookings "
                "WHERE day=? AND venue=? AND slot=? AND activity=?",
                (scope.moment.time, scheduled["venue"], scheduled["slot"],
                 scheduled["course_id"]))[0][0])
            data = {"day": scope.moment.time,
                    "academic_load": academic_event(self.campus.config.seed, scope.moment.time),
                    "context": academic_context(self.campus.config.seed, scope.moment.time, scope.actor),
                    "calendar": calendar, "course_available_slots": max(
                        0, scheduled["capacity"] - occupied) if scheduled["open"] else 0,
                    "scenario": scenario_context(self.campus.config.seed, scope.moment.time,
                                                  self.campus.student(scope.actor))}
        else:
            data = {"peer_interactions_today": self.campus.store.read(lambda view: [row[0] for row in
                view.iter_query("SELECT DISTINCT e.actor FROM campus_events e JOIN campus_edges t ON (t.a=? AND t.b=e.actor) OR (t.b=? AND t.a=e.actor) WHERE e.day=? AND e.action='peer' AND e.status='completed' ORDER BY e.actor",
                                (ref.key, ref.key, scope.moment.time))])}
        body = dumps(data).encode()
        revision = self.campus.store.read(lambda view: view.live_revision)
        return DocumentChunk(body[offset:offset + size], len(body),
                             offset + size if offset + size < len(body) else None, revision, ref)


def campus_plugin(config):
    population, edges = make_population(config)
    staff = make_staff(config)
    def initialize(writer):
        for state in population:
            writer.execute("INSERT INTO campus_students VALUES(?,?)", (state["id"], dumps(state)))
        for member in staff:
            writer.execute("INSERT INTO campus_staff VALUES(?,?)", (member["id"], dumps(member)))
        for a, b in edges:
            writer.execute("INSERT OR IGNORE INTO campus_edges VALUES(?,?)", (a, b))
            left, right = sorted((a, b))
            writer.execute("INSERT OR IGNORE INTO campus_relationships VALUES(?,?,?,?,?,?,?)",
                           (left, right, 0.5, None, None, 0, relationship_type(a, b)))
    def install(context):
        campus = Campus(context.require("storage", "store"), config)
        information = context.require("interaction", "information")
        actions = context.require("interaction", "actions")
        information.mount("/students", CampusInformation(campus))
        information.mount("/service", CampusInformation(campus))
        information.mount("/course", CampusInformation(campus))
        information.mount("/peers", CampusInformation(campus))
        information.mount("/peer_dialogue", CampusInformation(campus))
        schema = {"type": "object", "properties": {}, "additionalProperties": False}
        for name in ("attend", "rest", "peer", "request", "accept_support"):
            action_schema = ({"type": "object", "properties": {
                "partner": {"type": "string"}, "turns": {"type": "array"},
                "usage": {"type": "object"}, "prompt_version": {"type": "string"}},
                "required": ["partner", "turns", "usage", "prompt_version"]
                    if config.driver == "llm" else [],
                "additionalProperties": False} if name == "peer" else schema)
            actions.register(Action("campus." + name, ("campus", "student"), name, action_schema,
                lambda scope, target, args, name=name: campus.act(scope, target, name, args),
                available=lambda scope, target: target.key == scope.actor),
                dependencies=("campus_students", "campus_queue", "campus_edges",
                              "campus_relationships", "campus_peer_invitations",
                              "campus_peer_response_queue", "campus_venue_bookings",
                              "campus_service_usage", "campus_follow_up_usage",
                              "campus_interactions"))
        actions.register(Action("campus.staff_shift", ("campus", "staff"), "staff_shift", schema,
            lambda scope, target, args: campus.staff_shift(scope, target),
            available=lambda scope, target: target.key == scope.actor),
            dependencies=("campus_staff", "campus_staff_shifts"))
        decision_schema = {"type": "object", "properties": {
            "action": {"type": "string"}, "provider": {"type": "string"},
            "model": {"type": "string"}, "temperature": {"type": "number"},
            "prompt_version": {"type": "string"}, "decision_context": {"type": "object"},
            "policy": {"type": "object"}, "usage": {"type": "object"},
            "period": {"type": "string"}},
            "required": ["action", "provider", "model", "temperature", "prompt_version",
                         "decision_context", "policy", "usage"],
            "additionalProperties": False}
        actions.register(Action("campus.model_decision", ("campus", "student"),
            "model_decision", decision_schema,
            lambda scope, target, args: campus.model_decision(scope, target, args),
            available=lambda scope, target: target.key == scope.actor),
            dependencies=("campus_events",))
        actions.register(Action("campus.peer_invite", ("campus", "student"),
            "peer_invite", schema,
            lambda scope, target, args: campus.peer_invite(scope, target),
            available=lambda scope, target: target.key == scope.actor),
            dependencies=("campus_students", "campus_edges", "campus_peer_invitations",
                          "campus_peer_response_queue",
                          "campus_events"))
        context.provide("campus", campus)
    return Plugin("campus", ("storage", "interaction"), install, schema=SCHEMA, initialize=initialize)
