#!/usr/bin/env python3
"""Catalog v8 roster migration (Jim 2026-11-01 "i approve all 8 suggestions"). Idempotent:
re-running on an already-migrated catalog produces byte-identical output."""
import json, sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".").expanduser()
CAT = ROOT / "catalog" / "topics.json"
c = json.loads(CAT.read_text())

RENAME = {  # old agent name -> new canonical name
    "FTE Apply": "Job Search", "Investment": "Consulting", "Marketing": "Content Studio",
    "Stills & Clips": "Content Studio", "SEO Auditor": "Content Studio", "Sales": "OAWJ Shop",
    "Expense Manager": "Books", "Subscription Cleaner": "Books", "Disk Saver": "Admin",
    "Travel": "Travel & Personal",
}
# Ops lanes moved per topic (Ops itself retires after one clean week, not now)
OPS_TO = {"fte.reject": "Job Search", "fte.submit": None, "catalant.email_confirmed": "Consulting",
          "shop.order_received": "OAWJ Shop", "adobe.review_logged": "Content Studio",
          "adobe.batch_submitted": None}
IDS = {
    "Admin": "fa50d9b1-127a-404a-81d2-7bf61b49e1fe", "Job Search": "a8926586-fbf3-4d6c-8f0a-790e65ead1bd",
    "Writing Critic": "80f6148a-faf0-4d35-96b5-d9047389209a", "Unemployment": "b31aa760-294c-437c-82fe-9be43ebeff0c",
    "Consulting": "dd0ab463-c048-4751-87c7-68b6f041d6d9", "Markets": "bc706fe2-1f93-40f5-83a5-81659f154d1d",
    "North Idaho Labs": "cd904b26-f530-418d-bffc-39c46a0451e5", "NatureNet": "cea6bba3-83ef-460d-98b4-b81f565c2026",
    "Books": "c8c41a30-3468-484b-8e16-ed184224e00d", "Content Studio": "5d55b9e2-f9a6-45b0-8371-5254e17eb025",
    "Social Media": "0b44ceee-7329-4bd0-9c7f-2288abdd0782", "OAWJ Shop": "9c5fcb12-9a8e-42f3-b619-c7ccb804e3fd",
    "Ops": "bc44de8b-c063-4a99-96b3-a3dbc64ef963", "Travel & Personal": "34ff498e-f62a-48ec-bda8-8b8ea4a62ae4",
    "Grok Bot": "bedb13fb-aecb-4e5a-840d-523a921d977a",
}
MERGED_SEATS = {  # old seats folded in; their ids stay resolvable for history until retired
    "Stills & Clips": "ca62e4bf-ae28-4748-a4b6-5c016f0a83cd", "SEO Auditor": "624476b9-0878-48cd-ae81-00a0d6e08e00",
    "Subscription Cleaner": "b5b207f9-7281-423f-a743-3aba78cb1176", "Disk Saver": "dff6c0dd-91f2-42eb-9cac-c27e7381defa",
}


def rn(x):
    if isinstance(x, str):
        return RENAME.get(x, x)
    if isinstance(x, list):
        out = []
        for v in x:
            v = rn(v)
            if v not in out:
                out.append(v)
        return out
    if isinstance(x, dict):
        return {k: rn(v) for k, v in x.items()}
    return x


AGENT_KEYS = {"publishers", "subscribers", "must_act", "owner", "primary"}


def walk(obj):
    """Rename agent names only in agent-bearing keys (never in descriptions/notes)."""
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if k in AGENT_KEYS:
                obj[k] = rn(v)
            elif k == "must_act_rules":
                for r in v:
                    r["must_act"] = rn(r.get("must_act", []))
            else:
                walk(v)
    elif isinstance(obj, list):
        for v in obj:
            walk(v)


walk(c["topics"])
walk(c["coverage"].get("expected_downstream", []))
walk(c["follow_through"])

T = c["topics"]
for t, new in OPS_TO.items():
    m = T.get(t)
    if not m:
        continue
    for key in ("publishers", "subscribers"):
        lst = [a for a in m.get(key, []) if a != "Ops"]
        if key == "publishers" and new and "Ops" in m.get(key, []) and new not in lst:
            lst.insert(0, new)
        m[key] = lst
    m["must_act"] = [a for a in m.get("must_act", []) if a != "Ops"]

