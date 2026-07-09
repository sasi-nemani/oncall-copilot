#!/usr/bin/env python3
"""Generate a mixed corpus of STRUCTURED and UNSTRUCTURED incident data.

WHY: the JD asks for "pipelines for structured and unstructured data". To show that truthfully
you need a corpus that clearly contains BOTH — and that is internally COHERENT: the chats,
emails and postmortems (unstructured) narrate the very same incidents that the incident records,
deploys, alerts and metrics (structured) describe. So we treat a few INCIDENTS as the single
source of truth and render every artifact from them. random.seed() makes it reproducible.

Run:  python scripts/generate_corpus.py    ->  ./corpus/{structured,unstructured}/...
"""
import os
import csv
import json
import random
import datetime

random.seed(42)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(ROOT, "corpus")

# --- source of truth: incidents on the fictional payments platform ---
INCIDENTS = [
    dict(id="INC-101", service="checkout", sev="SEV1", metric="error_rate", deploy="checkout-v93",
         author="ravi", cause="a null-pointer in the coupon code path shipped in checkout-v93",
         fix="rolled back checkout-v93", opened="2026-06-24T09:12:00Z", closed="2026-06-24T10:05:00Z",
         summary="checkout 5xx spike right after the v93 deploy"),
    dict(id="INC-102", service="auth", sev="SEV2", metric="latency_p95", deploy="auth-a55",
         author="mei", cause="auth-a55 added a synchronous call to a slow LDAP endpoint",
         fix="feature-flagged the LDAP lookup off", opened="2026-06-25T14:03:00Z", closed="2026-06-25T15:20:00Z",
         summary="login latency climbed after auth-a55"),
    dict(id="INC-103", service="payments", sev="SEV1", metric="error_rate", deploy=None,
         author="sara", cause="the upstream card processor returned 503s during their maintenance window",
         fix="failed over to the secondary processor", opened="2026-06-26T02:40:00Z", closed="2026-06-26T03:35:00Z",
         summary="payments failures from an upstream processor outage"),
    dict(id="INC-104", service="search", sev="SEV3", metric="latency_p95", deploy="search-s12",
         author="tom", cause="search-s12 disabled query caching by mistake",
         fix="re-enabled the query cache", opened="2026-06-27T11:15:00Z", closed="2026-06-27T11:52:00Z",
         summary="search slowdown after the s12 deploy"),
]


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    return path


# ---------------- STRUCTURED (schema-aware: JSON / CSV / JSONL) ----------------
def gen_structured():
    d = os.path.join(CORPUS, "structured")
    os.makedirs(d, exist_ok=True)
    made = []

    # incident records
    recs = [dict(id=i["id"], service=i["service"], severity=i["sev"], opened_at=i["opened"],
                 closed_at=i["closed"], summary=i["summary"], root_cause=i["cause"], fix=i["fix"])
            for i in INCIDENTS]
    made.append(_write(os.path.join(d, "incidents.json"), json.dumps(recs, indent=2)))

    # deploys.csv — the incident deploys (rolled_back) + clean deploys as noise
    rows = [dict(id=i["deploy"], service=i["service"], at=i["opened"], author=i["author"],
                 status="rolled_back") for i in INCIDENTS if i["deploy"]]
    for svc in ["payments", "checkout", "search", "notifications"]:
        rows.append(dict(id=f"{svc}-{random.randint(10, 99)}", service=svc,
                         at=f"2026-06-2{random.randint(0, 3)}T0{random.randint(1, 9)}:00:00Z",
                         author=random.choice(["ravi", "mei", "sara", "tom"]), status="ok"))
    p = os.path.join(d, "deploys.csv")
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "service", "at", "author", "status"])
        w.writeheader(); w.writerows(rows)
    made.append(p)

    # alerts.csv — one firing alert per incident
    arows = [dict(id=f"ALRT-{200 + n}", service=i["service"], metric=i["metric"], severity=i["sev"],
                  state="resolved", fired_at=i["opened"], resolved_at=i["closed"])
             for n, i in enumerate(INCIDENTS, 1)]
    p = os.path.join(d, "alerts.csv")
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "service", "metric", "severity", "state",
                                          "fired_at", "resolved_at"])
        w.writeheader(); w.writerows(arows)
    made.append(p)

    # metrics.jsonl — short time-series around each incident: normal -> spike -> recover
    p = os.path.join(d, "metrics.jsonl")
    with open(p, "w") as f:
        for i in INCIDENTS:
            base = 0.3 if i["metric"] == "error_rate" else 180.0
            peak = base * random.uniform(8, 20)
            series = [base, base, peak * 0.6, peak, peak * 0.4, base]
            t0 = datetime.datetime.fromisoformat(i["opened"].replace("Z", "+00:00"))
            for k, v in enumerate(series):
                ts = (t0 + datetime.timedelta(minutes=10 * k)).isoformat()
                f.write(json.dumps(dict(service=i["service"], metric=i["metric"],
                                        at=ts, value=round(v, 2))) + "\n")
    made.append(p)
    return made


