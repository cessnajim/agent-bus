#!/usr/bin/env python3
"""Live Agent Bus visualization — HTTP + SSE over today's jsonl, state, catalog."""
from __future__ import annotations

import json
import mimetypes
import os
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent / "static"
EVENTS = ROOT / "events"
STATE = ROOT / "state"
CATALOG = ROOT / "catalog" / "topics.json"
HOST = os.environ.get("BUS_VIZ_HOST", "0.0.0.0")
PORT = int(os.environ.get("BUS_VIZ_PORT", "8788"))


def today() -> str:
    return datetime.now().astimezone().date().isoformat()


def read_catalog() -> dict:
    if not CATALOG.exists():
        return {"topics": {}}
    raw = json.loads(CATALOG.read_text())
    topics = {}
    for name, meta in (raw.get("topics") or {}).items():
        topics[name] = {
            "description": meta.get("description", ""),
            "publishers": list(meta.get("publishers") or []),
            "subscribers": list(meta.get("subscribers") or []),
            "required_refs": list(meta.get("required_refs") or []),
            "state_file": meta.get("state_file"),
            "done_means": meta.get("done_means", ""),
        }
    return {
        "version": raw.get("version"),
        "owner": raw.get("owner"),
        "rules": raw.get("rules"),
        "topics": topics,
    }


def normalize_event(ev: dict) -> dict:
    return {
        "topic": ev.get("topic"),
        "ts": ev.get("ts"),
        "actor": ev.get("actor"),
        "idempotency_key": ev.get("idempotency_key") or ev.get("key"),
        "refs": ev.get("refs") or {},
        "state_delta": ev.get("state_delta") or {},
        "note": ev.get("note") or "",
    }


def read_events(day: str | None = None) -> list:
    path = EVENTS / f"{day or today()}.jsonl"
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(normalize_event(json.loads(line)))
        except json.JSONDecodeError:
            continue
    return rows


def read_state() -> dict:
    out = {}
    if not STATE.exists():
        return out
    for p in STATE.glob("*.json"):
        try:
            out[p.stem] = json.loads(p.read_text())
        except json.JSONDecodeError:
            out[p.stem] = {"_error": "invalid json"}
    return out


def snapshot() -> dict:
    catalog = read_catalog()
    events = read_events()
    state = read_state()
    topic_volumes = {}
    last_by_topic = {}
    actor_counts = {}
    for ev in events:
        t = ev.get("topic") or "?"
        topic_volumes[t] = topic_volumes.get(t, 0) + 1
        if ev.get("ts"):
            last_by_topic[t] = ev["ts"]
        a = ev.get("actor") or "?"
        actor_counts[a] = actor_counts.get(a, 0) + 1
    for t in catalog.get("topics", {}):
        topic_volumes.setdefault(t, 0)
        last_by_topic.setdefault(t, "")
    return {
        "ok": True,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "day": today(),
        "catalog": catalog,
        "events": events,
        "state": state,
        "stats": {
            "event_count": len(events),
            "topic_volumes": topic_volumes,
            "last_by_topic": last_by_topic,
            "actor_counts": actor_counts,
        },
        "paths": {"root": str(ROOT)},
    }


def file_fingerprint() -> tuple:
    parts = [("day", today())]
    day_path = EVENTS / f"{today()}.jsonl"
    if day_path.exists():
        st = day_path.stat()
        parts.append(("events", st.st_mtime_ns, st.st_size))
    if STATE.exists():
        for p in sorted(STATE.glob("*.json")):
            st = p.stat()
            parts.append((p.name, st.st_mtime_ns, st.st_size))
    if CATALOG.exists():
        st = CATALOG.stat()
        parts.append(("catalog", st.st_mtime_ns, st.st_size))
    return tuple(parts)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        return

    def _no_store(self):
        self.send_header("Cache-Control", "no-store")

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._file(STATIC / "index.html", "text/html; charset=utf-8")
        if path == "/api/snapshot":
            body = json.dumps(snapshot()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self._no_store()
            self.end_headers()
            self.wfile.write(body)
            return
        if path.startswith("/api/stream"):
            return self._sse()
        rel = path.lstrip("/")
        candidate = (STATIC / rel).resolve()
        static_root = STATIC.resolve()
        if not candidate.is_relative_to(static_root) or not candidate.is_file():
            self.send_error(404)
            return
        ctype = mimetypes.guess_type(str(candidate))[0] or "application/octet-stream"
        return self._file(candidate, ctype)

    def _file(self, path: Path, ctype: str):
        if not path.exists():
            self.send_error(404)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self._no_store()
        self.end_headers()
        self.wfile.write(data)

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Retry", "2000")
        self.end_headers()
        last = None
        last_ping = 0.0
        try:
            # Initial retry hint for EventSource auto-reconnect
            self.wfile.write(b"retry: 2000\n\n")
            self.wfile.flush()
            payload = json.dumps(snapshot())
            self.wfile.write(("event: snapshot\ndata: %s\n\n" % payload).encode())
            self.wfile.flush()
            last = file_fingerprint()
            last_ping = time.time()
            while True:
                time.sleep(0.35)
                now = time.time()
                fp = file_fingerprint()
                if fp != last:
                    last = fp
                    payload = json.dumps(snapshot())
                    self.wfile.write(("event: snapshot\ndata: %s\n\n" % payload).encode())
                    self.wfile.flush()
                    last_ping = now
                elif now - last_ping >= 2.0:
                    # Real event (not a comment) so the browser JS sees it and
                    # knows the socket is still alive during quiet bus periods.
                    ping = json.dumps({"t": datetime.now().astimezone().isoformat(timespec="seconds")})
                    self.wfile.write(("event: ping\ndata: %s\n\n" % ping).encode())
                    self.wfile.flush()
                    last_ping = now
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            return


def main():
    STATIC.mkdir(parents=True, exist_ok=True)
    ThreadingHTTPServer.allow_reuse_address = True
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"agent-bus viz http://{HOST}:{PORT}/  root={ROOT}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