# fte.reject: Job Search owns the reject loop now (Admin drops job policy)
T["fte.reject"]["must_act"] = ["Job Search"]
T["fte.reject"]["must_act_note"] = "Job Search runs the reject learning loop (moved from Admin 2026-11-01); others pull."
if "Job Search" not in T["ws.short_of_target"]["publishers"]:
    T["ws.short_of_target"]["publishers"].append("Job Search")
for t in ("critic.pass.outbound", "seo.audit"):
    for a in ("North Idaho Labs", "NatureNet"):
        if a not in T[t]["subscribers"]:
            T[t]["subscribers"].append(a)
if "Books" not in T["ui.cert_filed"]["subscribers"]:
    T["ui.cert_filed"]["subscribers"].append("Books")
T["fte.submit"]["must_act_note"] = "GE sync is the publish hook; Unemployment pulls at cert/weekly WS; Admin pulls at ledger."

ALL_PUB = ["Grok Bot", "Admin", "Job Search", "Writing Critic", "Unemployment", "Consulting", "Books",
           "Content Studio", "Social Media", "OAWJ Shop", "Travel & Personal", "North Idaho Labs", "NatureNet"]
NEW_TOPICS = {
    "fte.response": dict(description="Employer outcome on a submitted FTE role other than a reject: recruiter reply, screen, interview, assessment, offer",
        publishers=["Job Search", "Admin"], subscribers=["Admin", "Writing Critic", "Unemployment", "Grok Bot"], must_act=["Grok Bot"],
        must_act_note="Larry (Grok Bot) gets Jim's time on the calendar; others pull.", required_refs=["company", "role", "source_id", "kind"],
        done_means="GE stage updated; Larry has the screen/interview on Jim's card; the graph links the outcome to its Application."),
    "fte.harvest": dict(description="Harvest sweep finished (sources, found, kept after filter rules)",
        publishers=["Job Search"], subscribers=["Admin"], must_act=[], required_refs=["sweep", "found", "kept", "path"],
        done_means="Harvest file at refs.path; kept count feeds the conversion queries."),
    "fte.filter_change": dict(description="FTE job filter rule added, changed or removed",
        publishers=["Job Search", "Admin"], subscribers=["Admin", "Writing Critic"], must_act=[], required_refs=["rule", "change", "reason", "jim_ok"],
        done_means="fte-job-filter-rules skill text matches the change; Jim's yes cited when the rule is Jim-locked; evidence graph query in note."),
    "critic.revise": dict(description="Writing Critic REVISE or FAIL verdict with fail tags",
        publishers=["Writing Critic"], subscribers=["Admin", "Job Search", "Consulting", "OAWJ Shop", "Content Studio", "Social Media"],
        must_act=["refs.owner"], must_act_note="Wakes only the drafting owner.", required_refs=["lane", "packet_path", "fail_tags", "owner"],
        done_means="Owner revised and resubmitted (later critic.pass) or dropped with a note; fail_tags feed the Friday learning review."),
    "income.earned": dict(description="Money earned or invoiced (gross, in the week worked), not yet paid",
        publishers=["Consulting", "OAWJ Shop", "Books", "Admin"], subscribers=["Books", "Unemployment", "Admin", "Grok Bot"], must_act=["Books"],
        required_refs=["entity", "source", "amount_gross", "work_date", "status"],
        done_means="Books ledger row written (revenue_2026.csv); Unemployment counts the gross in the week worked."),
    "income.paid": dict(description="Earned income actually received",
        publishers=["Books", "Admin"], subscribers=["Consulting", "OAWJ Shop", "Unemployment", "Admin"], must_act=[],
        required_refs=["entity", "source", "amount", "paid_date", "earned_key"],
        done_means="Ledger row marked paid and matched to its income.earned key."),
    "jim.ask": dict(description="A question or approval waiting on Jim (mirrors /workspace/jim-asks/queue.jsonl)",
        publishers=ALL_PUB, subscribers=["Grok Bot", "Admin"], must_act=["Grok Bot"], required_refs=["ask_id", "owner", "deadline"],
        done_means="Row in the asks queue and shown in the next 8 AM / 4 PM batch (urgent: now)."),
    "jim.answer": dict(description="Jim answered an ask (approve / hold / change / no)",
        publishers=["Grok Bot", "Admin"], subscribers=["Admin"], must_act=["refs.owner"], required_refs=["ask_id", "owner", "answer"],
        done_means="Owner acted on the answer (next lane event) or published learning.applied."),
    "learning.proposal": dict(description="Evidence-backed change proposal from the Friday learning review",
        publishers=["Admin"], subscribers=["Grok Bot"], must_act=["Grok Bot"], required_refs=["proposal_id", "target", "evidence_query"],
        done_means="In Jim's 4 PM asks batch; nothing edited until Jim says yes."),
    "learning.applied": dict(description="A profile, skill, routine, filter or catalog change went live",
        publishers=ALL_PUB, subscribers=["Admin", "Grok Bot"], must_act=[], required_refs=["change_id", "target", "jim_ok"],
        done_means="Change is live and cites Jim's approval; the graph links it to the proposal or ask."),
    "shop.reply": dict(description="Named-buyer or shop inbox reply (licensing, print, extended)",
        publishers=["OAWJ Shop"], subscribers=["Admin", "Books"], must_act=[], required_refs=["prospect", "thread", "kind"],
        done_means="Reply logged against the prospect; quote or follow-up drafted for Critic if needed."),
    "marketplace.outcome": dict(description="Marketplace or expert-network outcome (shortlisted, won, lost, call booked, call done)",
        publishers=["Consulting"], subscribers=["Admin", "Books"], must_act=[], required_refs=["platform", "project", "outcome"],
        done_means="Outcome logged; a done paid call also publishes income.earned."),
    "social.metrics": dict(description="Weekly social metrics snapshot per channel",
        publishers=["Social Media"], subscribers=["Admin", "Content Studio"], must_act=[], required_refs=["week", "channel"],
        done_means="Numbers cited from the platform; feeds the content queries."),
    "grant.submitted": dict(description="Grant, pitch or registration Jim submitted (NSF, SBIR, SAM, etc.)",
        publishers=["NatureNet", "North Idaho Labs"], subscribers=["Admin", "Books", "Writing Critic"], must_act=[],
        required_refs=["entity", "program", "submitted_by", "confirmation"],
        done_means="Jim submitted it himself; confirmation path or id in refs."),
    "site.deployed": dict(description="Public site change deployed and verified live (northidaholabs.com, atlas, news.latah, ems, OAWJ site copy)",
        publishers=["North Idaho Labs", "Content Studio"], subscribers=["Admin", "Content Studio"], must_act=[], required_refs=["site", "pr", "live_url"],
        done_means="Merged PR deployed and live URL checked."),
}
for name, meta in NEW_TOPICS.items():
    meta.setdefault("state_file", None)
    T.setdefault(name, meta)