# ---------------- UNSTRUCTURED (prose: chats / emails / postmortems) ----------------
def _chat(i):
    return "\n".join([
        f"# #incident-{i['service']} — {i['id']}",
        f"[{i['opened']}] oncall-bot: :rotating_light: {i['sev']} — {i['service']} {i['metric']} alert firing ({i['id']})",
        f"[{i['opened']}] {i['author']}: ack, taking a look",
        f"[{i['opened']}] {i['author']}: {i['service']} started misbehaving — "
        + (f"anything deploy recently? I see {i['deploy']} went out" if i["deploy"]
           else "no deploys on our side, checking upstream"),
        f"[{i['opened']}] mei: yeah, timing lines up. suspect {i['cause']}",
        f"[{i['closed']}] {i['author']}: {i['fix']}. {i['metric']} recovering now.",
        f"[{i['closed']}] {i['author']}: closing {i['id']}. postmortem to follow.",
    ])


def _email(i):
    return (f"From: oncall-bot@payments.example\n"
            f"To: eng-oncall@payments.example\n"
            f"Subject: [{i['sev']}] {i['service']} incident {i['id']} — {i['summary']}\n"
            f"Date: {i['opened']}\n\n"
            f"An incident has been opened for {i['service']}.\n\n"
            f"Summary: {i['summary']}\n"
            f"Suspected cause: {i['cause']}\n"
            f"Resolution: {i['fix']}\n"
            f"Timeline: opened {i['opened']}, resolved {i['closed']}\n\n"
            f"-- On-Call Bot\n")


def _postmortem(i):
    return (f"# Postmortem — {i['id']}: {i['summary']}\n\n"
            f"- **Service:** {i['service']}\n- **Severity:** {i['sev']}\n"
            f"- **Duration:** {i['opened']} → {i['closed']}\n\n"
            f"## What happened\n{i['summary'].capitalize()}.\n\n"
            f"## Root cause\nThe root cause was {i['cause']}.\n\n"
            f"## Resolution\nWe {i['fix']}, and {i['metric']} returned to normal.\n\n"
            f"## Follow-ups\n- Add an automated check that ties a firing alert to any deploy in the "
            f"preceding 30 minutes.\n- Review {i['service']} rollback runbook for accuracy.\n")


def gen_unstructured():
    made = []
    for i in INCIDENTS:
        made.append(_write(os.path.join(CORPUS, "unstructured", "chats", f"{i['id']}.txt"), _chat(i)))
        made.append(_write(os.path.join(CORPUS, "unstructured", "emails", f"{i['id']}.eml"), _email(i)))
        made.append(_write(os.path.join(CORPUS, "unstructured", "postmortems", f"{i['id']}.md"), _postmortem(i)))
    return made


if __name__ == "__main__":
    s = gen_structured()
    u = gen_unstructured()
    print(f"corpus written to {CORPUS}")
    print(f"  structured   ({len(s)} files): " + ", ".join(os.path.basename(p) for p in s))
    print(f"  unstructured ({len(u)} files): {len(INCIDENTS)} incidents × (chat + email + postmortem)")
