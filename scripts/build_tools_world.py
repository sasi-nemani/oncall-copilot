#!/usr/bin/env python3
"""Bridge: derive the live-tools world (data_v2/) FROM the v2 corpus (corpus/structured/).

Why: the retrieval corpus (INC-100+) and the live tools were two different worlds (the tools read the
old v1 demo data), so the agent couldn't investigate a v2 incident with its tools — it had to run
RAG-only. This regenerates the tool-facing files from the SAME incidents the corpus documents, so
`get_metric` / `recent_deploys` / `get_alerts` / `get_incident_timeline` / `search_logs` describe the
exact world the postmortems describe.

It also picks the most recent incident as CURRENTLY happening — its alert is firing, its metric is
breaching NOW, its deploy is the latest — so there's a live incident to investigate end to end
(observe alert -> check metric -> find the deploy -> retrieve the runbook -> propose the rollback).

Deterministic: derived only from the (seeded) corpus + fixed series, so same corpus -> same world.

Run:  python scripts/build_tools_world.py            # writes data_v2/
Use:  TOOLS_DATA_DIR=data_v2 RETRIEVAL_SOURCE=index  (point tools AND retrieval at the v2 world)
"""
import os
import csv
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(ROOT, "corpus", "structured")
OUT = os.path.join(ROOT, "data_v2")

# Fixed metric series (no RNG -> deterministic), one per corpus metric. Threshold-aware: baseline sits
# below `warn` and breach ends above `crit` (src/tools.py THRESHOLDS) so get_metric reads OK vs CRITICAL.
BASELINE = {
    "error_rate":  [0.2, 0.3, 0.2, 0.3, 0.2, 0.3, 0.2, 0.3],          # crit 2.0%
    "latency_p95": [200, 210, 190, 205, 200, 205, 195, 205],          # crit 1000ms
    "latency_p99": [400, 420, 390, 410, 400, 415, 395, 410],          # crit 1500ms
    "queue_depth": [100, 120, 90, 110, 100, 115, 95, 110],            # crit 5000
    "saturation":  [30, 35, 28, 33, 30, 34, 29, 32],                  # crit 90%
}
BREACH = {
    "error_rate":  [0.3, 0.4, 0.9, 1.6, 2.3, 3.1, 3.6, 3.8],          # -> CRITICAL
    "latency_p95": [210, 260, 430, 700, 980, 1180, 1350, 1440],
    "latency_p99": [420, 560, 820, 1180, 1520, 1900, 2200, 2400],
    "queue_depth": [200, 500, 1200, 2500, 4000, 5500, 6800, 7200],
    "saturation":  [30, 45, 60, 75, 85, 92, 96, 98],
}
GENERIC_BREACH = [10, 14, 28, 51, 72, 88, 95, 98]                     # fallback for any other metric


def _load():
    incidents = json.load(open(os.path.join(CORPUS, "incidents.json")))
    deploys = list(csv.DictReader(open(os.path.join(CORPUS, "deploys.csv"))))
    alerts = list(csv.DictReader(open(os.path.join(CORPUS, "alerts.csv"))))
    # affected metric per incident: the alert that fired at the incident's open time on that service.
    alert_by = {(a["service"], a["fired_at"]): a for a in alerts}
    return incidents, deploys, alerts, alert_by


def build():
    incidents, deploys, alerts, alert_by = _load()
    services = sorted({i["service"] for i in incidents})
    current = max(incidents, key=lambda i: i["opened_at"])            # the "happening now" incident
    cur_alert = alert_by.get((current["service"], current["opened_at"]))
    cur_metric = cur_alert["metric"] if cur_alert else "error_rate"
    os.makedirs(OUT, exist_ok=True)

    # --- metrics.json: {service: {metric: [values]}} — baseline everywhere, breaching for the live one.
    metrics = {}
    for s in services:
        metrics[s] = {k: list(v) for k, v in BASELINE.items()}
    live = metrics.setdefault(current["service"], {})
    live[cur_metric] = list(BREACH.get(cur_metric, GENERIC_BREACH))
    json.dump(metrics, open(os.path.join(OUT, "metrics.json"), "w"), indent=1)

    # --- deploys.json: {service: [ {id, at, author, status} ]} — most recent first.
    dep = {}
    for d in sorted(deploys, key=lambda d: d["at"], reverse=True):
        dep.setdefault(d["service"], []).append(
            {"id": d["id"], "at": d["at"], "author": d["author"], "status": d["status"]})
    json.dump(dep, open(os.path.join(OUT, "deploys.json"), "w"), indent=1)

    # --- alerts.json: only the CURRENT incident is firing; get_alerts filters to firing anyway.
    out_alerts = [{"service": current["service"], "name": cur_metric, "severity": current["severity"],
                   "status": "firing", "since": current["opened_at"],
                   "summary": current["summary"]}]
    json.dump(out_alerts, open(os.path.join(OUT, "alerts.json"), "w"), indent=1)

    # --- incidents.json: {service: [ {at, event} ]} chronological timeline per service.
    timeline = {}
    for i in sorted(incidents, key=lambda i: i["opened_at"]):
        s = i["service"]; dep_id = i["summary"].split()[-1]          # e.g. "...after checkout-v67"
        evs = timeline.setdefault(s, [])
        a = alert_by.get((s, i["opened_at"]))
        metric = a["metric"] if a else "error_rate"
        evs.append({"at": i["opened_at"], "event": f"deploy {dep_id} rolled out"})
        evs.append({"at": i["opened_at"], "event": f"{metric} breached threshold ({i['severity']}) — {i['id']} opened"})
        if i["id"] != current["id"]:
            evs.append({"at": i["closed_at"], "event": f"resolved: {i['fix']} — {i['id']} closed"})
        else:
            evs.append({"at": i["opened_at"], "event": f"ONGOING — investigating {i['id']} (alert firing)"})
    json.dump(timeline, open(os.path.join(OUT, "incidents.json"), "w"), indent=1)

    # --- logs.jsonl: a few lines per incident; ERROR lines for the live one so search_logs finds them.
    with open(os.path.join(OUT, "logs.jsonl"), "w") as f:
        for i in incidents:
            lvl = "ERROR" if i["id"] == current["id"] else "WARN"
            f.write(json.dumps({"at": i["opened_at"], "level": lvl, "service": i["service"],
                                "msg": f"{i['summary']} ({i['root_cause']})"}) + "\n")

    print(f"wrote data_v2/ from {len(incidents)} corpus incidents across {len(services)} services")
    print(f"LIVE incident: {current['id']} — {current['service']} {cur_metric} "
          f"{current['severity']} firing since {current['opened_at']}")
    print(f"  cause (in corpus): {current['root_cause']}")
    return current


if __name__ == "__main__":
    build()
