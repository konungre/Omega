"""Agent-thread bridge. HTTP handlers never evaluate MeTTa or execute tools."""
import json
import threading
import uuid

from .store import APIError, Store, now, require, text


class Runtime:
    def __init__(self, path):
        self.store = Store(path)
        self.lock = threading.RLock()
        self.connected = False
        self.run_id = None
        self.request_id = None
        self.failed = False
        self.server = None

    def agent(self):
        with self.lock:
            return {"connected": self.connected, "status": "working" if self.run_id else "idle" if self.connected else "offline",
                    "run_id": self.run_id, "request_id": self.request_id}

    def receive(self):
        with self.lock:
            if self.request_id:
                return ""
            record = self.store.claim_message()
            if record:
                self.request_id = record["id"]
                return record["payload"]["text"]
            return ""

    def allow_turn(self, has_input):
        return (has_input is True or has_input == "input") or self.store.get("config", "operator")["autonomy_enabled"]

    def context(self):
        if not self.store.get("config", "operator")["goal_context_enabled"]:
            return ""
        goals = [g for g in self.store.all("goals") if g["status"] == "active"]
        # Plain data, never executable MeTTa; bounded to avoid unbounded prompt growth.
        return "\nOPERATOR GOALS (active only; report evidence and improvement outcomes using dashboard skills):\n" + json.dumps(
            [{"id": g["id"], "title": g["title"], "description": g["description"]} for g in goals[:20]], ensure_ascii=False)[:30000]

    def begin_turn(self):
        with self.lock, self.store.transaction():
            if self.run_id:
                return
            self.run_id, self.failed = str(uuid.uuid4()), False
            run = {"id": self.run_id, "request_id": self.request_id, "kind": "conversation" if self.request_id else "autonomous",
                   "status": "running", "started_at": now(),
                   "goal_ids": [g["id"] for g in self.store.all("goals") if g["status"] == "active"]}
            self.store.put("runs", run)
            self.store.event("run.started", run, self.request_id, self.run_id)

    def provider_result(self, response):
        with self.lock, self.store.transaction():
            if response.is_error():
                self.failed = True
                self.store.event("provider.failed", {"message": str(response.error)[:12000]}, self.request_id, self.run_id)
            else:
                self.store.event("provider.completed", {"tool_calls": [
                    {"id": call.id, "name": call.name, "error": call.error} for call in response.calls
                ]}, self.request_id, self.run_id)

    def tool_result(self, call, result):
        with self.lock, self.store.transaction():
            content = str(result)
            failed = not content.startswith("SUCCESS, RETURN: ")
            self.failed |= failed
            evidence = {"id": str(uuid.uuid4()), "kind": "tool_result", "title": str(call)[:300],
                        "content": content[:50000], "truncated": len(content) > 50000,
                        "status": "failed" if failed else "recorded", "at": now(),
                        "request_id": self.request_id, "run_id": self.run_id}
            self.store.put("evidence", evidence)
            self.store.event("evidence.created", evidence, self.request_id, self.run_id)
            return "recorded"

    def report(self, kind, raw):
        """Explicit, structured agent reports; never infer improvement from a tool name."""
        try:
            report = json.loads(str(raw))
            require(isinstance(report, dict), "Report must be a JSON object")
            with self.lock, self.store.transaction():
                require(self.run_id is not None, "Reports require an active agent turn", 409)
                if kind == "evidence":
                    require(set(report) == {"title", "content"}, "Evidence expects title and content")
                    record = {"title": text(report["title"], "title", 300),
                              "content": text(report["content"], "content", 50000),
                              "kind": "agent_report", "status": "reported"}
                else:
                    require(set(report) == {"title", "summary", "outcome", "evidence_ids"},
                            "Improvement expects title, summary, outcome, evidence_ids")
                    require(report["outcome"] in ("improved", "unchanged", "regressed", "inconclusive"), "Invalid outcome")
                    refs = report["evidence_ids"]
                    require(isinstance(refs, list) and 0 < len(refs) <= 50 and all(isinstance(r, str) and self.store.get("evidence", r) for r in refs),
                            "Reference 1–50 existing evidence IDs")
                    record = {"title": text(report["title"], "title", 300), "summary": text(report["summary"], "summary"),
                              "outcome": report["outcome"], "evidence_ids": refs, "attribution": "agent_report"}
                record.update(id=str(uuid.uuid4()), at=now(), request_id=self.request_id, run_id=self.run_id)
                self.store.put("evidence" if kind == "evidence" else "improvements", record)
                self.store.event(f"{kind}.created", record, self.request_id, self.run_id)
                return json.dumps(record, ensure_ascii=False)
        except (ValueError, TypeError, APIError) as exc:
            return json.dumps({"error": str(exc)})

    def send(self, message):
        content = str(message).replace("\\n", "\n").replace("\r", "")
        if not content:
            return
        with self.lock, self.store.transaction():
            record = {"id": str(uuid.uuid4()), "role": "assistant", "text": content,
                      "at": now(), "run_id": self.run_id, "request_id": self.request_id}
            self.store.put("messages", record)
            self.store.event("message.created", record, self.request_id, self.run_id)

    def end_turn(self):
        with self.lock, self.store.transaction():
            if self.run_id:
                run = self.store.get("runs", self.run_id)
                run.update(status="failed" if self.failed else "completed", finished_at=now())
                self.store.put("runs", run)
                self.store.event(f"run.{run['status']}", run, self.request_id, self.run_id)
            if self.request_id:
                self.store.finish(self.request_id, "failed" if self.failed else "completed", {"run_id": self.run_id})
            self.run_id = self.request_id = None
            self.failed = False

    def stop(self):
        with self.lock:
            self.connected = False
        if self.server:
            self.server.shutdown()
            self.server.server_close()


_active = None


def active():
    return _active


def attach(runtime):
    global _active
    _active = runtime
    runtime.connected = True
