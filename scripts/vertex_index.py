#!/usr/bin/env python3
"""Stand up (and tear down) a Vertex AI Vector Search index for the on-call corpus.

Three managed resources, created in order — and this is where the money is:
  1. INDEX          — the ANN structure built from our uploaded vectors (batch build, ~cheap).
  2. INDEX ENDPOINT — the serving surface.
  3. DEPLOYED INDEX — the index placed on a dedicated node behind the endpoint. THIS bills per hour,
                      so `down` must undeploy it. `up` does all three; `down` reverses them.

State (resource names) is written to index/vertex_resources.json after EACH step, so a teardown
always has something to act on even if a later step failed or the process died mid-way.

Usage:  python scripts/vertex_index.py up      # create index -> endpoint -> deploy  (long: ~40-60 min)
        python scripts/vertex_index.py down    # undeploy -> delete endpoint -> delete index
        python scripts/vertex_index.py status  # print current state
"""
import os
import sys
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT = os.getenv("GCP_PROJECT", "linkedinpost-agentsalltheway")
REGION = os.getenv("GCP_REGION", "us-central1")
STATE = os.path.join(ROOT, "index", "vertex_resources.json")
META = os.path.join(ROOT, "index", "vertex_meta.json")
DISPLAY = "oncall-vsearch"
DEPLOYED_ID = "oncall_vs_1"


def _state():
    return json.load(open(STATE)) if os.path.exists(STATE) else {}


def _save(**kw):
    s = _state()
    s.update(kw)
    json.dump(s, open(STATE, "w"), indent=2)
    return s


def _init():
    from google.cloud import aiplatform
    aiplatform.init(project=PROJECT, location=REGION)
    return aiplatform


def up():
    ai = _init()
    meta = json.load(open(META))
    s = _state()

    # 1. INDEX — tree-AH ANN over our 384-dim normalized vectors; dot-product == cosine here.
    if not s.get("index"):
        print("[1/3] creating index (tree-AH) — this is the long step, ~30-50 min ...", flush=True)
        index = ai.MatchingEngineIndex.create_tree_ah_index(
            display_name=DISPLAY,
            contents_delta_uri=meta["gcs_dir"],
            dimensions=meta["dimensions"],
            approximate_neighbors_count=50,
            distance_measure_type="DOT_PRODUCT_DISTANCE",
            index_update_method="BATCH_UPDATE",
        )
        s = _save(index=index.resource_name)
        print("      index:", index.resource_name, flush=True)
    else:
        index = ai.MatchingEngineIndex(s["index"])
        print("[1/3] index exists:", s["index"], flush=True)

    # 2. INDEX ENDPOINT — public serving surface (simplest; no VPC peering needed).
    if not s.get("endpoint"):
        print("[2/3] creating index endpoint ...", flush=True)
        ep = ai.MatchingEngineIndexEndpoint.create(
            display_name=DISPLAY + "-ep", public_endpoint_enabled=True)
        s = _save(endpoint=ep.resource_name)
        print("      endpoint:", ep.resource_name, flush=True)
    else:
        ep = ai.MatchingEngineIndexEndpoint(s["endpoint"])
        print("[2/3] endpoint exists:", s["endpoint"], flush=True)

    # 3. DEPLOY — put the index on the SMALLEST single node (this is what bills hourly).
    if not s.get("deployed"):
        print("[3/3] deploying index to endpoint — ~20-30 min; billing starts when it goes live ...", flush=True)
        ep.deploy_index(index=index, deployed_index_id=DEPLOYED_ID,
                        min_replica_count=1, max_replica_count=1, machine_type="e2-standard-2")
        host = ep.public_endpoint_domain_name
        s = _save(deployed=DEPLOYED_ID, public_domain=host)
        print("      deployed:", DEPLOYED_ID, "| host:", host, flush=True)
    else:
        print("[3/3] already deployed:", s["deployed"], flush=True)

    print("\nUP complete. Endpoint is LIVE and billing. Run the eval, then `down` to stop charges.")
    print(json.dumps(_state(), indent=2))


def down():
    ai = _init()
    s = _state()
    if s.get("endpoint") and s.get("deployed"):
        try:
            print("undeploying index (stops hourly billing) ...", flush=True)
            ep = ai.MatchingEngineIndexEndpoint(s["endpoint"])
            ep.undeploy_index(deployed_index_id=s["deployed"])
            s = _save(deployed=None)
        except Exception as e:
            print("  undeploy warning:", e, flush=True)
    if s.get("endpoint"):
        try:
            print("deleting endpoint ...", flush=True)
            ai.MatchingEngineIndexEndpoint(s["endpoint"]).delete(force=True)
            s = _save(endpoint=None)
        except Exception as e:
            print("  endpoint delete warning:", e, flush=True)
    if s.get("index"):
        try:
            print("deleting index ...", flush=True)
            ai.MatchingEngineIndex(s["index"]).delete()
            s = _save(index=None)
        except Exception as e:
            print("  index delete warning:", e, flush=True)
    print("DOWN complete. Remaining state:", json.dumps(_state()))
    print("(The GCS bucket with the vectors is left intact — that's the durable store.)")


def status():
    print(json.dumps(_state(), indent=2))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"up": up, "down": down, "status": status}.get(cmd, status)()
