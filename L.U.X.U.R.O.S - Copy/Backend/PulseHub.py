"""Pulse Hub: LUXUR's memory and activity store (SQLite, Data/luxur.db).

Memories are nodes; links between them (similar topic, same conversation,
used by / learned from a task) form the brain graph shown in the Pulse Hub tab.
"""
import json
import re
import sqlite3
import threading
from collections import Counter
from datetime import datetime, timedelta

import psutil

from Backend.Common import CHATLOG_PATH, DATA_DIR, LoadChatLog, SaveChatLog

DB_PATH = str(DATA_DIR / "luxur.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, ended_at TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER, role TEXT, content TEXT,
    created_at TEXT, meta_json TEXT);
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT CHECK(kind IN ('episodic','semantic','procedural','project')),
    content TEXT, source TEXT CHECK(source IN ('user','ai','verified')),
    importance INTEGER DEFAULT 5, project_id INTEGER, created_at TEXT, updated_at TEXT,
    active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS memory_links (memory_id INTEGER, related_id INTEGER, relation TEXT);
CREATE TABLE IF NOT EXISTS task_links (task_id INTEGER, memory_id INTEGER, relation TEXT);
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, description TEXT, status TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS agents (
    id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE, name TEXT, role TEXT, instructions TEXT,
    model_key TEXT, tools_json TEXT, status TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id INTEGER, task_id INTEGER, input TEXT, output TEXT,
    status TEXT, error TEXT, started TEXT, finished TEXT);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, parent_id INTEGER, agent_id INTEGER, title TEXT, intent TEXT,
    plan_json TEXT, status TEXT, result_json TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS strands (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, description TEXT, steps_json TEXT, schedule TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS strand_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, strand_id INTEGER, status TEXT, started TEXT, finished TEXT, log_json TEXT);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, source TEXT, level TEXT, type TEXT, payload_json TEXT);
CREATE TABLE IF NOT EXISTS permissions (
    action_key TEXT PRIMARY KEY, risk_level TEXT, allowed INTEGER DEFAULT 0, ask_each_time INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS tool_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, agent_id INTEGER, tool TEXT, args_json TEXT,
    risk_level TEXT, result TEXT, approved_by TEXT);
