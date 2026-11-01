// Agent graph learning queries. Run any of these by name with:
//   $AGENT_BUS_ROOT/bin/graphq.py <name> [--param key=value] [--json]
// Block format: "// name:", "// desc:", optional "// params: k=v, k=v" defaults, then one Cypher statement ending in ";".
// Notes: "applied" means the Application has an fte.submit or fte.reject event. The bus has no reply/screen/offer
// topic yet, so outcomes are rejected vs still open (silence). bus.test events are excluded everywhere.

// name: conversion_by_role
// desc: Apply outcome by role level (VP / Head / Sr Director / Director / Manager / Other)
MATCH (ap:Application)-[:FOR]->(j:Job)
WHERE ap.status IN ['submitted','rejected']
WITH ap, toLower(coalesce(j.role,'')) AS r
WITH ap, CASE
  WHEN r CONTAINS 'chief' OR r =~ '.*\\bcto\\b.*' THEN 'C-level'
  WHEN r CONTAINS 'vice president' OR r =~ '.*\\b(s?vp)\\b.*' THEN 'VP'
  WHEN r CONTAINS 'head of' OR r STARTS WITH 'head' THEN 'Head of'
  WHEN r =~ '.*\\b(senior|sr\\.?) director\\b.*' THEN 'Senior Director'
  WHEN r CONTAINS 'director' THEN 'Director'
  WHEN r CONTAINS 'manager' THEN 'Manager'
  ELSE 'Other' END AS level
RETURN level, count(*) AS applied, sum(CASE WHEN ap.status='rejected' THEN 1 ELSE 0 END) AS rejected,
       sum(CASE WHEN ap.status='submitted' THEN 1 ELSE 0 END) AS open_no_answer,
       round(100.0*sum(CASE WHEN ap.status='rejected' THEN 1 ELSE 0 END)/count(*),0) AS reject_pct
ORDER BY applied DESC;

// name: conversion_by_focus
// desc: Apply outcome by role focus (AI/ML/data vs platform/cloud vs general eng)
MATCH (ap:Application)-[:FOR]->(j:Job)
WHERE ap.status IN ['submitted','rejected']
WITH ap, toLower(coalesce(j.role,'')) AS r
WITH ap, CASE
  WHEN r =~ '.*\\b(ai|ml|machine learning|genai|agentic|llm)\\b.*' THEN 'AI/ML'
  WHEN r =~ '.*\\b(data|analytics)\\b.*' THEN 'Data'
  WHEN r =~ '.*\\b(platform|cloud|infrastructure|devops|sre)\\b.*' THEN 'Platform/Cloud'
  ELSE 'General engineering' END AS focus
RETURN focus, count(*) AS applied, sum(CASE WHEN ap.status='rejected' THEN 1 ELSE 0 END) AS rejected,
       sum(CASE WHEN ap.status='submitted' THEN 1 ELSE 0 END) AS open_no_answer
ORDER BY applied DESC;

// name: conversion_by_company
// desc: Companies with the most applications and how they answered
// params: limit=20
MATCH (ap:Application)-[:FOR]->(:Job)-[:AT]->(c:Company)
WHERE ap.status IN ['submitted','rejected']
RETURN c.name AS company, count(*) AS applied,
       sum(CASE WHEN ap.status='rejected' THEN 1 ELSE 0 END) AS rejected,
       sum(CASE WHEN ap.status='submitted' THEN 1 ELSE 0 END) AS open_no_answer
ORDER BY applied DESC, rejected DESC LIMIT $limit;

// name: conversion_by_cv
// desc: Apply outcome by CV variant (only submits that logged refs.cv_variant)
MATCH (ap:Application) WHERE ap.status IN ['submitted','rejected']
OPTIONAL MATCH (ap)-[:USED_CV]->(cv:CVVersion)
RETURN coalesce(cv.name,'(not logged)') AS cv_variant, count(*) AS applied,
       sum(CASE WHEN ap.status='rejected' THEN 1 ELSE 0 END) AS rejected,
       sum(CASE WHEN ap.status='submitted' THEN 1 ELSE 0 END) AS open_no_answer
ORDER BY applied DESC;

