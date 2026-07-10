#!/usr/bin/env python3
"""Turn our local chunk index into Vertex AI Vector Search's input format, and upload it.

WHAT VERTEX WANTS: a JSONL file where each line is one datapoint —
    {"id", "embedding": [384 floats], "restricts": [...], "numeric_restricts": [...]}
  - `embedding`         : the vector. We reuse the SAME local model the app already uses
                          (all-MiniLM-L6-v2, 384-dim, normalized) so retrieval quality is
                          comparable to the local backend — we're swapping the STORE, not the model.
  - `restricts`         : categorical metadata filters. We tag each point with its `service`, so a
                          query can say "only search checkout chunks" — the managed equivalent of our
                          hand-rolled RETRIEVAL_FILTER=service.
  - `numeric_restricts` : NUMERIC filters. We tag each point with its incident date as an int
                          (YYYYMMDD), which unlocks the thing the local heuristic could NOT do — a
                          true DATE-RANGE filter ("service=checkout AND date within a week of the 26th").
                          That range filter is precisely what the 3 remaining Run-4 failures need.

Run:  python scripts/build_vertex_index_data.py            # writes local + uploads to the bucket
Env:  VSEARCH_BUCKET  (gs:// bucket for the index input; default below)
"""
import os
import re
import json
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "index", "chunks.jsonl")
OUT = os.path.join(ROOT, "index", "vertex_datapoints.jsonl")
BUCKET = os.getenv("VSEARCH_BUCKET", "gs://linkedinpost-agentsalltheway-oncall-vsearch")
GCS_DIR = "vectors"                                          # index reads this FOLDER, not the file
_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def _incident_dates():
    # incident_id -> opened date as int YYYYMMDD, so unstructured chunks (which carry an incident_id
    # but may not spell the date in-text) are still date-filterable.
    path = os.path.join(ROOT, "corpus", "structured", "incidents.json")
    if not os.path.exists(path):
        return {}
    out = {}
    for r in json.load(open(path)):
        m = _DATE.search(r.get("opened_at", ""))
        if m:
            out[r["id"]] = int(m.group(1) + m.group(2) + m.group(3))
    return out


def _date_int(chunk, inc_dates):
    m = _DATE.search(chunk["text"])                         # a date printed in the chunk itself?
    if m:
        return int(m.group(1) + m.group(2) + m.group(3))
    inc = chunk["metadata"].get("incident_id")              # else fall back to its incident's date
    return inc_dates.get(inc)


def build():
    # Read the ingested index directly — it carries full metadata (service, incident_id) that the
    # app's flattened in-memory CHUNKS drops. The datapoint id IS the chunk's content-hash id, so the
    # Vertex retriever can map a neighbour id straight back to its chunk text/source at query time.
    chunks = [json.loads(line) for line in open(INDEX)]
    texts = [c["text"] for c in chunks]

    # Embed with the SAME local model the app uses; normalized so dot-product == cosine.
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("all-MiniLM-L6-v2")
    vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=True)

    inc_dates = _incident_dates()
    n_svc = n_date = 0
    with open(OUT, "w") as f:
        for c, v in zip(chunks, vecs):
            dp = {"id": c["id"], "embedding": [round(float(x), 7) for x in v]}
            svc = c["metadata"].get("service")
            if svc:
                dp["restricts"] = [{"namespace": "service", "allow": [svc]}]
                n_svc += 1
            d = _date_int(c, inc_dates)
            if d:
                dp["numeric_restricts"] = [{"namespace": "date", "value_int": d}]
                n_date += 1
            f.write(json.dumps(dp) + "\n")

    dim = len(vecs[0])
    print(f"wrote {len(chunks)} datapoints (dim={dim}) -> {os.path.relpath(OUT, ROOT)}")
    print(f"  {n_svc} tagged with a service restrict · {n_date} tagged with a date numeric_restrict")

    # Upload to the FOLDER the index will read. Vertex ingests every file under gs://bucket/vectors/.
    # NOTE: the file must end in .json / .csv / .avro — Vertex rejects a .jsonl extension outright
    # (even though the CONTENT is JSON-lines). So it uploads as .json.
    dest = f"{BUCKET}/{GCS_DIR}/vertex_datapoints.json"
    subprocess.run(["gcloud", "storage", "cp", OUT, dest], check=True)
    print(f"uploaded -> {dest}")
    # Save the dimension for the index-create step (avoids hardcoding it in two places).
    with open(os.path.join(ROOT, "index", "vertex_meta.json"), "w") as f:
        json.dump({"dimensions": dim, "gcs_dir": f"{BUCKET}/{GCS_DIR}", "count": len(chunks)}, f)
    return dim


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ROOT)
    build()
