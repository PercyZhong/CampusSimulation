"""Society0 owned campus facts and actor bound actions."""

import json

from society0.kernel.interaction import Action, ActionResult, DocumentChunk, Ref
from society0.kernel.plugins import Plugin

from .domain import academic_event, clamp, next_mood, next_stress
from .language import language_backend
from .population import make_population, make_staff

SCHEMA = (
    "CREATE TABLE campus_students(id TEXT NOT NULL PRIMARY KEY, body TEXT NOT NULL)",
    "CREATE TABLE campus_staff(id TEXT NOT NULL PRIMARY KEY, body TEXT NOT NULL)",
    ("CREATE TABLE campus_staff_shifts(day INTEGER NOT NULL,actor TEXT NOT NULL,"
     "slots INTEGER NOT NULL,PRIMARY KEY(day,actor))"),
    "CREATE TABLE campus_edges(a TEXT NOT NULL,b TEXT NOT NULL,PRIMARY KEY(a,b))",
    "CREATE TABLE campus_queue(actor TEXT NOT NULL PRIMARY KEY, requested_day INTEGER NOT NULL, ordinal INTEGER NOT NULL UNIQUE)",
    "CREATE TABLE campus_service_usage(day INTEGER PRIMARY KEY, used INTEGER NOT NULL)",
    "CREATE TABLE campus_events(ordinal INTEGER PRIMARY KEY,day INTEGER NOT NULL,actor TEXT NOT NULL,action TEXT NOT NULL,status TEXT NOT NULL,reason TEXT,detail TEXT NOT NULL)",
    "CREATE TABLE campus_days(day INTEGER PRIMARY KEY,body TEXT NOT NULL)",
)