cov = c["coverage"]
al = cov.setdefault("actor_aliases", {})
for k in list(al):
    al[k] = RENAME.get(al[k], al[k])
for old, new in RENAME.items():
    al[old] = new
al.setdefault("fte apply", "Job Search")
al.setdefault("larry", "Grok Bot")
al.setdefault("systems", "Admin")
al.setdefault("nil", "North Idaho Labs")

old = cov["agents"]
def take(name, default):
    return old.get(name) or default
newcov = {
    "Admin": take("Admin", {"silent_days": 1}),
    "Writing Critic": take("Writing Critic", {"silent_days": 3}),
    "Job Search": take("Job Search", old.get("FTE Apply", {"silent_days": 2, "weekdays_only": True})),
    "OAWJ Shop": take("OAWJ Shop", old.get("Sales", {"silent_days": 4})),
    "Content Studio": take("Content Studio", {**old.get("Marketing", {"silent_days": 4}),
        "quiet_ok": "Shorts/Adobe batches, stills/clips prep and SEO audits come in waves; LCC paused until the kickoff video is public."}),
    "Consulting": take("Consulting", {"silent_days": 5,
        "quiet_ok": "Catalant (Mondays), TrueBridge/BTG/EVONA and expert-network calls are opportunity-driven."}),
    "Social Media": take("Social Media", {"silent_days": 7}),
    "Books": take("Books", {**old.get("Expense Manager", {"silent_days": 8}),
        "quiet_ok": "Monday receipt sweep; income rows when money is earned/paid; subscription review on request."}),
    "Travel & Personal": take("Travel & Personal", {"silent_days": 14, "on_request": True, "since": "2026-10-15",
        "quiet_ok": "Trip and personal chores on request; garden check is not a bus event."}),
    "Unemployment": take("Unemployment", {"silent_days": 8}),
    "North Idaho Labs": take("North Idaho Labs", {"silent_days": 14, "on_request": True, "since": "2026-11-01",
        "quiet_ok": "Publishes site.deployed / grant.submitted when they happen; added to the bus 2026-11-01."}),
    "NatureNet": take("NatureNet", {"silent_days": 14, "on_request": True, "since": "2026-11-01",
        "quiet_ok": "Publishes grant.submitted when Jim files; added to the bus 2026-11-01."}),
    "Ops": {"exempt": True, "retire_after": "2026-11-08",
        "quiet_ok": "Lanes moved 2026-11-01 (job mail -> Job Search, verify clicks -> Consulting, shop/Stripe/deploy -> OAWJ Shop). Retire after one clean week."},
    "Markets": take("Markets", {"exempt": True, "quiet_ok": "Research only; no side effects (Jim-approved off-bus)."}),
    "moms health": take("moms health", {"exempt": True, "quiet_ok": "Private; off the bus."}),
    "ex wife watch": take("ex wife watch", {"exempt": True, "quiet_ok": "Private; off the bus."}),
}
cov["agents"] = newcov
cov["note"] = ("Read by bin/busctl.py coverage and bin/bus_watchdog.py. silent_days = calendar days (business days when weekdays_only) "
               "a publisher may go without a real event before it is flagged. snooze_until (YYYY-MM-DD) mutes one agent. "
               "on_request=true: works only when asked, never flagged SILENT/NEVER. exempt=true: off the bus by design. "
               "quiet_ok explains why silence is normally fine. Admin owns this block.")

