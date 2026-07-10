#!/usr/bin/env python3
"""The ANN experiment: build a REAL tree-AH (approximate) index on Vertex, and measure what
approximation actually costs — recall vs an exact scan — and its latency, at a scale where ANN matters.

Reads a persisted dataset (scripts/ann_dataset.py) from GCS, so index builds are cheap to repeat and
the data is never lost. Per-dataset resource state -> datasets/<name>/resources.json.

Lifecycle (granular so serving cost is minimal — only `deploy`..`down` bills):
  build   <name>   create the tree-AH index from gs://.../datasets/<name>/index_input  (long, ~free)
  deploy  <name>   create endpoint + deploy the index                                  (bills hourly)
  measure <name>   ANN recall@k vs exact numpy + query latency
  down    <name>   undeploy + delete index + delete endpoint  (GCS dataset is kept)

Run:  python scripts/ann_experiment.py build real10k
"""
import os
import sys
import json
import time
import subprocess
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUCKET = os.getenv("VSEARCH_BUCKET", "gs://linkedinpost-agentsalltheway-oncall-vsearch")
PROJECT = os.getenv("GCP_PROJECT", "linkedinpost-agentsalltheway")
REGION = os.getenv("GCP_REGION", "us-central1")
K = 10


def _dir(name):
    d = os.path.join(ROOT, "datasets", name)
    os.makedirs(d, exist_ok=True)
    return d


def _meta(name):
    p = os.path.join(_dir(name), "meta.json")
    if not os.path.exists(p):                       # pull from GCS if we only have the cloud copy
        subprocess.run(["gcloud", "storage", "cp", f"{BUCKET}/datasets/{name}/meta.json", p], check=True)
    return json.load(open(p))


def _state(name):
    p = os.path.join(_dir(name), "resources.json")
    return json.load(open(p)) if os.path.exists(p) else {}


def _save(name, **kw):
    s = _state(name); s.update(kw)
    json.dump(s, open(os.path.join(_dir(name), "resources.json"), "w"), indent=2)
    return s


def _ai():
    from google.cloud import aiplatform
    aiplatform.init(project=PROJECT, location=REGION)
    return aiplatform


def build(name):
    ai = _ai(); m = _meta(name); s = _state(name)
    if s.get("index"):
        print("index exists:", s["index"]); return
    print(f"[{name}] building tree-AH index over {m['count']} x {m['dim']} vectors "
          f"(the REAL approximate ANN) — ~30-50 min ...", flush=True)
    idx = ai.MatchingEngineIndex.create_tree_ah_index(
        display_name=f"ann-{name}",
        contents_delta_uri=m["gcs_dir"],
        dimensions=m["dim"],
        approximate_neighbors_count=150,
        distance_measure_type="DOT_PRODUCT_DISTANCE",
        shard_size="SHARD_SIZE_SMALL",              # <1GB data -> smallest shard -> e2-standard-2
        index_update_method="BATCH_UPDATE",
    )
    _save(name, index=idx.resource_name)
    print("  index:", idx.resource_name, flush=True)


def deploy(name):
    ai = _ai(); s = _state(name)
    idx = ai.MatchingEngineIndex(s["index"])
    if not s.get("endpoint"):
        ep = ai.MatchingEngineIndexEndpoint.create(display_name=f"ann-{name}-ep", public_endpoint_enabled=True)
        s = _save(name, endpoint=ep.resource_name)
    else:
        ep = ai.MatchingEngineIndexEndpoint(s["endpoint"])
    if not s.get("deployed"):
        did = name.replace("-", "_")
        print(f"[{name}] deploying (bills hourly on e2-standard-2) — ~20-30 min ...", flush=True)
        ep.deploy_index(index=idx, deployed_index_id=did, min_replica_count=1, max_replica_count=1,
                        machine_type="e2-standard-2")
        _save(name, deployed=did, public_domain=ep.public_endpoint_domain_name)
    print("deployed:", _state(name), flush=True)


def measure(name):
    ai = _ai(); s = _state(name); d = _dir(name)
    for fn in ["vectors.npy", "ids.json", "queries.npy"]:
        if not os.path.exists(os.path.join(d, fn)):
            subprocess.run(["gcloud", "storage", "cp", f"{BUCKET}/datasets/{name}/{fn}", os.path.join(d, fn)], check=True)
    vectors = np.load(os.path.join(d, "vectors.npy"))
    ids = json.load(open(os.path.join(d, "ids.json")))
    queries = np.load(os.path.join(d, "queries.npy"))

    # EXACT ground truth (numpy) + brute-force latency, on the same box.
    bf_ms = []
    exact = []
    for q in queries:
        t0 = time.perf_counter()
        sims = vectors @ q
        top = np.argpartition(-sims, K)[:K]
        top = top[np.argsort(-sims[top])]
        bf_ms.append((time.perf_counter() - t0) * 1000)
        exact.append({ids[i] for i in top})

    # ANN (tree-AH) neighbours via the deployed endpoint.
    ep = ai.MatchingEngineIndexEndpoint(s["endpoint"])
    ann_ms, recalls = [], []
    for q, ex in zip(queries, exact):
        t0 = time.perf_counter()
        resp = ep.find_neighbors(deployed_index_id=s["deployed"], queries=[q.tolist()], num_neighbors=K)
        ann_ms.append((time.perf_counter() - t0) * 1000)
        got = {n.id for n in (resp[0] if resp else [])}
        recalls.append(len(got & ex) / K)

    m = _meta(name)
    print(f"\n=== ANN experiment: {name}  ({m['count']:,} x {m['dim']} vectors, {len(queries)} queries, top-{K}) ===")
    print(f"recall@{K} (ANN vs exact) : {np.mean(recalls) * 100:.1f}%   (1.0 = found all true neighbours)")
    print(f"brute-force latency       : {np.median(bf_ms):.2f} ms/query (numpy, this box)")
    print(f"ANN latency (incl network): {np.median(ann_ms):.1f} ms/query (round-trip to Vertex)")
    rec = {"name": name, "count": m["count"], "dim": m["dim"], "k": K,
           "recall": round(float(np.mean(recalls)), 4),
           "bf_ms": round(float(np.median(bf_ms)), 3), "ann_ms": round(float(np.median(ann_ms)), 1)}
    json.dump(rec, open(os.path.join(d, "result.json"), "w"), indent=2)
    print("saved ->", os.path.relpath(os.path.join(d, "result.json"), ROOT))


def down(name):
    ai = _ai(); s = _state(name)
    if s.get("endpoint") and s.get("deployed"):
        try:
            print("undeploying (stops billing) ...", flush=True)
            ai.MatchingEngineIndexEndpoint(s["endpoint"]).undeploy_index(deployed_index_id=s["deployed"])
            s = _save(name, deployed=None)
        except Exception as e:
            print("  undeploy warning:", e)
    if s.get("endpoint"):
        try:
            ai.MatchingEngineIndexEndpoint(s["endpoint"]).delete(force=True); s = _save(name, endpoint=None)
        except Exception as e:
            print("  endpoint delete warning:", e)
    if s.get("index"):
        try:
            ai.MatchingEngineIndex(s["index"]).delete(); s = _save(name, index=None)
        except Exception as e:
            print("  index delete warning:", e)
    print("down complete:", _state(name), "(GCS dataset kept)")


if __name__ == "__main__":
    cmd, name = sys.argv[1], sys.argv[2]
    {"build": build, "deploy": deploy, "measure": measure, "down": down}[cmd](name)
