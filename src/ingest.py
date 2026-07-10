"""Ingestion pipeline — unify STRUCTURED + UNSTRUCTURED corpus into one retrievable index.

The JD names "pipelines for structured and unstructured data". The two need DIFFERENT handling:
  - UNSTRUCTURED (chats/emails/postmortems): prose -> CHUNKED into passages.
  - STRUCTURED (JSON/CSV/JSONL records): schema-aware SERIALIZATION -> each record rendered as a
    natural-language line so semantic search can match a CSV row, WITH its fields kept as metadata.

Both land in index/chunks.jsonl as {id, text, metadata}. IDs are CONTENT HASHES => idempotent:
re-ingest the same corpus and you get an identical index (stable for evals).

Run:  python -m src.ingest        # reads ./corpus, writes ./index/chunks.jsonl
"""
import os
import csv
import glob
import json
import hashlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(ROOT, "corpus")
INDEX = os.path.join(ROOT, "index", "chunks.jsonl")


def _id(text):
    return hashlib.sha1(text.encode()).hexdigest()[:12]     # content hash -> same text, same id


def _incident_id(path):
    base = os.path.basename(path)                            # files are INC-123.ext
    return base.split(".")[0] if base.startswith("INC-") else None


def chunk(text, size=600):
    """Pack paragraphs into ~size-char chunks (splits on blank lines). Fine for short docs; a
    production pipeline would chunk by semantic section or token count."""
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    out, cur = [], ""
    for p in paras:
        if cur and len(cur) + len(p) > size:
            out.append(cur)
            cur = p
        else:
            cur = (cur + "\n\n" + p).strip()
    if cur:
        out.append(cur)
    return out


def _service_by_incident():
    # incident_id -> service, from the structured source of truth. Lets us tag UNSTRUCTURED chunks
    # (chats/emails/postmortems, named INC-xxx) with a service too, so retrieval can filter by it.
    path = os.path.join(CORPUS, "structured", "incidents.json")
    return {r["id"]: r["service"] for r in json.load(open(path))} if os.path.exists(path) else {}


def ingest_unstructured():
    """Chunk every prose file under corpus/unstructured/ and tag it with source + service metadata."""
    svc = _service_by_incident()
    out = []
    for path in sorted(glob.glob(os.path.join(CORPUS, "unstructured", "**", "*"), recursive=True)):
        if not os.path.isfile(path):
            continue
        doc_type = os.path.basename(os.path.dirname(path))   # chats / emails / postmortems
        inc = _incident_id(path)
        for c in chunk(open(path).read()):
            out.append({"id": _id(c), "text": c, "metadata": {
                "source": os.path.relpath(path, ROOT), "type": doc_type, "modality": "unstructured",
                "incident_id": inc, "service": svc.get(inc)}})
    return out


def _serialize_structured():
    """The schema-aware step: each structured record -> one natural-language line + field metadata."""
    d = os.path.join(CORPUS, "structured")
    out = []

    for r in json.load(open(os.path.join(d, "incidents.json"))):
        out.append((f"Incident {r['id']} — {r['service']} ({r['severity']}): {r['summary']}. "
                    f"Root cause: {r['root_cause']}. Fix: {r['fix']}. "
                    f"Opened {r['opened_at']}, resolved {r['closed_at']}.",
                    {"source": "corpus/structured/incidents.json", "type": "incident",
                     "service": r["service"], "incident_id": r["id"]}))

    for r in csv.DictReader(open(os.path.join(d, "deploys.csv"))):
        out.append((f"Deploy {r['id']} to {r['service']} at {r['at']} by {r['author']} — status {r['status']}.",
                    {"source": "corpus/structured/deploys.csv", "type": "deploy",
                     "service": r["service"], "deploy_id": r["id"]}))

    for r in csv.DictReader(open(os.path.join(d, "alerts.csv"))):
        out.append((f"Alert {r['id']}: {r['service']} {r['metric']} {r['severity']}, {r['state']}. "
                    f"Fired {r['fired_at']}, resolved {r['resolved_at']}.",
                    {"source": "corpus/structured/alerts.csv", "type": "alert",
                     "service": r["service"], "alert_id": r["id"]}))

    # metrics.jsonl: aggregate per (service, metric) — raw per-sample rows are too granular to index.
    groups = {}
    for line in open(os.path.join(d, "metrics.jsonl")):
        m = json.loads(line)
        groups.setdefault((m["service"], m["metric"]), []).append(m["value"])
    for (svc, met), vals in groups.items():
        out.append((f"Metric series — {svc} {met}: {len(vals)} samples, "
                    f"baseline ~{min(vals)}, peak ~{max(vals)}.",
                    {"source": "corpus/structured/metrics.jsonl", "type": "metric",
                     "service": svc, "metric": met}))
    return out


def ingest_structured():
    return [{"id": _id(line), "text": line, "metadata": {**meta, "modality": "structured"}}
            for line, meta in _serialize_structured()]


def run():
    chunks = ingest_unstructured() + ingest_structured()
    os.makedirs(os.path.dirname(INDEX), exist_ok=True)
    with open(INDEX, "w") as f:
        for c in chunks:
            f.write(json.dumps(c) + "\n")
    u = sum(1 for c in chunks if c["metadata"]["modality"] == "unstructured")
    print(f"ingested -> {os.path.relpath(INDEX, ROOT)}")
    print(f"  {len(chunks)} chunks:  {u} unstructured (chunked prose)  +  {len(chunks) - u} "
          f"structured (serialized records)")
    return chunks


if __name__ == "__main__":
    run()