CREATE TABLE IF NOT EXISTS telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, cpu REAL, ram REAL, active_agents INTEGER, state TEXT);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(kind);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
"""

# Non-destructive upgrades for databases created by earlier versions.
MIGRATIONS = [
    # memory_links had no unique key, so "INSERT OR IGNORE" never de-duplicated.
    "DELETE FROM memory_links WHERE rowid NOT IN (SELECT MIN(rowid) FROM memory_links GROUP BY memory_id, related_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_memory_links ON memory_links(memory_id, related_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_task_links ON task_links(task_id, memory_id)",
]

DEFAULT_AGENTS = [
    ("executive_assistant", "Executive Assistant Agent", "Schedules, reminders, daily briefings, task organization", "chat", "[]", "idle"),
    ("research", "Research Agent", "Deep research, source gathering, structured analysis", "search", '["web_search"]', "idle"),
    ("coding", "Coding Agent", "Programming assistance, debugging, code generation", "chat", '["files"]', "idle"),
    ("business_strategy", "Business Strategy Agent", "Business models, market strategy, planning", "chat", "[]", "idle"),
    ("financial_analysis", "Financial Analysis Agent", "Financial statements, ratios, stock research, NEPSE", "search", '["web_search"]', "idle"),
    ("writing", "Writing Agent", "Writing, editing, content production", "chat", '["files"]', "idle"),
    ("productivity", "Productivity Agent", "Workflow suggestions, focus, task management", "chat", "[]", "idle"),
    ("desktop_operator", "Desktop Operator Agent", "Desktop control: apps, mouse, keyboard, windows", "chat", '["desktop_control"]', "idle"),
    ("memory", "Memory Agent", "Memory management, retrieval, summarization", "chat", '["memory"]', "idle"),
]

VOICE_STATE_MAP = {
    "Available ...": "IDLE", "Listening ...": "LISTENING", "Thinking ...": "THINKING",
    "Searching ...": "PROCESSING", "Answering ...": "SPEAKING", "Translating ...": "PROCESSING",
}

STOP_WORDS = {"this", "that", "with", "from", "have", "what", "when", "your", "about", "like", "just",
              "they", "will", "would", "there", "their", "which", "user", "uses", "often", "usually"}

TELEMETRY_KEEP = 2000


def _now():
    return datetime.now().isoformat(timespec="seconds")


class EventBus:
    def __init__(self, db_execute):
        self._execute = db_execute
        self._subscribers = {}
        self._lock = threading.Lock()

    def subscribe(self, event_type, callback):
        with self._lock:
            self._subscribers.setdefault(event_type, []).append(callback)

    def publish(self, event_type, payload=None, source="system", level="info"):
        ts = _now()
        self._execute("INSERT INTO events (ts, source, level, type, payload_json) VALUES (?,?,?,?,?)",
                      (ts, source, level, event_type, json.dumps(payload or {})))
        with self._lock:
            callbacks = list(self._subscribers.get(event_type, [])) + list(self._subscribers.get("*", []))
        for cb in callbacks:
            try:
                cb({"type": event_type, "payload": payload, "source": source, "level": level, "ts": ts})
            except Exception as e:
                print(f"[PulseHub] subscriber error: {e}")


class PulseHub:
    _instance = None
    _init_lock = threading.Lock()

    def __new__(cls):
        with cls._init_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(SCHEMA)
        for sql in MIGRATIONS:
            self._conn.execute(sql)
        self._conn.commit()

        self.voice_state = "IDLE"
        self.active_agents = 0
        self.event_bus = EventBus(self._execute)
        self._telemetry_stop = threading.Event()
        threading.Thread(target=self._telemetry_loop, daemon=True).start()
        self._seed_agents()
        self.event_bus.publish("hub.started", {"db": DB_PATH})

    # ---------------------------------------------------------------- db helpers

    def _execute(self, sql, params=()):
        try:
            with self._lock:
                cur = self._conn.execute(sql, params)
                self._conn.commit()
                return cur
        except Exception as e:
            print(f"[PulseHub] write failed: {e}")

            class _FailedResult:
                lastrowid = -1
                rowcount = 0
            return _FailedResult()

    def _query(self, sql, params=()):
        with self._lock:
            cur = self._conn.execute(sql, params)
            columns = [d[0] for d in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]

    def _seed_agents(self):
        if self._query("SELECT COUNT(*) AS c FROM agents")[0]["c"] == 0:
            for key, name, role, model_key, tools, status in DEFAULT_AGENTS:
                self._execute("INSERT INTO agents (key, name, role, instructions, model_key, tools_json, status, created_at) "
                              "VALUES (?,?,?,?,?,?,?,?)", (key, name, role, "", model_key, tools, status, _now()))

    def _telemetry_loop(self):
        ticks = 0
        while not self._telemetry_stop.wait(5):
            try:
                self._execute("INSERT INTO telemetry (ts, cpu, ram, active_agents, state) VALUES (?,?,?,?,?)",
                              (_now(), psutil.cpu_percent(interval=None), psutil.virtual_memory().percent,
                               self.active_agents, self.voice_state))
                ticks += 1
                if ticks % 120 == 0:  # every ~10 minutes keep only the newest rows
                    self._execute("DELETE FROM telemetry WHERE id <= (SELECT MAX(id) FROM telemetry) - ?", (TELEMETRY_KEEP,))
            except Exception as e:
                print(f"[PulseHub] telemetry error: {e}")

    # ---------------------------------------------------------------- sessions & messages

    def set_voice_state(self, status_text):
        state = VOICE_STATE_MAP.get(status_text, status_text.split(":")[0].upper())
        self.voice_state = state
        self.event_bus.publish("voice.state", {"state": state, "text": status_text}, source="voice_engine")

    def current_session(self):
        row = self._query("SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1")
        if row:
            return row[0]["id"]
        return self._execute("INSERT INTO sessions (started_at) VALUES (?)", (_now(),)).lastrowid

    def end_session(self):
        self._execute("UPDATE sessions SET ended_at=? WHERE ended_at IS NULL", (_now(),))

    def log_conversation(self, user_text, assistant_text, meta=None):
        session_id, now = self.current_session(), _now()
        for role, text in (("user", user_text), ("assistant", assistant_text)):
            self._execute("INSERT INTO messages (session_id, role, content, created_at, meta_json) VALUES (?,?,?,?,?)",
                          (session_id, role, text, now, json.dumps(meta or {})))
        # ChatLog.json mirrors the conversation for the chat screen.
        log = LoadChatLog()
        log += [{"role": "user", "content": user_text}, {"role": "assistant", "content": assistant_text}]
        try:
            SaveChatLog(log)
        except OSError as e:
            print(f"[PulseHub] could not write {CHATLOG_PATH}: {e}")

    def recent_messages(self, limit=10):
        rows = self._query("SELECT role, content FROM messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
                           (self.current_session(), limit))
        return list(reversed(rows))

    def recent_messages_any(self, limit=10):
        """Last messages across ALL sessions, so context survives restarts."""
        return list(reversed(self._query("SELECT role, content FROM messages ORDER BY id DESC LIMIT ?", (limit,))))

    # ---------------------------------------------------------------- memories

    @staticmethod
    def _significant_words(text):
        return {w for w in re.findall(r"[a-z0-9']+", text.lower()) if len(w) > 3 and w not in STOP_WORDS}

    def add_memory(self, content, kind="semantic", source="user", importance=5):
        now = _now()
        cur = self._execute("INSERT INTO memories (kind, content, source, importance, created_at, updated_at) "
                            "VALUES (?,?,?,?,?,?)", (kind, content, source, importance, now, now))
        self.event_bus.publish("memory.created", {"id": cur.lastrowid, "kind": kind}, source="memory")
        return cur.lastrowid

    def _link(self, a, b, relation):
        if a == b or a < 0 or b < 0:
            return
        for x, y in ((a, b), (b, a)):
            self._execute("INSERT OR IGNORE INTO memory_links (memory_id, related_id, relation) VALUES (?,?,?)", (x, y, relation))
        self.event_bus.publish("memory.linked", {"a": a, "b": b, "relation": relation}, source="memory")

    def add_memory_with_links(self, content, kind="semantic", source="ai", importance=5):
        """Store a memory and auto-link it to related ones (Obsidian-style graph)."""
        existing = self._query("SELECT id FROM memories WHERE active=1 AND LOWER(content)=LOWER(?)", (content.strip(),))
        if existing:
            return existing[0]["id"]
        memory_id = self.add_memory(content, kind=kind, source=source, importance=importance)
        words = self._significant_words(content)
        for rel in self.search_memories(content, limit=12):
            if rel["id"] != memory_id and len(words & self._significant_words(rel["content"])) >= 2:
                self._link(memory_id, rel["id"], "similar")
        # Memories learned within a few minutes of each other come from the same conversation.
        since = (datetime.now() - timedelta(minutes=10)).isoformat(timespec="seconds")
        recent = self._query("SELECT id FROM memories WHERE active=1 AND id != ? AND created_at >= ? "
                             "ORDER BY id DESC LIMIT 1", (memory_id, since))
        if recent:
            self._link(memory_id, recent[0]["id"], "same conversation")
        return memory_id

    def remember(self, content):
        """Something the user explicitly asked LUXUR to remember."""
        return self.add_memory_with_links(content.strip(), kind="semantic", source="user", importance=8)

    def linked_memories(self, memory_id):
        return self._query("SELECT m.id, m.kind, m.content, l.relation FROM memory_links l "
                           "JOIN memories m ON m.id = l.related_id WHERE l.memory_id = ? AND m.active=1", (memory_id,))

    def search_memories(self, query, limit=5):
        words = [w for w in re.findall(r"[a-z0-9']+", query.lower()) if len(w) > 2 and w not in STOP_WORDS]
        if not words:
            return []
        clauses = " OR ".join(["LOWER(content) LIKE ?"] * len(words))
        return self._query(f"SELECT id, kind, content, source, importance FROM memories WHERE active=1 AND ({clauses}) "
                           "ORDER BY importance DESC, id DESC LIMIT ?", [f"%{w}%" for w in words] + [limit])

    def list_memories(self, kind=None, limit=100):
        if kind:
            return self._query("SELECT id, kind, content, source, importance, created_at FROM memories "
                               "WHERE active=1 AND kind=? ORDER BY importance DESC, id DESC LIMIT ?", (kind, limit))
        return self._query("SELECT id, kind, content, source, importance, created_at FROM memories "
                           "WHERE active=1 ORDER BY importance DESC, id DESC LIMIT ?", (limit,))

    def update_memory(self, memory_id, content, importance=None):
        if importance is None:
            self._execute("UPDATE memories SET content=?, updated_at=? WHERE id=?", (content, _now(), memory_id))
        else:
            self._execute("UPDATE memories SET content=?, importance=?, updated_at=? WHERE id=?",
                          (content, importance, _now(), memory_id))

    def delete_memory(self, memory_id, hard=False):
        if hard:
            self._execute("DELETE FROM memories WHERE id=?", (memory_id,))
        else:
            self._execute("UPDATE memories SET active=0 WHERE id=?", (memory_id,))

    # ---------------------------------------------------------------- tasks

    def create_task(self, title, intent="", plan=None, agent_id=None, parent_id=None, status="queued"):
        now = _now()
        cur = self._execute("INSERT INTO tasks (parent_id, agent_id, title, intent, plan_json, status, created_at, updated_at) "
                            "VALUES (?,?,?,?,?,?,?,?)", (parent_id, agent_id, title, intent, json.dumps(plan or []), status, now, now))
        self.event_bus.publish("task.created", {"id": cur.lastrowid, "title": title, "intent": intent})
        return cur.lastrowid

    def update_task(self, task_id, status, result=None, plan=None, intent=None):
        sets, params = ["status=?", "result_json=?", "updated_at=?"], [status, json.dumps(result or {}), _now()]
        if intent is not None:
            sets.append("intent=?")
            params.append(intent)
        if plan is not None:
            sets.append("plan_json=?")
            params.append(json.dumps(plan))
        self._execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id=?", params + [task_id])
        self.event_bus.publish("task.updated", {"id": task_id, "status": status})

    def link_task_memory(self, task_id, memory_id, relation):
        if task_id and memory_id and memory_id > 0:
            self._execute("INSERT OR IGNORE INTO task_links (task_id, memory_id, relation) VALUES (?,?,?)",
                          (task_id, memory_id, relation))

    def add_reminder(self, text, due):
        """Store a reminder; `due` is a datetime or None (saved as a note)."""
        result = {"due": due.isoformat(timespec="minutes")} if due else {}
        task_id = self.create_task(text[:120], intent="reminder", status="reminder" if due else "note")
        self.update_task(task_id, "reminder" if due else "note", result)
        return task_id

    def due_reminders(self):
        now = datetime.now().isoformat(timespec="minutes")
        rows = self._query("SELECT id, title, result_json FROM tasks WHERE status='reminder'")
        return [r for r in rows if json.loads(r["result_json"] or "{}").get("due", "9999") <= now]

    # ---------------------------------------------------------------- audit & patterns

    def audit(self, tool, args, risk, result, approved_by="auto"):
        self._execute("INSERT INTO tool_audit (ts, agent_id, tool, args_json, risk_level, result, approved_by) "
                      "VALUES (?,?,?,?,?,?,?)", (_now(), None, tool, json.dumps(args), risk, str(result)[:500], approved_by))

    def record_usage(self, kind, name):
        """kind: 'app' or 'site'. Feeds pattern detection."""
        name = name.lower().strip()
        if name:
            self.event_bus.publish(f"usage.{kind}", {"name": name}, source="desktop")

    def detect_patterns(self, min_count=3, days=14):
        """Turn repeated behaviour into procedural memories (updated, not duplicated)."""
        since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        found = []
        for kind, label in (("app", "Frequently opens {} (opened {} times in the last two weeks)"),
                            ("site", "Often visits {} ({} visits in the last two weeks)")):
            rows = self._query("SELECT payload_json FROM events WHERE type=? AND ts >= ?", (f"usage.{kind}", since))
            counts = Counter(json.loads(r["payload_json"]).get("name", "") for r in rows)
            for name, count in counts.most_common(5):
                if name and count >= min_count:
                    found.append((f"{kind}:{name}", label.format(name, count)))
        hours = Counter(int(r["created_at"][11:13]) for r in self._query(
            "SELECT created_at FROM messages WHERE role='user' AND created_at >= ?", (since,)) if r["created_at"])
        if sum(hours.values()) >= 10:
            peak = max(hours, key=hours.get)
            found.append(("schedule:peak", f"Usually works with LUXUR around {peak:02d}:00-{(peak + 1) % 24:02d}:00"))

        for key, text in found:
            marker = f"[pattern:{key}]"
            existing = self._query("SELECT id FROM memories WHERE active=1 AND content LIKE ?", (f"%{marker}",))
            if existing:
                self.update_memory(existing[0]["id"], f"{text} {marker}")
            else:
                self.add_memory_with_links(f"{text} {marker}", kind="procedural", source="verified", importance=6)
                self.event_bus.publish("pattern.detected", {"key": key}, source="memory")
        return [text for _, text in found]

    # ---------------------------------------------------------------- brain graph

    def graph_version(self):
        """Cheap fingerprint; changes whenever nodes or links change."""
        row = self._query("SELECT (SELECT IFNULL(MAX(id),0) FROM memories) AS m, (SELECT COUNT(*) FROM memories WHERE active=1) AS a, "
                          "(SELECT COUNT(*) FROM memory_links) AS l, (SELECT IFNULL(MAX(id),0) FROM tasks) AS t, "
                          "(SELECT COUNT(*) FROM task_links) AS tl, (SELECT IFNULL(MAX(updated_at),'') FROM tasks) AS tu")[0]
        return tuple(row.values())

    def graph(self, memory_limit=120, task_limit=40):
        memories = self._query("SELECT id, kind, content, source, importance, created_at FROM memories WHERE active=1 "
                               "ORDER BY importance DESC, id DESC LIMIT ?", (memory_limit,))
        tasks = self._query("SELECT id, title, intent, status, created_at FROM tasks "
                            "WHERE intent NOT IN ('pending', '') ORDER BY id DESC LIMIT ?", (task_limit,))
        mem_ids = {m["id"] for m in memories}
        task_ids = {t["id"] for t in tasks}
        nodes = [{"id": f"m{m['id']}", "type": "memory", "kind": m["kind"], "label": re.sub(r"\s*\[pattern:[^\]]+\]", "", m["content"]),
                  "importance": m["importance"], "created_at": m["created_at"]} for m in memories]
        nodes += [{"id": f"t{t['id']}", "type": "task", "kind": t["status"], "label": t["title"],
                   "importance": 4, "created_at": t["created_at"]} for t in tasks]
        edges = [{"a": f"m{r['memory_id']}", "b": f"m{r['related_id']}", "relation": r["relation"]}
                 for r in self._query("SELECT memory_id, related_id, relation FROM memory_links WHERE memory_id < related_id")
                 if r["memory_id"] in mem_ids and r["related_id"] in mem_ids]
        edges += [{"a": f"t{r['task_id']}", "b": f"m{r['memory_id']}", "relation": r["relation"]}
                  for r in self._query("SELECT task_id, memory_id, relation FROM task_links")
                  if r["task_id"] in task_ids and r["memory_id"] in mem_ids]
        # Tasks with the same intent are connected too.
        by_intent = {}
        for t in tasks:
            by_intent.setdefault(t["intent"].split(" ")[0], []).append(t["id"])
        for ids in by_intent.values():
            edges += [{"a": f"t{a}", "b": f"t{b}", "relation": "same intent"} for a, b in zip(ids, ids[1:])]
        return {"nodes": nodes, "edges": edges}

    def node_details(self, node_id):
        kind, raw = node_id[0], int(node_id[1:])
        if kind == "m":
            rows = self._query("SELECT * FROM memories WHERE id=?", (raw,))
            if not rows:
                return None
            m = rows[0]
            tasks = self._query("SELECT t.id, t.title, t.status, l.relation FROM task_links l JOIN tasks t ON t.id=l.task_id "
                                "WHERE l.memory_id=? ORDER BY t.id DESC", (raw,))
            return {"title": re.sub(r"\s*\[pattern:[^\]]+\]", "", m["content"]), "type": "memory", "kind": m["kind"],
                    "source": m["source"], "importance": m["importance"], "created_at": m["created_at"],
                    "related": [{"id": f"m{r['id']}", "label": r["content"], "relation": r["relation"]} for r in self.linked_memories(raw)],
                    "tasks": [{"id": f"t{t['id']}", "label": t["title"], "relation": t["relation"], "status": t["status"]} for t in tasks]}
        rows = self._query("SELECT * FROM tasks WHERE id=?", (raw,))
        if not rows:
            return None
        t = rows[0]
        mems = self._query("SELECT m.id, m.content, l.relation FROM task_links l JOIN memories m ON m.id=l.memory_id "
                           "WHERE l.task_id=? AND m.active=1", (raw,))
        result = json.loads(t["result_json"] or "{}")
        return {"title": t["title"], "type": "task", "kind": t["status"], "source": t["intent"],
                "importance": None, "created_at": t["created_at"],
                "summary": result.get("summary") or result.get("answer") or result.get("error") or "",
                "steps": json.loads(t["plan_json"] or "[]"),
                "related": [{"id": f"m{m['id']}", "label": m["content"], "relation": m["relation"]} for m in mems],
                "tasks": []}

    # ---------------------------------------------------------------- misc

    def list_agents(self):
        return self._query("SELECT * FROM agents ORDER BY id")

    def get_agent(self, key):
        rows = self._query("SELECT * FROM agents WHERE key=?", (key,))
        return rows[0] if rows else None

    def recent_events(self, limit=30):
        return self._query("SELECT ts, source, level, type, payload_json FROM events ORDER BY id DESC LIMIT ?", (limit,))

    def latest_telemetry(self):
        return self._query("SELECT ts, cpu, ram, active_agents, state FROM telemetry ORDER BY id DESC LIMIT 1")

    def snapshot(self):
        tel = self.latest_telemetry()
        return {"voice_state": self.voice_state, "active_agents": self.active_agents,
                "cpu": tel[0]["cpu"] if tel else None, "ram": tel[0]["ram"] if tel else None,
                "recent_events": self.recent_events(10),
                "recent_tasks": self._query("SELECT id, title, intent, status FROM tasks ORDER BY id DESC LIMIT 10")}

    def shutdown(self):
        self._telemetry_stop.set()
        self.end_session()
        self.event_bus.publish("hub.stopped", {}, level="warning")
        self._conn.close()


if __name__ == "__main__":
    hub = PulseHub()
    print("DB:", DB_PATH)
    print("Agents:", [a["key"] for a in hub.list_agents()])
    print("Graph:", {k: len(v) for k, v in hub.graph().items()})
    print("Patterns:", hub.detect_patterns())