// name: conversion_by_ats
// desc: Apply outcome by ATS (ashby, greenhouse, workday, lever...)
MATCH (ap:Application) WHERE ap.status IN ['submitted','rejected']
RETURN coalesce(toLower(ap.ats),'(not logged)') AS ats, count(*) AS applied,
       sum(CASE WHEN ap.status='rejected' THEN 1 ELSE 0 END) AS rejected
ORDER BY applied DESC;

// name: reject_reasons
// desc: Rejection types and average days from submit to the bus logging the reject
MATCH (ap:Application)-[:REJECTED_FOR]->(r:RejectReason)
RETURN r.name AS reject_type, count(*) AS n,
       round(avg(CASE WHEN ap.submitted_epoch IS NOT NULL THEN (ap.rejected_epoch-ap.submitted_epoch)/86400.0 END),1) AS avg_days_to_reject
ORDER BY n DESC;

// name: park_reasons
// desc: Why packets park instead of submitting (latest park per application)
MATCH (ap:Application)-[x:PARKED_FOR]->(p:ParkReason)
RETURN p.name AS reason, count(DISTINCT ap) AS applications,
       sum(CASE WHEN ap.status='submitted' OR ap.status='rejected' THEN 1 ELSE 0 END) AS later_submitted
ORDER BY applications DESC;

// name: critic_by_lane
// desc: Writing Critic passes by lane and ISO week
MATCH (cr:CriticReview)<-[:RECORDS]-(e:Event)
RETURN coalesce(cr.lane,'(none)') AS lane, date(datetime({epochSeconds:toInteger(e.epoch)})).week AS iso_week, count(*) AS passes
ORDER BY iso_week DESC, passes DESC;

// name: critic_rework
// desc: Jobs/assets that needed 2+ Critic passes (rework proxy; the bus has no critic.fail topic)
MATCH (cr:CriticReview)-[:PASSED]->(s)
WITH s, count(cr) AS passes WHERE passes > 1
OPTIONAL MATCH (s)-[:AT]->(c:Company)
RETURN coalesce(c.name, '') AS company, left(coalesce(s.role, s.name, s.uid),70) AS subject, passes
ORDER BY passes DESC LIMIT 20;

// name: critic_pass_not_submitted
// desc: FTE jobs with a Critic PASS but no submit, reject or open park on the bus (lost packets)
MATCH (cr:CriticReview)-[:PASSED]->(j:Job)-[:AT]->(c:Company)
WHERE NOT EXISTS { MATCH (j)<-[:FOR]-(ap:Application) WHERE ap.status IN ['submitted','rejected','parked'] }
WITH j, c, max(cr.epoch) AS last_pass
RETURN c.name AS company, left(j.role,60) AS role,
       toInteger((datetime().epochSeconds - last_pass)/86400) AS days_since_pass
ORDER BY days_since_pass ASC;

// name: critic_to_submit_hours
// desc: Hours from last Critic PASS to submit, by company (handoff speed)
// params: limit=15
MATCH (cr:CriticReview)-[:PASSED]->(j:Job)<-[:FOR]-(ap:Application)
WHERE ap.submitted_epoch IS NOT NULL AND cr.epoch <= ap.submitted_epoch
WITH ap, j, max(cr.epoch) AS pass_epoch
MATCH (j)-[:AT]->(c:Company)
RETURN c.name AS company, round((ap.submitted_epoch - pass_epoch)/3600.0,1) AS hours_pass_to_submit
ORDER BY hours_pass_to_submit DESC LIMIT $limit;

// name: stalls
// desc: Work items with no follow-up event on the same subject for N hours
// params: hours=48, limit=25
MATCH (e:Event)
WHERE e.test = false AND e.epoch < datetime().epochSeconds - $hours*3600
  AND ( (e.topic='critic.pass' AND coalesce(e.lane,'fte') IN ['fte','warm_note'] AND NOT toLower(coalesce(e.company,'')) IN ['master','masters'])
     OR (e.topic='fte.park' AND e.status='open')
     OR (e.topic='social.post' AND e.status STARTS WITH 'draft')
     OR e.topic IN ['shop.exclusive_draft_ready','fte.hold_cleared'] )
  AND NOT (e)-[:NEXT]->()
RETURN e.topic AS topic, e.actor AS actor, coalesce(e.company,'') AS subject, left(coalesce(e.role,''),50) AS role,
       e.day AS day, toInteger((datetime().epochSeconds - e.epoch)/3600) AS hours_waiting
