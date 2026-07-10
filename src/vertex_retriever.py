"""Query side of the Vertex AI Vector Search backend.

This is the managed counterpart to src/retriever.py's local search. When RETRIEVAL_BACKEND=vertex,
retriever.retrieve() delegates here. The flow:
  1. embed the question with the SAME local model used to build the index (all-MiniLM-L6-v2, 384-dim);
  2. pull `service` and a date out of the question;
  3. ask the deployed index for nearest neighbours, but constrained by:
       - a `service` RESTRICT   (categorical) — search only that service's chunks, and
       - a `date` NUMERIC RANGE (>= d-window, <= d+window) — the filter our local heuristic couldn't do;
  4. map the returned datapoint ids back to chunk text/source and format the context.

The date-range restrict is the whole point: the 3 incidents Run 4 still confused are same-service and
close in date, so narrowing the ANN search to a date WINDOW around the question is what disambiguates
them — done inside the store, not by re-ranking after the fact.
"""
import os
import re
import json
import datetime
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, "index", "vertex_resources.json")
INDEX = os.path.join(ROOT, "index", "chunks.jsonl")
DATE_WINDOW_DAYS = int(os.getenv("VSEARCH_DATE_WINDOW", "7"))   # +/- days around the question's date
_SERVICES = {"payments", "checkout", "auth", "search", "notifications", "cart", "inventory", "shipping"}
_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

_EP = _MODEL = _CHUNKS = _STATE = None
_LOCK = threading.Lock()          # eval runs concurrent workers -> guard first-call init (model load)


def _load():
    global _EP, _MODEL, _CHUNKS, _STATE
    if _EP is not None:
        return
    with _LOCK:
        if _EP is not None:       # another thread finished init while we waited
            return
        _init_locked()


def _init_locked():
    global _EP, _MODEL, _CHUNKS, _STATE
    _STATE = json.load(open(STATE))
    from google.cloud import aiplatform
    aiplatform.init(project=os.getenv("GCP_PROJECT", "linkedinpost-agentsalltheway"),
                    location=os.getenv("GCP_REGION", "us-central1"))
    ep = aiplatform.MatchingEngineIndexEndpoint(_STATE["endpoint"])
    from sentence_transformers import SentenceTransformer
    _MODEL = SentenceTransformer("all-MiniLM-L6-v2")
    _CHUNKS = {json.loads(l)["id"]: json.loads(l) for l in open(INDEX)}   # id -> chunk
    _EP = ep                      # assign LAST: the lock-free fast path keys off _EP being set


def _question_service(q):
    ql = q.lower()
    return next((s for s in _SERVICES if s in ql), None)


def _date_window(q):
    m = _DATE.search(q)
    if not m:
        return None
    d = datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    lo = d - datetime.timedelta(days=DATE_WINDOW_DAYS)
    hi = d + datetime.timedelta(days=DATE_WINDOW_DAYS)
    return int(lo.strftime("%Y%m%d")), int(hi.strftime("%Y%m%d"))


def retrieve(question, k=4):
    _load()
    from google.cloud.aiplatform.matching_engine.matching_engine_index_endpoint import (
        Namespace, NumericNamespace)

    qv = _MODEL.encode([question], normalize_embeddings=True)[0].tolist()

    filt = []
    svc = _question_service(question)
    if svc:
        filt.append(Namespace("service", [svc], []))               # allow=[svc], deny=[]

    numeric = []
    win = _date_window(question)
    if win:
        lo, hi = win
        numeric.append(NumericNamespace(name="date", value_int=lo, op="GREATER_EQUAL"))
        numeric.append(NumericNamespace(name="date", value_int=hi, op="LESS_EQUAL"))

    resp = _EP.find_neighbors(deployed_index_id=_STATE["deployed"], queries=[qv],
                              num_neighbors=k, filter=filt, numeric_filter=numeric)
    neighbors = resp[0] if resp else []
    # A too-tight date window can starve the query; fall back to service-only, then unfiltered.
    if not neighbors and numeric:
        resp = _EP.find_neighbors(deployed_index_id=_STATE["deployed"], queries=[qv],
                                  num_neighbors=k, filter=filt)
        neighbors = resp[0] if resp else []

    out = []
    for n in neighbors:
        c = _CHUNKS.get(n.id)
        if c:
            out.append(f"[{c['metadata'].get('source', 'index')}]\n{c['text']}")
    return "\n\n".join(out)
