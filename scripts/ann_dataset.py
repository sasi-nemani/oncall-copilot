#!/usr/bin/env python3
"""Build + PERSIST a vector dataset for the ANN experiment, so we never regenerate/re-embed.

Each dataset lands both locally (datasets/<name>/) and in GCS (gs://BUCKET/datasets/<name>/):
  datapoints.json  — Vertex index input {id, embedding}          (what the index builds from)
  vectors.npy      — the raw N x dim matrix                       (to compute EXACT ground-truth recall)
  ids.json         — row -> id, aligned with vectors.npy
  queries.npy      — 100 query vectors sampled from the set       (the recall/latency probe)
  meta.json        — {name, dim, count, n_queries, source}

Two kinds:
  gen-real  <n_incidents> [name]  — generate a big synthetic CORPUS, ingest, embed real chunks
                                    (realistic text/clustering). Regenerates the local corpus; run
                                    `restore` after to return to the 40-incident baseline.
  gen-synth <n_vectors>   [name]  — clustered random vectors (no text). Fast; exercises ANN at scale
                                    without touching the repo corpus.

Run:  python scripts/ann_dataset.py gen-real 1500 real10k
      python scripts/ann_dataset.py gen-synth 50000 synth50k
      python scripts/ann_dataset.py restore
"""
import os
import sys
import json
import glob
import subprocess
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUCKET = os.getenv("VSEARCH_BUCKET", "gs://linkedinpost-agentsalltheway-oncall-vsearch")
DIM = 384
N_QUERIES = 100
rng = np.random.default_rng(0)


def _persist(name, vectors, ids, source):
    vectors = np.asarray(vectors, dtype=np.float32)
    d = os.path.join(ROOT, "datasets", name)
    os.makedirs(d, exist_ok=True)

    # datapoints.json — Vertex input (JSON-lines content, .json extension as Vertex requires).
    with open(os.path.join(d, "datapoints.json"), "w") as f:
        for i, v in zip(ids, vectors):
            f.write(json.dumps({"id": str(i), "embedding": [round(float(x), 7) for x in v]}) + "\n")
    np.save(os.path.join(d, "vectors.npy"), vectors)
    json.dump(list(map(str, ids)), open(os.path.join(d, "ids.json"), "w"))

    # queries: sample N_QUERIES rows as probes (query == an existing point; exact top-k is well-defined).
    qidx = rng.choice(len(vectors), size=min(N_QUERIES, len(vectors)), replace=False)
    np.save(os.path.join(d, "queries.npy"), vectors[qidx])
    json.dump({"name": name, "dim": int(vectors.shape[1]), "count": int(len(vectors)),
               "n_queries": int(len(qidx)), "source": source,
               "gcs_dir": f"{BUCKET}/datasets/{name}/index_input"}, open(os.path.join(d, "meta.json"), "w"))

    # Upload. The index reads ONLY the index_input/ subfolder (just datapoints.json) — the .npy/query
    # artifacts live one level up so Vertex doesn't try to parse them as index data.
    subprocess.run(["gcloud", "storage", "cp", os.path.join(d, "datapoints.json"),
                    f"{BUCKET}/datasets/{name}/index_input/datapoints.json"], check=True)
    for fn in ["vectors.npy", "ids.json", "queries.npy", "meta.json"]:
        subprocess.run(["gcloud", "storage", "cp", os.path.join(d, fn),
                        f"{BUCKET}/datasets/{name}/{fn}"], check=True)
    print(f"[{name}] persisted {len(vectors)} x {vectors.shape[1]} -> datasets/{name}/ and "
          f"{BUCKET}/datasets/{name}/")


def gen_real(n_incidents, name):
    print(f"[{name}] generating a {n_incidents}-incident corpus (regenerates local corpus) ...")
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "generate_corpus.py"),
                    str(n_incidents)], check=True, cwd=ROOT)
    subprocess.run([sys.executable, "-m", "src.ingest"], check=True, cwd=ROOT)
    chunks = [json.loads(l) for l in open(os.path.join(ROOT, "index", "chunks.jsonl"))]
    print(f"[{name}] embedding {len(chunks)} chunks (all-MiniLM-L6-v2) ...")
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("all-MiniLM-L6-v2")
    vecs = model.encode([c["text"] for c in chunks], normalize_embeddings=True, show_progress_bar=True)
    _persist(name, vecs, [c["id"] for c in chunks], source=f"corpus:{n_incidents}-incidents")


def gen_synth(n_vectors, name):
    # Clustered vectors: 1 cluster per ~250 points, so ANN has real neighbourhoods to exploit (uniform
    # random would be a pathological worst case for recall). Normalized -> dot == cosine, like the app.
    print(f"[{name}] generating {n_vectors} clustered {DIM}-dim vectors ...")
    n_clusters = max(2, n_vectors // 250)
    centers = rng.standard_normal((n_clusters, DIM)).astype(np.float32)
    assign = rng.integers(0, n_clusters, size=n_vectors)
    vecs = centers[assign] + 0.35 * rng.standard_normal((n_vectors, DIM)).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    _persist(name, vecs, [f"s{i}" for i in range(n_vectors)], source=f"synthetic:{n_clusters}-clusters")


def restore():
    print("restoring the 40-incident baseline corpus + index ...")
    subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "generate_corpus.py"), "40"],
                   check=True, cwd=ROOT)
    subprocess.run([sys.executable, "-m", "src.ingest"], check=True, cwd=ROOT)
    print("restored.")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "gen-real":
        gen_real(int(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else "real10k")
    elif cmd == "gen-synth":
        gen_synth(int(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else "synth50k")
    elif cmd == "restore":
        restore()
    else:
        print(__doc__)
