#!/usr/bin/env python3
"""Load the agent bus into the agent-graph Neo4j (idempotent, safe to re-run).

Reads (never writes) $AGENT_BUS_ROOT/events/*.jsonl and catalog/topics.json, plus an
optional Jim asks queue snapshot (jsonl). Every node is MERGEd on a stable uid, so a
re-run changes nothing unless the bus changed. Event uid = sha1(topic|idempotency_key|ts).

Usage: load_bus.py [--asks PATH] [--quiet] [--rebuild]
Env:   AGENT_BUS_ROOT, AGENT_GRAPH_CREDENTIALS (default ~/.config/agent-graph/credentials),
       AGENT_GRAPH_ASKS (default ~/.local/share/agent-graph/inputs/jim-asks-queue.jsonl)
"""
import argparse, datetime as dt, fcntl, hashlib, json, os, re, sys, time
from pathlib import Path

VENV_PY = Path("~/.local/share/agent-graph/venv/bin/python").expanduser()
try:
    from neo4j import GraphDatabase
except ImportError:
    if VENV_PY.exists() and Path(sys.executable) != VENV_PY:
        os.execv(str(VENV_PY), [str(VENV_PY), *sys.argv])
    raise

ROOT = Path(os.environ.get("AGENT_BUS_ROOT", Path(__file__).resolve().parents[1]))
CRED = Path(os.environ.get("AGENT_GRAPH_CREDENTIALS", "~/.config/agent-graph/credentials")).expanduser()
ASKS = Path(os.environ.get("AGENT_GRAPH_ASKS", "~/.local/share/agent-graph/inputs/jim-asks-queue.jsonl")).expanduser()
STATE_DIR = Path("~/.local/share/agent-graph").expanduser()

JOB_TOPICS = {"fte.submit", "fte.reject", "fte.park", "fte.hold_cleared"}
APP_STATUS = {"fte.submit": "submitted", "fte.reject": "rejected", "fte.park": "parked", "fte.hold_cleared": "hold_cleared"}
ROSTER = ["Admin", "FTE Apply", "Writing Critic", "Unemployment", "Travel", "Expense Manager", "Investment",
          "Social Media", "Marketing", "Sales", "Ops", "SEO Auditor", "Stills & Clips", "Subscription Cleaner", "Jim"]
KNOWN_IDS = {"FTE Apply": "a8926586-fbf3-4d6c-8f0a-790e65ead1bd", "Admin": "fa50d9b1-127a-404a-81d2-7bf61b49e1fe",
             "Writing Critic": "80f6148a-faf0-4d35-96b5-d9047389209a", "Unemployment": "b31aa760-294c-437c-82fe-9be43ebeff0c",
             "Investment": "dd0ab463-c048-4751-87c7-68b6f041d6d9"}
KNOWN_IDS = {"FTE Apply": "a8926586-fbf3-4d6c-8f0a-790e65ead1bd", "Admin": "fa50d9b1-127a-404a-81d2-7bf61b49e1fe",
             "Writing Critic": "80f6148a-faf0-4d35-96b5-d9047389209a", "Unemployment": "b31aa760-294c-437c-82fe-9be43ebeff0c",
             "Investment": "dd0ab463-c048-4751-87c7-68b6f041d6d9"}


def creds():
    out = {}
    for line in CRED.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")


def norm_sid(s):
    """Canonical posting id: a UUID if present, else the longest 4+ digit run, else a slug.
    Lets 'gh5465434008', 'li-5465434008' and 5465434008 land on the same Job."""
    s = str(s).strip().lower()
    m = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", s)
    if m:
        return m.group(0)
    runs = re.findall(r"\d{4,}", s)
    if runs:
        return max(runs, key=len)
    return slug(s)


def parse_ts(s):
    try:
        return dt.datetime.fromisoformat(str(s))
    except Exception:
        return None


def h(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:20]


def is_master(refs):
    c = str(refs.get("company", "")).lower()
    return c in ("master", "masters") or "master" in str(refs.get("source_id", "")).lower() or refs.get("lane") == "master"


