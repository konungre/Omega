"""Transactional command ledger, projections, and replayable activity journal."""
import json
import fcntl
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


class APIError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def require(condition, message, status=400, code="invalid_command"):
    if not condition:
        raise APIError(status, code, message)


def text(value, name, limit=12000):
    require(isinstance(value, str) and 0 < len(value.strip()) <= limit,
            f"{name} must be nonempty text of at most {limit} characters")
    return value.strip()


class Store:
    def __init__(self, path):
        self.owner_file = None
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.owner_file = open(path, "a+b")
            try:
                fcntl.flock(self.owner_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                self.owner_file.close()
                raise RuntimeError("This Omega API database already has an active owner") from None
        self.lock = threading.RLock()
        self.db = None
        try:
            self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError(f"Unsupported Omega API database version {version}")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    collection TEXT NOT NULL, id TEXT NOT NULL, data TEXT NOT NULL,
                    PRIMARY KEY(collection, id));
                CREATE TABLE IF NOT EXISTS requests (
                    id TEXT PRIMARY KEY, command TEXT NOT NULL, payload TEXT NOT NULL,
                    status TEXT NOT NULL, result TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL,
                    request_id TEXT, run_id TEXT, at TEXT NOT NULL, data TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS requests_status ON requests(status, created_at);
                PRAGMA user_version=1;
            """)
            with self.transaction():
                if not self.get("meta", "instance"):
                    self.put("meta", {"id": "instance", "value": str(uuid.uuid4())})
                if not self.get("config", "operator"):
                    self.put("config", {"id": "operator", "autonomy_enabled": True,
                                        "goal_context_enabled": True})
                # A process restart cannot prove an in-flight effect did or did not happen.
                for row in self.db.execute("SELECT id FROM requests WHERE status='running'").fetchall():
                    self.finish(row["id"], "interrupted", {"message": "Agent restarted; inspect evidence before retrying."})
                for run in self.all("runs"):
                    if run["status"] == "running":
                        run.update(status="interrupted", finished_at=now())
                        self.put("runs", run)
                        self.event("run.interrupted", run, run_id=run["id"])
        except BaseException:
            if self.db is not None:
                self.db.close()
            if self.owner_file:
                self.owner_file.close()
            raise

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    def get(self, collection, identifier):
        with self.lock:
            row = self.db.execute("SELECT data FROM documents WHERE collection=? AND id=?",
                                  (collection, identifier)).fetchone()
            return json.loads(row[0]) if row else None

    def put(self, collection, document):
        self.db.execute("INSERT INTO documents VALUES (?,?,?) ON CONFLICT(collection,id) DO UPDATE SET data=excluded.data",
                        (collection, document["id"], encode(document)))

    def all(self, collection, limit=None):
        with self.lock:
            sql = "SELECT data FROM documents WHERE collection=? ORDER BY rowid DESC"
            params = [collection]
            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)
            return [json.loads(row[0]) for row in self.db.execute(sql, params)]

    def event(self, kind, data, request_id=None, run_id=None):
        cursor = self.db.execute("INSERT INTO events(type,request_id,run_id,at,data) VALUES (?,?,?,?,?)",
                                 (kind, request_id, run_id, now(), encode(data)))
        return cursor.lastrowid

    def highwater(self):
        return self.db.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]

    def events(self, after=0, limit=200):
        with self.lock:
            require(type(after) is int and 0 <= after <= self.highwater(),
                    "Event cursor is invalid; fetch a fresh snapshot", 409, "cursor_invalid")
            return [dict(row) | {"data": json.loads(row["data"])} for row in self.db.execute(
                "SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT ?", (after, limit))]

    def request(self, identifier):
        with self.lock:
            row = self.db.execute("SELECT * FROM requests WHERE id=?", (identifier,)).fetchone()
            if not row:
                raise APIError(404, "not_found", "Request not found")
            return dict(row) | {"payload": json.loads(row["payload"]),
                                "result": json.loads(row["result"]) if row["result"] else None}

    def finish(self, identifier, status="completed", result=None):
        self.db.execute("UPDATE requests SET status=?,result=?,updated_at=? WHERE id=?",
                        (status, encode(result or {}), now(), identifier))
        self.event(f"request.{status}", self.request(identifier), request_id=identifier)

    def submit(self, body, agent_connected=False):
        require(isinstance(body, dict) and set(body) == {"request_id", "command", "payload"},
                "Expected request_id, command, and payload")
        identifier, command, payload = body["request_id"], body["command"], body["payload"]
        require(isinstance(identifier, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", identifier),
                "request_id must contain 1–128 letters, digits, underscores, dots, colons, or hyphens")
        require(isinstance(command, str) and isinstance(payload, dict), "Invalid command envelope")
        with self.transaction():
            existing = self.db.execute("SELECT id FROM requests WHERE id=?", (identifier,)).fetchone()
            if existing:
                record = self.request(identifier)
                require(record["command"] == command and record["payload"] == payload,
                        "request_id already identifies a different command", 409, "request_conflict")
                return record, True
            stamp = now()
            self.db.execute("INSERT INTO requests VALUES (?,?,?,?,?,?,?)",
                            (identifier, command, encode(payload), "accepted", None, stamp, stamp))
            self.event("request.accepted", {"id": identifier, "command": command}, identifier)
            result = self._apply(command, payload, identifier, agent_connected)
            if command != "chat.send":
                self.finish(identifier, result=result)
            return self.request(identifier), False

    def _apply(self, command, payload, identifier, agent_connected):
        if command == "chat.send":
            require(set(payload) == {"text"}, "chat.send expects text")
            content = text(payload["text"], "text")
            require(agent_connected, "Start Omega with commchannel=dashboard before sending a message", 503, "agent_offline")
            pending = self.db.execute("SELECT COUNT(*) FROM requests WHERE status='accepted' AND command='chat.send'").fetchone()[0]
            require(pending <= 256, "The message queue is full; try again later", 429, "queue_full")
            message = {"id": identifier, "role": "user", "text": content, "at": now(), "request_id": identifier}
            self.put("messages", message)
            self.event("message.created", message, identifier)
            return {"message_id": identifier}
        if command == "goal.create":
            require(set(payload) <= {"title", "description"} and "title" in payload, "goal.create expects title and optional description")
            title = text(payload["title"], "title", 300)
            description = payload.get("description", "")
            require(isinstance(description, str) and len(description) <= 12000, "description must be text of at most 12000 characters")
            goal = {"id": identifier, "title": title, "description": description,
                    "status": "active", "created_at": now(), "updated_at": now()}
            self.put("goals", goal)
            self.event("goal.created", goal, identifier)
            return goal
        if command == "goal.set_status":
            require(set(payload) == {"goal_id", "status"}, "goal.set_status expects goal_id and status")
            require(isinstance(payload["goal_id"], str), "goal_id must be text")
            goal = self.get("goals", payload["goal_id"])
            require(goal is not None, "Goal not found", 404, "not_found")
            status = payload["status"]
            require(status in ("active", "paused", "completed", "cancelled"), "Unknown goal status")
            require(goal["status"] not in ("completed", "cancelled") or goal["status"] == status,
                    "A terminal goal cannot be reopened; create a new goal", 409, "invalid_transition")
            goal.update(status=status, updated_at=now())
            self.put("goals", goal)
            self.event("goal.updated", goal, identifier)
            return goal
        if command == "config.update":
            allowed = {"autonomy_enabled", "goal_context_enabled"}
            require(bool(payload) and set(payload) <= allowed and all(type(v) is bool for v in payload.values()),
                    "Configuration accepts boolean autonomy_enabled and goal_context_enabled only")
            config = self.get("config", "operator") | payload
            self.put("config", config)
            self.event("config.updated", config, identifier)
            return config
        if command == "request.cancel":
            require(set(payload) == {"request_id"} and isinstance(payload["request_id"], str), "request.cancel expects request_id")
            target = self.request(payload["request_id"])
            require(target["command"] == "chat.send" and target["status"] == "accepted",
                    "Only queued messages can be cancelled; running tool calls cannot be interrupted", 409, "invalid_transition")
            self.finish(target["id"], "cancelled")
            return {"cancelled": target["id"]}
        raise APIError(400, "unknown_command", f"Unsupported command: {command}")

    def claim_message(self):
        with self.transaction():
            row = self.db.execute("SELECT id FROM requests WHERE command='chat.send' AND status='accepted' ORDER BY rowid LIMIT 1").fetchone()
            if not row:
                return None
            identifier = row[0]
            self.db.execute("UPDATE requests SET status='running',updated_at=? WHERE id=?", (now(), identifier))
            record = self.request(identifier)
            self.event("request.started", record, identifier)
            return record

    def snapshot(self, agent):
        # Projections and cursor are read together: no snapshot/subscribe gap.
        with self.lock:
            return {"api_version": "1", "instance_id": self.get("meta", "instance")["value"],
                    "cursor": self.highwater(), "agent": agent,
                    "config": self.get("config", "operator"),
                    "goals": self.all("goals"), "messages": self.all("messages", 200)[::-1],
                    "runs": self.all("runs", 100), "evidence": self.all("evidence", 100),
                    "improvements": self.all("improvements", 100),
                    "requests": [self.request(row[0]) for row in self.db.execute("SELECT id FROM requests ORDER BY rowid DESC LIMIT 100")],
                    "activity": [dict(row) | {"data": json.loads(row["data"])} for row in self.db.execute("SELECT * FROM events ORDER BY seq DESC LIMIT 100")]}

    def close(self):
        with self.lock:
            self.db.close()
            if self.owner_file and not self.owner_file.closed:
                self.owner_file.close()