c["agents"] = dict(IDS)
c["agents_note"] = ("Roster v8 (Jim 2026-11-01 'i approve all 8 suggestions'): canonical agent name -> agent id. "
                    "Old names resolve through coverage.actor_aliases; see agent_renames for history.")
c["agent_renames"] = {
    "date": "2026-11-01",
    "approval": "Jim 2026-11-01 11:59 AM PT: 'i approve all 8 suggestions' (relayed by Larry)",
    "renames": {"FTE Apply": "Job Search", "Investment": "Consulting", "Marketing": "Content Studio",
                "Sales": "OAWJ Shop", "Expense Manager": "Books", "Travel": "Travel & Personal"},
    "merged_into": {"Stills & Clips": "Content Studio", "SEO Auditor": "Content Studio",
                    "Subscription Cleaner": "Books", "Disk Saver": "Admin"},
    "merged_seat_ids": MERGED_SEATS,
    "pending": {"Ops": "retire after one clean week (~2026-11-08)", "ex wife watch": "waiting on Jim's one-liner"},
}
c["version"] = 8
c["updated_at"] = "2026-11-01"
if "roster v8" not in c.get("updated_note", ""):
    c["updated_note"] = ("2026-11-01 roster v8 (Jim: 'i approve all 8 suggestions'): agent renames/merges with alias map so history resolves; "
                         "new topics fte.response, fte.harvest, fte.filter_change, critic.revise, income.earned/paid, jim.ask/answer, "
                         "learning.proposal/applied, shop.reply, marketplace.outcome, social.metrics, grant.submitted, site.deployed; "
                         "NIL + NatureNet added; Markets/private agents exempt; on-request agents never flagged silent; Ops lanes moved, Ops exempt until retirement. "
                         "Earlier: " + c.get("updated_note", ""))
CAT.write_text(json.dumps(c, indent=2, ensure_ascii=False) + "\n")
print("catalog v8 written:", len(T), "topics,", len(newcov), "coverage agents")
