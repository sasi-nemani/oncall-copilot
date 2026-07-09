#!/usr/bin/env python3
"""Generate a mixed corpus of STRUCTURED and UNSTRUCTURED incident data (procedural + scalable).

WHY: the JD asks for "pipelines for structured and unstructured data". To show that truthfully
you need a corpus that clearly contains BOTH, is internally COHERENT (the chats/emails/postmortems
narrate the same incidents the alerts/deploys/metrics/incident records describe), and is big
enough to look like a real pipeline's input. A few INCIDENTS are the single source of truth;
every artifact is rendered from them. random.seed() makes it reproducible at any size.

Run:  python scripts/generate_corpus.py [N]     # N incidents, default 40
      -> ./corpus/structured/{incidents.json,deploys.csv,alerts.csv,metrics.jsonl}
         ./corpus/unstructured/{chats,emails,postmortems}/INC-*.{txt,eml,md}   (3 per incident)
"""
import os
import csv
import sys
import json
import random
import datetime

random.seed(42)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(ROOT, "corpus")

SERVICES = ["payments", "checkout", "auth", "search", "notifications", "cart", "inventory", "shipping"]
METRICS = ["error_rate", "latency_p95", "latency_p99", "saturation", "queue_depth"]
BASE_VAL = {"error_rate": 0.3, "latency_p95": 180.0, "latency_p99": 240.0,
            "saturation": 45.0, "queue_depth": 20.0}
AUTHORS = ["ravi", "mei", "sara", "tom", "noah", "priya", "alex", "jin", "dana", "omar"]
DEPS = ["the card processor", "LDAP", "Redis", "Kafka", "the pricing service", "S3", "the fraud API", "the CDN"]
FEATURES = ["query caching", "the LDAP lookup", "rate limiting", "connection pooling", "the retry budget", "the circuit breaker"]
AREAS = ["coupon code", "checkout", "session handling", "the search index", "cart merge", "address validation"]
SEVS = ["SEV1", "SEV2", "SEV2", "SEV3", "SEV3", "SEV3"]   # weighted: SEV3 most common
BASE = datetime.datetime(2026, 3, 1)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def make_incident(n):
    svc, metric, sev = random.choice(SERVICES), random.choice(METRICS), random.choice(SEVS)
    deploy = f"{svc}-v{random.randint(10, 199)}" if random.random() < 0.65 else None
    dep, feat, area = random.choice(DEPS), random.choice(FEATURES), random.choice(AREAS)
    opened = BASE + datetime.timedelta(days=random.randint(0, 120), hours=random.randint(0, 23),
                                       minutes=random.randint(0, 59))
    closed = opened + datetime.timedelta(minutes=random.randint(15, 180))
    if deploy:
        cause = random.choice([
            f"a null-pointer in the {area} path shipped in {deploy}",
            f"{deploy} added a synchronous call to slow {dep}",
            f"{deploy} disabled {feat} by mistake",
            f"a bad config in {deploy} that lowered a timeout to near-zero"])
        fix = random.choice([f"rolled back {deploy}", f"feature-flagged {feat} off",
                             f"reverted the config change in {deploy}"])
    else:
        cause = random.choice([
            f"{dep} returned 503s during a maintenance window",
            f"connection-pool exhaustion in {svc} under load",
            f"a retry storm after {dep} slowed down",
            f"{svc} pods OOM-killed by a slow memory leak"])
        fix = random.choice([f"failed over to a secondary {dep}",
                             f"scaled {svc} out and raised the pool size",
                             f"restarted the affected {svc} pods"])
    return dict(id=f"INC-{100 + n}", service=svc, sev=sev, metric=metric, deploy=deploy,
                author=random.choice(AUTHORS), dep=dep, cause=cause, fix=fix,
                opened=_iso(opened), closed=_iso(closed),
                summary=f"{svc} {metric} incident" + (f" after {deploy}" if deploy else " from an upstream issue"))


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