def read_events(aliases):
    rows = []
    for f in sorted((ROOT / "events").glob("*.jsonl")):
        for n, line in enumerate(f.read_text().splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                print(f"skip bad json {f.name}:{n}", file=sys.stderr)
                continue
            refs = e.get("refs") or {}
            ts = parse_ts(e.get("ts"))
            actor = aliases.get(e.get("actor"), e.get("actor") or "unknown")
            topic = e.get("topic") or "unknown"
            rows.append({
                "uid": h(topic, e.get("idempotency_key"), e.get("ts")),
                "topic": topic, "domain": topic.split(".")[0], "key": e.get("idempotency_key"),
                "ts": e.get("ts"), "epoch": ts.timestamp() if ts else None,
                "day": f.stem, "occurred_at": str(refs.get("occurred_at") or f.stem)[:10],
                "actor": actor, "raw_actor": e.get("actor"), "note": e.get("note"),
                "test": bool(refs.get("test")) or topic == "bus.test",
                "backfill": bool(refs.get("backfill")),
                "refs": refs, "refs_json": json.dumps(refs, sort_keys=True, default=str),
                "delta_json": json.dumps(e.get("state_delta") or {}, sort_keys=True, default=str),
                "file": f.name, "line": n,
            })
    rows.sort(key=lambda r: (r["epoch"] or 0, r["file"], r["line"]))
    return rows


ROLE_SID = {}  # (company_slug, role_slug) -> sid, learned from events that carry a source_id


def learn_role_sids(rows):
    for r in rows:
        refs = r["refs"]
        if refs.get("company") and refs.get("role") and refs.get("source_id") not in (None, ""):
            ROLE_SID.setdefault((slug(refs["company"]), slug(refs["role"])), norm_sid(refs["source_id"]))


def subject_for(r):
    """(label, uid, props) of the main thing an event is about, or None."""
    t, refs = r["topic"], r["refs"]
    if t in JOB_TOPICS or (t == "critic.pass" and refs.get("company") and not is_master(refs)):
        if refs.get("source_id") not in (None, ""):
            sid = norm_sid(refs["source_id"])
        else:
            sid = ROLE_SID.get((slug(refs.get("company", "")), slug(refs.get("role", ""))), slug(refs.get("role", "")))
        return ("Job", f"{slug(refs.get('company',''))}|{sid}", {})
    if t == "critic.pass":
        return ("Asset", "cv|" + slug(refs.get("role", "master"))[:80], {"name": refs.get("role"), "kind": "cv_master"})
    if t == "critic.pass.outbound":
        return ("Asset", "out|" + slug(refs.get("asset", ""))[:80], {"name": str(refs.get("asset"))[:200], "kind": refs.get("lane")})
    if t == "social.post":
        k = refs.get("critic_key") or re.sub(r"-\d{4}-\d{2}-\d{2}$", "", r["key"] or "")
        return ("Post", "social|" + slug(k), {"channel": refs.get("channel"), "name": str(refs.get("asset"))[:200]})
    if t == "youtube.published":
        return ("Post", "yt|" + str(refs.get("video_id")), {"channel": refs.get("channel"), "name": refs.get("title"), "url": refs.get("url")})
    if t in ("assets.ready", "adobe.batch_submitted"):
        return ("AssetBatch", str(refs.get("batch_id")), {"theme": refs.get("theme"), "for": refs.get("for")})
    if t in ("shop.exclusive_sent", "shop.exclusive_draft_ready"):
        return ("Prospect", slug(refs.get("prospect", "")), {"name": refs.get("prospect")})
    if t in ("catalant.pitch_submitted", "marketplace.pitch_submitted"):
        net = refs.get("network") or "Catalant"
        return ("Opportunity", f"{slug(net)}|{refs.get('opportunity_id')}", {"network": net, "name": refs.get("title") or refs.get("role"), "rate": refs.get("rate"), "band": refs.get("band")})
    if t == "expense.logged":
        return ("Expense", r["key"], {"amount": refs.get("amount"), "vendor": refs.get("vendor"), "entity": refs.get("entity"), "status": refs.get("status"), "occurred_at": refs.get("occurred_at")})
    if t == "ui.cert_filed":
        return ("CertWeek", str(refs.get("week")), {"claim": refs.get("claim")})
    if t == "seo.audit":
        return ("Finding", r["key"], {"site": refs.get("site"), "finding": str(refs.get("finding"))[:300], "status": refs.get("status"), "owner": refs.get("owner")})
    if t.startswith("travel."):
        return ("Trip", slug(refs.get("trip", "")), {"name": refs.get("trip")})
    if t == "adobe.review_logged" or t == "adobe.reject_reasons_captured":
        return ("AdobeReview", str(refs.get("date")), {})
    return None


def clean_agents(names):
    """Catalog lists sometimes annotate names ('Admin (day-ledger)') or use rule refs ('refs.owner')."""
    out = []
    for n in names or []:
        if not isinstance(n, str) or n.startswith("refs."):
            continue
        out.append(re.sub(r"\s*\(.*\)$", "", n).strip())
    return out


SCHEMA = [
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Event) REQUIRE n.uid IS UNIQUE",
    *[f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{l}) REQUIRE n.uid IS UNIQUE" for l in
      ["Job", "Application", "Asset", "Post", "AssetBatch", "Prospect", "Opportunity", "Expense", "CertWeek",
       "Finding", "Trip", "AdobeReview", "CriticReview", "Ask"]],
    *[f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{l}) REQUIRE n.name IS UNIQUE" for l in
      ["Agent", "Topic", "Company", "CVVersion", "RejectReason", "ParkReason", "Vendor", "Entity"]],
    "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Day) REQUIRE n.date IS UNIQUE",
    "CREATE INDEX IF NOT EXISTS FOR (n:Event) ON (n.topic)",
    "CREATE INDEX IF NOT EXISTS FOR (n:Event) ON (n.epoch)",
]


