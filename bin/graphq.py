#!/usr/bin/env python3
"""Query the agent graph by name, no Cypher needed.

  graphq.py list                         # show every named query
  graphq.py stalls --param hours=24      # run one, override a default param
  graphq.py agents_silent --json         # machine-readable output
  graphq.py --cypher 'MATCH (n:Job) RETURN count(n) AS jobs'   # ad hoc, read-only

Queries live in $AGENT_BUS_ROOT/graph/queries.cypher. Read-only: runs in a READ transaction.
Credentials: ~/.config/agent-graph/credentials (override AGENT_GRAPH_CREDENTIALS). Never print them.
"""
import argparse, json, os, re, sys
from pathlib import Path

VENV_PY = Path("~/.local/share/agent-graph/venv/bin/python").expanduser()
try:
    from neo4j import GraphDatabase, READ_ACCESS
except ImportError:
    if VENV_PY.exists() and Path(sys.executable) != VENV_PY:
        os.execv(str(VENV_PY), [str(VENV_PY), *sys.argv])
    raise

ROOT = Path(os.environ.get("AGENT_BUS_ROOT", Path(__file__).resolve().parents[1]))
QFILE = ROOT / "graph" / "queries.cypher"
CRED = Path(os.environ.get("AGENT_GRAPH_CREDENTIALS", "~/.config/agent-graph/credentials")).expanduser()


def parse_queries():
    qs, cur = {}, None
    for line in QFILE.read_text().splitlines():
        m = re.match(r"//\s*(name|desc|params):\s*(.*)$", line.strip())
        if m:
            k, v = m.groups()
            if k == "name":
                cur = {"name": v.strip(), "desc": "", "params": {}, "body": []}
                qs[cur["name"]] = cur
            elif cur is not None and k == "desc":
                cur["desc"] = v.strip()
            elif cur is not None and k == "params":
                for kv in v.split(","):
                    if "=" in kv:
                        a, b = kv.split("=", 1)
                        cur["params"][a.strip()] = conv(b.strip())
            continue
        if cur is not None and not line.strip().startswith("//"):
            cur["body"].append(line)
    for q in qs.values():
        q["cypher"] = "\n".join(q["body"]).strip().rstrip(";")
    return qs


def conv(v):
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    if re.fullmatch(r"-?\d+\.\d*", v):
        return float(v)
    return v


def creds():
    out = {}
    for line in CRED.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def fmt(v):
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    return "" if v is None else str(v)


def table(rows):
    if not rows:
        print("(no rows)")
        return
    cols = list(rows[0].keys())
    data = [[fmt(r.get(c)) for c in cols] for r in rows]
    w = [min(max(len(c), *(len(d[i]) for d in data)), 90) for i, c in enumerate(cols)]
    print("  ".join(c.ljust(w[i]) for i, c in enumerate(cols)))
    print("  ".join("-" * w[i] for i in range(len(cols))))
    for d in data:
        print("  ".join(d[i][:w[i]].ljust(w[i]) for i in range(len(cols))))


def main():
    ap = argparse.ArgumentParser(description="Named read-only queries over the agent graph")
    ap.add_argument("name", nargs="?", default="list")
    ap.add_argument("--param", action="append", default=[], help="key=value (repeatable)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--cypher", help="ad hoc read-only Cypher instead of a named query")
    a = ap.parse_args()
    qs = parse_queries()
    if a.cypher:
        q = {"cypher": a.cypher, "params": {}}
    elif a.name == "list":
        for q in qs.values():
            p = ", ".join(f"{k}={v}" for k, v in q["params"].items())
            print(f"{q['name']:<28} {q['desc']}" + (f"  [{p}]" if p else ""))
        return
    elif a.name not in qs:
        sys.exit(f"unknown query {a.name!r}; run 'graphq.py list'")
    else:
        q = qs[a.name]
    params = dict(q["params"])
    for kv in a.param:
        k, v = kv.split("=", 1)
        params[k] = conv(v)
    c = creds()
    drv = GraphDatabase.driver(c.get("NEO4J_URI", "bolt://127.0.0.1:7687"), auth=(c.get("NEO4J_USER", "neo4j"), c["NEO4J_PASSWORD"]))
    with drv.session(default_access_mode=READ_ACCESS) as s:
        rows = s.execute_read(lambda tx: [r.data() for r in tx.run(q["cypher"], **params)])
    drv.close()
    if a.json:
        print(json.dumps(rows, indent=2, default=str))
    else:
        table(rows)


if __name__ == "__main__":
    main()