# ---------------- STRUCTURED (schema-aware records: JSON / CSV / JSONL) ----------------
def gen_structured(incidents):
    d = os.path.join(CORPUS, "structured")
    os.makedirs(d, exist_ok=True)

    recs = [dict(id=i["id"], service=i["service"], severity=i["sev"], opened_at=i["opened"],
                 closed_at=i["closed"], summary=i["summary"], root_cause=i["cause"], fix=i["fix"])
            for i in incidents]
    _write(os.path.join(d, "incidents.json"), json.dumps(recs, indent=2))

    dep_rows = [dict(id=i["deploy"], service=i["service"], at=i["opened"], author=i["author"],
                     status="rolled_back") for i in incidents if i["deploy"]]
    for _ in range(len(incidents) // 2):   # clean deploys as realistic noise
        svc = random.choice(SERVICES)
        dep_rows.append(dict(id=f"{svc}-v{random.randint(10, 199)}", service=svc,
                             at=_iso(BASE + datetime.timedelta(days=random.randint(0, 120))),
                             author=random.choice(AUTHORS), status="ok"))
    with open(os.path.join(d, "deploys.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "service", "at", "author", "status"])
        w.writeheader(); w.writerows(dep_rows)

    arows = [dict(id=f"ALRT-{200 + n}", service=i["service"], metric=i["metric"], severity=i["sev"],
                  state="resolved", fired_at=i["opened"], resolved_at=i["closed"])
             for n, i in enumerate(incidents)]
    with open(os.path.join(d, "alerts.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "service", "metric", "severity", "state", "fired_at", "resolved_at"])
        w.writeheader(); w.writerows(arows)

    with open(os.path.join(d, "metrics.jsonl"), "w") as f:
        for i in incidents:
            base = BASE_VAL[i["metric"]]
            peak = base * random.uniform(6, 20)
            series = [base, base, peak * 0.6, peak, peak * 0.4, base]
            t0 = datetime.datetime.strptime(i["opened"], "%Y-%m-%dT%H:%M:%SZ")
            for k, v in enumerate(series):
                ts = _iso(t0 + datetime.timedelta(minutes=10 * k))
                f.write(json.dumps(dict(service=i["service"], metric=i["metric"], at=ts, value=round(v, 2))) + "\n")


# ---------------- UNSTRUCTURED (prose: chats / emails / postmortems) ----------------
def _chat(i):
    return "\n".join([
        f"# #incident-{i['service']} — {i['id']}",
        f"[{i['opened']}] oncall-bot: :rotating_light: {i['sev']} — {i['service']} {i['metric']} alert firing ({i['id']})",
        f"[{i['opened']}] {i['author']}: ack, on it",
        f"[{i['opened']}] {i['author']}: "
        + (f"{i['deploy']} went out just before — timing lines up" if i["deploy"]
           else "no deploys our side, looks upstream"),
        f"[{i['opened']}] mei: agreed, suspect {i['cause']}",
        f"[{i['closed']}] {i['author']}: {i['fix']}. {i['metric']} recovering. closing {i['id']}.",
    ])


def _email(i):
    return (f"From: oncall-bot@payments.example\nTo: eng-oncall@payments.example\n"
            f"Subject: [{i['sev']}] {i['service']} incident {i['id']} — {i['summary']}\nDate: {i['opened']}\n\n"
            f"Summary: {i['summary']}\nSuspected cause: {i['cause']}\nResolution: {i['fix']}\n"
            f"Timeline: opened {i['opened']}, resolved {i['closed']}\n\n-- On-Call Bot\n")


def _postmortem(i):
    return (f"# Postmortem — {i['id']}: {i['summary']}\n\n"
            f"- **Service:** {i['service']}\n- **Severity:** {i['sev']}\n"
            f"- **Duration:** {i['opened']} → {i['closed']}\n\n"
            f"## Root cause\nThe root cause was {i['cause']}.\n\n"
            f"## Resolution\nWe {i['fix']}, and {i['metric']} returned to normal.\n\n"
            f"## Follow-ups\n- Auto-correlate a firing alert with deploys in the prior 30 minutes.\n"
            f"- Review the {i['service']} rollback runbook.\n")


def gen_unstructured(incidents):
    for i in incidents:
        _write(os.path.join(CORPUS, "unstructured", "chats", f"{i['id']}.txt"), _chat(i))
        _write(os.path.join(CORPUS, "unstructured", "emails", f"{i['id']}.eml"), _email(i))
        _write(os.path.join(CORPUS, "unstructured", "postmortems", f"{i['id']}.md"), _postmortem(i))


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    incidents = [make_incident(k) for k in range(n)]
    gen_structured(incidents)
    gen_unstructured(incidents)
    print(f"corpus written to {CORPUS}")
    print(f"  {n} incidents -> structured (4 files) + unstructured ({3 * n} files: "
          f"{n} chats + {n} emails + {n} postmortems)")