def load(tx_run, rows, catalog, asks):
    run = tx_run
    # Catalog: topics, agents, wiring
    topics = catalog.get("topics", {})
    cov = (catalog.get("coverage") or {}).get("agents", {})
    for name in set(ROSTER) | set(cov) | set((catalog.get("agents") or {})):
        a = cov.get(name, {})
        run("MERGE (a:Agent {name:$n}) SET a.agent_id=$id, a.silent_days=$sd, a.quiet_ok=$q, a.weekdays_only=$w, a.snooze_until=$sn, a.system=false",
            n=name, id=(catalog.get("agents") or {}).get(name) or KNOWN_IDS.get(name), sd=a.get("silent_days"), q=a.get("quiet_ok"), w=a.get("weekdays_only"), sn=a.get("snooze_until"))
    for name in (catalog.get("coverage") or {}).get("system_actors", []):
        run("MERGE (a:Agent {name:$n}) SET a.system=true", n=name)
    run("MATCH (:Agent)-[r:SUBSCRIBES|CAN_PUBLISH]->(:Topic) DELETE r")
    run("MATCH (:Topic)-[r:MUST_ACT]->(:Agent) DELETE r")
    for tname, meta in topics.items():
        run("MERGE (t:Topic {name:$n}) SET t.description=$d, t.domain=$dom, t.required_refs=$rr, t.state_file=$sf, t.in_catalog=true",
            n=tname, d=meta.get("description"), dom=tname.split(".")[0], rr=meta.get("required_refs", []), sf=meta.get("state_file"))
        for rel, key in (("CAN_PUBLISH", "publishers"), ("SUBSCRIBES", "subscribers")):
            for ag in clean_agents(meta.get(key, [])):
                run(f"MATCH (t:Topic {{name:$t}}) MERGE (a:Agent {{name:$a}}) MERGE (a)-[:{rel}]->(t)", t=tname, a=ag)
        for ag in clean_agents(meta.get("must_act", [])):
            if True:
                run("MATCH (t:Topic {name:$t}) MERGE (a:Agent {name:$a}) MERGE (t)-[:MUST_ACT]->(a)", t=tname, a=ag)

    days = sorted({r["day"] for r in rows})
    for d in days:
        run("MERGE (:Day {date:$d})", d=d)
    for a, b in zip(days, days[1:]):
        run("MATCH (a:Day {date:$a}),(b:Day {date:$b}) MERGE (a)-[:NEXT]->(b)", a=a, b=b)

    subjects = {}
    for r in rows:
        refs = r["refs"]
        run("""MERGE (e:Event {uid:$uid})
               SET e.topic=$topic, e.domain=$domain, e.key=$key, e.ts=$ts, e.epoch=$epoch, e.day=$day,
                   e.occurred_at=$occurred_at, e.actor=$actor, e.raw_actor=$raw_actor, e.note=$note, e.test=$test,
                   e.backfill=$backfill, e.refs_json=$refs_json, e.delta_json=$delta_json, e.file=$file, e.line=$line,
                   e.lane=$lane, e.company=$company, e.role=$role, e.status=$status
               MERGE (a:Agent {name:$actor})
               MERGE (a)-[:PUBLISHED]->(e)
               MERGE (t:Topic {name:$topic}) ON CREATE SET t.in_catalog=false, t.domain=$domain
               MERGE (e)-[:IN_TOPIC]->(t)
               WITH e MATCH (d:Day {date:$day}) MERGE (e)-[:ON_DAY]->(d)""",
            **{k: v for k, v in r.items() if k != "refs"}, lane=refs.get("lane"),
            company=refs.get("company") or refs.get("prospect"), role=refs.get("role"), status=refs.get("status"))
        subj = subject_for(r)
        if not subj:
            continue
        label, suid, props = subj
        props = {k: (str(v) if isinstance(v, (dict, list)) else v) for k, v in props.items() if v is not None}
        run(f"MATCH (e:Event {{uid:$e}}) MERGE (s:{label} {{uid:$s}}) SET s += $p MERGE (e)-[:ABOUT]->(s)", e=r["uid"], s=suid, p=props)
        subjects.setdefault((label, suid), []).append(r["uid"])
        t = r["topic"]
        if label == "Job":
            run("""MATCH (j:Job {uid:$s}) SET j.role=coalesce(j.role,$role), j.source_id=coalesce(j.source_id,$sid), j.ats=coalesce($ats,j.ats)
                   MERGE (c:Company {name:$company}) MERGE (j)-[:AT]->(c)""",
                s=suid, role=refs.get("role"), sid=str(refs.get("source_id", "")), ats=refs.get("ats"), company=str(refs.get("company")).strip())
            if t == "critic.pass":
                run("""MATCH (e:Event {uid:$e}), (j:Job {uid:$s})
                       MERGE (cr:CriticReview {uid:$e}) SET cr.result='PASS', cr.lane=coalesce($lane,'fte'), cr.ts=$ts, cr.epoch=$epoch, cr.packet_path=$pp
                       MERGE (e)-[:RECORDS]->(cr) MERGE (cr)-[:PASSED]->(j)
                       MERGE (w:Agent {name:$actor}) MERGE (w)-[:REVIEWED]->(cr)""",
                    e=r["uid"], s=suid, lane=refs.get("lane"), ts=r["ts"], epoch=r["epoch"], pp=refs.get("packet_path"), actor=r["actor"])
            else:
                run("""MATCH (e:Event {uid:$e}), (j:Job {uid:$s})
                       MERGE (ap:Application {uid:$s}) MERGE (ap)-[:FOR]->(j) MERGE (e)-[:ABOUT]->(ap)""", e=r["uid"], s=suid)
                if t == "fte.submit":
                    run("""MATCH (ap:Application {uid:$s})
                           SET ap.submitted_on=coalesce($so, ap.submitted_on, $day), ap.submitted_epoch=coalesce(ap.submitted_epoch,$epoch),
                               ap.ats=coalesce($ats, ap.ats), ap.cv_variant=coalesce($cv, ap.cv_variant)
                           WITH ap WHERE $cv IS NOT NULL MERGE (cv:CVVersion {name:$cv}) MERGE (ap)-[:USED_CV]->(cv)""",
                        s=suid, so=refs.get("submitted_on"), day=r["day"], epoch=r["epoch"], ats=refs.get("ats"), cv=refs.get("cv_variant"))
                elif t == "fte.reject":
                    run("""MATCH (ap:Application {uid:$s})-[:FOR]->(:Job)-[:AT]->(c:Company)
                           SET ap.rejected_on=coalesce($ro, $day), ap.reject_type=coalesce($rt, ap.reject_type, 'unspecified'), ap.rejected_epoch=$epoch
                           MERGE (ap)-[x:REJECTED_BY]->(c) SET x.reject_type=$rt, x.ts=$ts
                           MERGE (rr:RejectReason {name:coalesce($rt,'unspecified')}) MERGE (ap)-[:REJECTED_FOR]->(rr)""",
                        s=suid, ro=refs.get("rejected_on") or refs.get("lost_on"), day=r["day"], rt=refs.get("reject_type"), epoch=r["epoch"], ts=r["ts"])
                elif t == "fte.park":
                    run("""MATCH (ap:Application {uid:$s}) SET ap.park_status=$st, ap.park_reason=$rs, ap.park_epoch=$epoch
                           MERGE (pr:ParkReason {name:coalesce($rs,'other')}) MERGE (ap)-[x:PARKED_FOR]->(pr) SET x.status=$st, x.ts=$ts""",
                        s=suid, st=refs.get("status"), rs=refs.get("reason"), epoch=r["epoch"], ts=r["ts"])
        elif t in ("critic.pass", "critic.pass.outbound"):
            run("""MATCH (e:Event {uid:$e}), (s {uid:$s}) WHERE $label IN labels(s)
                   MERGE (cr:CriticReview {uid:$e}) SET cr.result='PASS', cr.lane=$lane, cr.ts=$ts, cr.epoch=$epoch, cr.packet_path=$pp
                   MERGE (e)-[:RECORDS]->(cr) MERGE (cr)-[:PASSED]->(s)
                   MERGE (w:Agent {name:$actor}) MERGE (w)-[:REVIEWED]->(cr)""",
                e=r["uid"], s=suid, label=label, lane=refs.get("lane") or ("master" if label == "Asset" else None),
                ts=r["ts"], epoch=r["epoch"], pp=refs.get("packet_path"), actor=r["actor"])
        elif label == "Expense":
            run("""MATCH (x:Expense {uid:$s}) MERGE (v:Vendor {name:$v}) MERGE (n:Entity {name:$n})
                   MERGE (x)-[:PAID_TO]->(v) MERGE (x)-[:BILLED_TO]->(n)""", s=suid, v=str(refs.get("vendor")), n=str(refs.get("entity")))
        elif label == "Finding" and refs.get("owner"):
            run("MATCH (f:Finding {uid:$s}) MERGE (a:Agent {name:$o}) MERGE (f)-[:OWNED_BY]->(a)", s=suid, o=str(refs["owner"]))
        elif label == "Post" and r["topic"] == "social.post":
            run("MATCH (p:Post {uid:$s}) SET p.status=$st, p.last_epoch=$ep", s=suid, st=refs.get("status"), ep=r["epoch"])

    # NEXT chains per subject: rebuilt each run so late backfills slot in correctly
    run("MATCH (:Event)-[n:NEXT]->(:Event) DELETE n")
    for (label, suid), uids in subjects.items():
        for a, b in zip(uids, uids[1:]):
            run("MATCH (a:Event {uid:$a}),(b:Event {uid:$b}) MERGE (a)-[n:NEXT {subject:$s}]->(b) SET n.subject_label=$l",
                a=a, b=b, s=suid, l=label)

    # Derived application status (latest lifecycle event wins; rejection is terminal)
    run("""MATCH (ap:Application)<-[:ABOUT]-(e:Event) WHERE e.topic IN $t
           WITH ap, e ORDER BY e.epoch WITH ap, collect(e.topic) AS ts
           SET ap.status = CASE WHEN 'fte.reject' IN ts THEN 'rejected'
                                WHEN 'fte.submit' IN ts THEN 'submitted'
                                WHEN ts[-1]='fte.park' AND ap.park_status='open' THEN 'parked'
                                ELSE 'not_submitted' END""", t=list(JOB_TOPICS))
    run("""MATCH (ap:Application)-[:FOR]->(j:Job) OPTIONAL MATCH (cr:CriticReview)-[:PASSED]->(j)
           WITH ap, count(cr) AS n SET ap.critic_passes=n""")



    # Jim asks queue snapshot
    if asks:
        run("MERGE (:Agent {name:'Jim'})")
        run("MATCH (:Ask)-[w:WAITING_ON]->(:Agent) DELETE w")
        aliases = (catalog.get("coverage") or {}).get("actor_aliases", {})
        seen = []
        for a in asks:
            # the queue has three row shapes: {agent, ask}, {from, q|question}, {asker, q}
            who = a.get("agent") or a.get("from") or a.get("asker")
            who = aliases.get(who, who)
            text = a.get("ask") or a.get("q") or a.get("question") or a.get("topic") or ""
            a = {**a, "agent": who, "ask": text, "urgent": bool(a.get("urgent")) or a.get("priority") == "urgent"}
            uid = a.get("id") or h(a.get("ts"), who, str(text)[:120])
            seen.append(uid)
            ts = parse_ts(a.get("ts"))
            st = str(a.get("status", "")).split(":")[0].split(" ")[0]
            run("""MERGE (k:Ask {uid:$uid}) SET k.ts=$ts, k.epoch=$ep, k.agent=$ag, k.status=$st, k.status_raw=$sr, k.urgent=$ur,
                       k.ask=$ask, k.answered_at=$aa, k.owner_confirmed=$oc, k.id=$id
                   FOREACH (_ IN CASE WHEN $ag <> 'unknown' THEN [1] ELSE [] END |
                       MERGE (a:Agent {name:$ag}) MERGE (k)-[:ASKED_BY]->(a))
                   WITH k
                   WITH k WHERE $st IN ['open','waiting-essays','pending']
                   MATCH (j:Agent {name:'Jim'}) MERGE (k)-[:WAITING_ON]->(j)""",
                uid=uid, ts=a.get("ts"), ep=ts.timestamp() if ts else None, ag=(clean_agents([a.get("agent")]) or ["unknown"])[0], st=st,
                sr=str(a.get("status")), ur=bool(a.get("urgent")), ask=str(a.get("ask"))[:500], aa=a.get("answered_at"),
                oc=a.get("owner_confirmed"), id=a.get("id"))
        # the graph mirrors the current snapshot: drop asks that left the queue
        run("MATCH (k:Ask) WHERE NOT k.uid IN $seen DETACH DELETE k", seen=seen)

    # Flows named in the catalog (Adobe Stock, NatureNet, NIL...) are subscribers, not agents
    run("MATCH (a:Agent) WHERE a.system IS NULL AND NOT (a)-[:PUBLISHED]->() AND NOT a.name IN $roster SET a.kind='flow'", roster=ROSTER)
    run("MATCH (a:Agent) WHERE a.kind IS NULL SET a.kind = CASE WHEN a.system THEN 'system' ELSE 'agent' END")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asks", default=str(ASKS))
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--rebuild", action="store_true", help="wipe the graph first (it is fully derived from the bus)")
    args = ap.parse_args()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock = open(STATE_DIR / "load.lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX)
    catalog = json.loads((ROOT / "catalog/topics.json").read_text())
    aliases = (catalog.get("coverage") or {}).get("actor_aliases", {})
    rows = read_events(aliases)
    learn_role_sids(rows)
    asks = []
    p = Path(args.asks).expanduser()
    if p.exists():
        for line in p.read_text().splitlines():
            if line.strip():
                try:
                    asks.append(json.loads(line))
                except Exception:
                    pass
    c = creds()
    drv = GraphDatabase.driver(c.get("NEO4J_URI", "bolt://127.0.0.1:7687"), auth=(c.get("NEO4J_USER", "neo4j"), c["NEO4J_PASSWORD"]))
    for attempt in range(24):  # Neo4j can take ~30s after boot
        try:
            drv.verify_connectivity()
            break
        except Exception:
            if attempt == 23:
                raise
            time.sleep(5)
    with drv.session() as s:
        if args.rebuild:
            s.run("MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 5000 ROWS").consume()
        for q in SCHEMA:
            s.run(q).consume()
        with s.begin_transaction() as tx:
            load(lambda _cypher, **kw: tx.run(_cypher, **kw).consume(), rows, catalog, asks)
            tx.commit()
        labels = {r["l"]: r["n"] for r in s.run("MATCH (n) UNWIND labels(n) AS l RETURN l, count(*) AS n ORDER BY l")}
        rels = {r["t"]: r["n"] for r in s.run("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS n ORDER BY t")}
    drv.close()
    summary = {"loaded_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"), "events_read": len(rows),
               "asks_read": len(asks), "nodes": labels, "relationships": rels}
    (STATE_DIR / "last_load.json").write_text(json.dumps(summary, indent=2))
    if not args.quiet:
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