def dumps(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class Campus:
    def __init__(self, store, config):
        self.store, self.config = store, config
        self.language = language_backend(config.language_backend)

    def student(self, actor):
        def read(view):
            rows = view.query("SELECT body FROM campus_students WHERE id=?", (actor,), max_rows=1)
            return json.loads(rows[0][0]) if rows else None
        return self.store.read(read)

    def students(self):
        return self.store.read(lambda view: [json.loads(row[0]) for row in
            view.iter_query("SELECT body FROM campus_students ORDER BY id")])

    def available_slots(self, view, day):
        rows = view.query("SELECT COALESCE(SUM(slots),0) FROM campus_staff_shifts WHERE day=?",
                          (day,))
        return min(self.config.capacity, rows[0][0])

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
            if self.config.condition == "outreach" and day == self.config.outreach_day:
                # Every synthetic actor sees general service information; no private-state targeting.
                for sid, body in list(writer.iter_query("SELECT id,body FROM campus_students ORDER BY id")):
                    state = json.loads(body)
                    state["outreach_seen"] = True
                    writer.execute("UPDATE campus_students SET body=? WHERE id=?", (dumps(state), sid))
                    self.event(writer, day, sid, "outreach_information", "completed")
        self.store.transaction(write)
        return load

    def act(self, scope, target, action):
        day, actor = scope.moment.time, scope.actor
        if target.key != actor:
            return ActionResult("rejected", {"reason": "wrong_actor"})
        def write(writer):
            rows = writer.query("SELECT body FROM campus_students WHERE id=?", (actor,), max_rows=1)
            if not rows:
                return {"status": "rejected", "reason": "unknown_actor"}
            state = json.loads(rows[0][0])
            reason = None
            if action == "request" and state["pending_day"] is not None:
                reason = "already_pending"
            if action == "request" and state["supported"]:
                reason = "already_supported"
            if action == "request" and day % self.config.open_every != 0:
                reason = "service_closed"
            if action == "accept_support":
                first = writer.query("SELECT actor FROM campus_queue ORDER BY requested_day,ordinal LIMIT 1")
                used = writer.query("SELECT used FROM campus_service_usage WHERE day=?", (day,))
                if state["pending_day"] is None:
                    reason = "no_pending_request"
                elif day % self.config.open_every != 0:
                    reason = "service_closed"
                elif not first or first[0][0] != actor:
                    reason = "queue_turn"
                elif (used[0][0] if used else 0) >= self.available_slots(writer, day):
                    reason = "capacity_full"
            if action == "peer":
                peers = writer.query("SELECT 1 FROM campus_edges WHERE a=? OR b=? LIMIT 1", (actor, actor))
                if not peers:
                    reason = "no_peer_edge"
            if reason:
                self.event(writer, day, actor, action, "rejected", reason)
                return {"status": "rejected", "reason": reason}
            if action == "request":
                ordinal = writer.query("SELECT COALESCE(MAX(ordinal),0)+1 FROM campus_queue")[0][0]
                writer.execute("INSERT INTO campus_queue VALUES(?,?,?)", (actor, day, ordinal))
                state["pending_day"] = day
                state["ever_requested"] = True
            elif action == "accept_support":
                wait = day - state["pending_day"]
                state["pending_day"] = None
                state["wait_days"] += 1 if wait else 0
                state["supported"] = True
                state["ever_supported"] = True
                state["stress"] = clamp(state["stress"] - 0.1)
                writer.execute("DELETE FROM campus_queue WHERE actor=?", (actor,))
                writer.execute("INSERT INTO campus_service_usage VALUES(?,1) ON CONFLICT(day) DO UPDATE SET used=used+1", (day,))
            elif action == "rest":
                state["recovery"] = clamp(state["recovery"] + 0.09)
            elif action == "peer":
                state["connection"] = clamp(state["connection"] + 0.045)
            elif action == "attend":
                state["willingness"] = clamp(state["willingness"] + 0.005)
            else:
                reason = "unknown_action"
                self.event(writer, day, actor, action, "rejected", reason)
                return {"status": "rejected", "reason": reason}
            writer.execute("UPDATE campus_students SET body=? WHERE id=?", (dumps(state), actor))
            detail = ({"wait_days": wait} if action == "accept_support" else
                      {"message": self.language.peer_message(seed=self.config.seed, day=day,
                                                              actor=actor), "backend": "offline"}
                      if action == "peer" else None)
            self.event(writer, day, actor, action, "completed",
                       detail=detail)
            return {"status": "completed", "action": action}
        result = self.store.transaction(write)
        return ActionResult(result["status"], result)

    def settle(self, day, load):
        def write(writer):
            usage = writer.query("SELECT used FROM campus_service_usage WHERE day=?", (day,))
            served = usage[0][0] if usage else 0
            available = self.available_slots(writer, day)
            if served > available:
                raise ValueError("service usage exceeds registered staff slots")
            states = []
            for actor, body in list(writer.iter_query("SELECT id,body FROM campus_students ORDER BY id")):
                state = json.loads(body)
                if state["pending_day"] is not None and day > state["pending_day"]:
                    state["wait_days"] += 1
                state["stress"] = next_stress(state["stress"], state["recovery"],
                                               load, state["supported"])
                state["mood"] = next_mood(state["mood"], state["stress"],
                                          state["recovery"], state["connection"] >= 0.3)
                state["recovery"] = clamp(state["recovery"] - 0.025)
                state["connection"] = clamp(state["connection"] - 0.012)
                state["low_streak"] = state["low_streak"] + 1 if state["connection"] < 0.3 else 0
                writer.execute("UPDATE campus_students SET body=? WHERE id=?", (dumps(state), actor))
                states.append(state)
            metrics = {
                "day": day, "academic_load": load, "requested": sum(s["ever_requested"] for s in states),
                "supported": sum(s["ever_supported"] for s in states), "served_today": served,
                "available_slots": available,
                "pending": sum(s["pending_day"] is not None for s in states),
                "low_connection_7d": sum(s["low_streak"] >= 7 for s in states),
                "mean_stress": round(sum(s["stress"] for s in states) / len(states), 6),
                "mean_mood": round(sum(s["mood"] for s in states) / len(states), 6),
                "mean_stress_change": round(sum(s["stress"] - s["initial_stress"]
                                                 for s in states) / len(states), 6),
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
        return None

    def read(self, scope, path, *, offset=0, size=65536):
        ref = self.ref(path)
        if ref is None:
            raise LookupError(path)
        if ref.kind == "student":
            state = self.campus.student(ref.key)
            data = {key: state[key] for key in ("id", "stress", "mood", "recovery", "connection", "willingness",
                                                  "supported", "pending_day", "outreach_seen")}
        elif ref.kind == "service":
            data = {"open": scope.moment.time % self.campus.config.open_every == 0,
                    "capacity": self.campus.config.capacity,
                    "available_slots": self.campus.store.read(lambda view:
                        self.campus.available_slots(view, scope.moment.time))}
        elif ref.kind == "course":
            data = {"day": scope.moment.time,
                    "academic_load": academic_event(self.campus.config.seed, scope.moment.time)}
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
    def install(context):
        campus = Campus(context.require("storage", "store"), config)
        information = context.require("interaction", "information")
        actions = context.require("interaction", "actions")
        information.mount("/students", CampusInformation(campus))
        information.mount("/service", CampusInformation(campus))
        information.mount("/course", CampusInformation(campus))
        information.mount("/peers", CampusInformation(campus))
        schema = {"type": "object", "properties": {}, "additionalProperties": False}
        for name in ("attend", "rest", "peer", "request", "accept_support"):
            actions.register(Action("campus." + name, ("campus", "student"), name, schema,
                lambda scope, target, args, name=name: campus.act(scope, target, name),
                available=lambda scope, target: target.key == scope.actor),
                dependencies=("campus_students", "campus_queue", "campus_edges", "campus_service_usage"))
        actions.register(Action("campus.staff_shift", ("campus", "staff"), "staff_shift", schema,
            lambda scope, target, args: campus.staff_shift(scope, target),
            available=lambda scope, target: target.key == scope.actor),
            dependencies=("campus_staff", "campus_staff_shifts"))
        context.provide("campus", campus)
    return Plugin("campus", ("storage", "interaction"), install, schema=SCHEMA, initialize=initialize)