ORDER BY hours_waiting ASC LIMIT $limit;

// name: asks_waiting_on_jim
// desc: Open asks in the Jim asks queue snapshot, oldest first
MATCH (k:Ask)-[:WAITING_ON]->(:Agent {name:'Jim'})
RETURN k.agent AS agent, k.urgent AS urgent, toInteger((datetime().epochSeconds - k.epoch)/86400) AS days_waiting,
       left(k.ask, 90) AS ask
ORDER BY days_waiting DESC;

// name: agents_silent
// desc: Agents with no real bus event for longer than their catalog silent_days (or --param days=N)
// params: days=0
MATCH (a:Agent {kind:'agent'})
OPTIONAL MATCH (a)-[:PUBLISHED]->(e:Event) WHERE e.test = false
WITH a, max(e.epoch) AS last, count(e) AS events
WITH a, events, last, CASE WHEN $days > 0 THEN $days ELSE coalesce(a.silent_days, 3) END AS allowed_days,
     CASE WHEN last IS NULL THEN null ELSE round((datetime().epochSeconds - last)/86400.0, 1) END AS days_silent
RETURN a.name AS agent, events, days_silent, allowed_days,
       (last IS NULL OR days_silent > allowed_days) AND NOT coalesce(date(a.snooze_until) >= date(), false) AS flagged,
       a.snooze_until AS snoozed_until, left(coalesce(a.quiet_ok,''),50) AS quiet_ok
ORDER BY flagged DESC, days_silent DESC;

// name: topic_coverage
// desc: Every topic: real event count, last day, allowed publishers, subscriber count (connectivity audit)
MATCH (t:Topic)
OPTIONAL MATCH (t)<-[:IN_TOPIC]-(e:Event) WHERE e.test = false
WITH t, count(e) AS events, max(e.day) AS last_day
OPTIONAL MATCH (p:Agent)-[:CAN_PUBLISH]->(t)
WITH t, events, last_day, collect(p.name) AS publishers
OPTIONAL MATCH (s:Agent)-[:SUBSCRIBES]->(t)
RETURN t.name AS topic, coalesce(t.in_catalog,false) AS in_catalog, events, last_day, publishers, count(s) AS subscribers
ORDER BY events ASC, topic;

// name: publisher_gaps
// desc: Agents the catalog lets publish a topic who have never published it (Admin excluded)
MATCH (a:Agent {kind:'agent'})-[:CAN_PUBLISH]->(t:Topic)
WHERE a.name <> 'Admin' AND NOT EXISTS { MATCH (a)-[:PUBLISHED]->(e:Event)-[:IN_TOPIC]->(t) WHERE e.test = false }
RETURN a.name AS agent, collect(t.name) AS never_published
ORDER BY agent;

// name: handoffs
// desc: Who picks up whose work next on the same subject, and how long it takes (bottlenecks)
MATCH (a:Agent)-[:PUBLISHED]->(e1:Event)-[:NEXT]->(e2:Event)<-[:PUBLISHED]-(b:Agent)
WHERE a <> b
RETURN a.name AS from_agent, e1.topic AS from_topic, b.name AS to_agent, e2.topic AS to_topic, count(*) AS n,
       round(avg((e2.epoch - e1.epoch)/3600.0),1) AS avg_hours
ORDER BY n DESC LIMIT 20;

// name: daily_submits
// desc: FTE submits per bus day, last N days
// params: days=14
MATCH (e:Event {topic:'fte.submit'})
WHERE e.epoch > datetime().epochSeconds - $days*86400
RETURN e.day AS day, count(*) AS submits ORDER BY day DESC;

// name: expenses_by_vendor
// desc: Logged business expenses by entity and vendor
MATCH (x:Expense)-[:PAID_TO]->(v:Vendor), (x)-[:BILLED_TO]->(n:Entity)
RETURN n.name AS entity, v.name AS vendor, count(*) AS receipts, round(sum(toFloat(x.amount)),2) AS total
ORDER BY total DESC;

// name: graph_counts
// desc: Node counts by label (health check)
MATCH (n) UNWIND labels(n) AS label RETURN label, count(*) AS n ORDER BY label;
