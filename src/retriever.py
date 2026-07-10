import os
import glob
import re
import json
from src import config

# Resolve docs/ relative to the repo root (works no matter the cwd).
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(ROOT, "index", "chunks.jsonl")   # output of the ingestion pipeline


def _load_docs_chunks():
    # Chunk by SECTION (## heading), not by blank line. Keeping each section intact means
    # a runbook's "Remediation" travels as one coherent unit instead of being fragmented
    # and crowded out by title-only scraps — the biggest lever for retrieval quality here.
    chunks = []
    # sorted(): glob order is filesystem-dependent, and chunk order breaks ties in keyword
    # ranking — unsorted, the same corpus scores differently on macOS vs Linux (CI caught this).
    for path in sorted(glob.glob(os.path.join(ROOT, "docs", "*.md"))):
        name = os.path.basename(path)
        text = open(path, encoding="utf-8").read()
        title = next((ln.lstrip("# ").strip() for ln in text.splitlines()
                      if ln.startswith("# ")), name)
        # Split on H2 headings; sections[0] is the title/preamble (no content) -> skip it.
        sections = re.split(r"(?m)^##\s+", text)
        for sec in sections[1:]:
            sec = sec.strip()
            if len(sec) > 10:
                chunks.append({"source": name, "text": f"[{title}] {sec}"})
    return chunks


def _load_index_chunks():
    # The ingestion pipeline's output (src/ingest.py): structured + unstructured, already chunked
    # and serialized. Map each to the {source, text} shape the search functions below expect;
    # metadata.source keeps citations resolvable (e.g. corpus/structured/alerts.csv).
    chunks = []
    with open(INDEX, encoding="utf-8") as f:
        for line in f:
            c = json.loads(line)
            chunks.append({"source": c["metadata"].get("source", "index"), "text": c["text"],
                           "service": c["metadata"].get("service")})   # for RETRIEVAL_FILTER=service
    return chunks


def _load_chunks():
    # Which corpus the retriever serves:
    #   RETRIEVAL_SOURCE=index  -> the ingested index (structured + unstructured), if it exists.
    #   default                 -> docs/*.md — zero-setup, and what the eval suite is calibrated
    #                              against. We keep the index OPT-IN so we never silently change
    #                              what a graded system retrieves out from under the benchmark.
    if os.getenv("RETRIEVAL_SOURCE") == "index" and os.path.exists(INDEX):
        return _load_index_chunks()
    return _load_docs_chunks()


CHUNKS = _load_chunks()


# ---------------------------------------------------------------- keyword
def _keyword_ranked(question):
    # Rank chunks by how many query words appear in them. Transparent, zero-dependency.
    # Returns only chunks with a non-zero match (so "no keywords hit" -> empty).
    words = set(re.findall(r"\w+", question.lower()))
    scored = []
    for i, ch in enumerate(CHUNKS):
        hay = ch["text"].lower()
        score = sum(1 for w in words if w in hay)
        if score:
            scored.append((score, i))
    scored.sort(key=lambda x: -x[0])
    return [i for _, i in scored]


# ---------------------------------------------------------------- semantic (local embeddings)
_MODEL = None
_VECS = None
_EMBED_OK = None            # None=unknown, True/False once we've tried to import


def _embeddings_available():
    global _EMBED_OK
    if _EMBED_OK is None:
        try:
            import sentence_transformers  # noqa: F401
            import numpy  # noqa: F401
            _EMBED_OK = True
        except ImportError:
            _EMBED_OK = False
    return _EMBED_OK


def _model():
    global _MODEL
    if _MODEL is None:
        from sentence_transformers import SentenceTransformer
        _MODEL = SentenceTransformer("all-MiniLM-L6-v2")     # small, local, no API/cost
    return _MODEL


def _chunk_vecs():
    global _VECS
    if _VECS is None:
        _VECS = _model().encode([c["text"] for c in CHUNKS], normalize_embeddings=True)
    return _VECS


def _semantic_scores(question):
    # cosine similarity (vectors are normalized) between the question and every chunk.
    qv = _model().encode([question], normalize_embeddings=True)[0]
    return _chunk_vecs() @ qv


# Below this similarity, nothing is even loosely relevant -> treat as "no context".
_SEM_FLOOR = 0.15


# ---------------------------------------------------------------- hybrid (fuse the two)
def _rrf(rank_lists, k0=60):
    # Reciprocal Rank Fusion: a robust, score-free way to merge rankings. A chunk that
    # ranks high in EITHER keyword or semantic bubbles up; agreement bubbles it higher.
    scores = {}
    for rl in rank_lists:
        for rank, idx in enumerate(rl):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (k0 + rank + 1)
    return [i for i, _ in sorted(scores.items(), key=lambda x: -x[1])]


def _select(question, k, mode):
    if mode in ("semantic", "hybrid") and not _embeddings_available():
        mode = "keyword"                                     # graceful fallback, no crash

    if mode == "keyword":
        return _keyword_ranked(question)[:k]

    import numpy as np
    sims = _semantic_scores(question)
    sem_ranked = [int(i) for i in np.argsort(-sims)]

    if mode == "semantic":
        return sem_ranked[:k] if sims[sem_ranked[0]] >= _SEM_FLOOR else []

    # hybrid: fuse keyword + semantic; refuse only if BOTH are weak.
    kw = _keyword_ranked(question)
    if not kw and sims[sem_ranked[0]] < _SEM_FLOOR:
        return []
    return _rrf([kw, sem_ranked])[:k]


_SERVICES = {"payments", "checkout", "auth", "search", "notifications", "cart", "inventory", "shipping"}


def _question_service(question):
    ql = question.lower()
    return next((s for s in _SERVICES if s in ql), None)


def retrieve(question, k=4, mode=None):
    # RETRIEVAL_BACKEND=vertex: delegate to the managed Vertex AI Vector Search backend (its own
    # service restrict + date-range numeric filter). Opt-in and import-on-use, so the local path
    # keeps zero cloud dependencies and behaves exactly as before when the flag is unset.
    if os.getenv("RETRIEVAL_BACKEND") == "vertex":
        from src import vertex_retriever
        return vertex_retriever.retrieve(question, k)
    # mode: keyword | semantic | hybrid (defaults to config.RETRIEVAL_MODE).
    mode = mode or config.RETRIEVAL_MODE
    # RETRIEVAL_FILTER=service: metadata-filtered retrieval. Keep the named service's chunks AND
    # float chunks mentioning the question's date to the top. Plain keyword/semantic ranking can't
    # weight the date (its tokens are common), so near-duplicate same-service incidents get confused
    # (the failure mode Run 3 exposed). This is the local stand-in for a vector DB's metadata
    # filter (Vertex: filter by service, range by date).
    if os.getenv("RETRIEVAL_FILTER") == "service":
        pool = _select(question, max(k * 6, 30), mode)              # wide pool to filter + re-rank
        svc = _question_service(question)
        if svc:
            pool = [i for i in pool if CHUNKS[i].get("service") == svc] or pool
        m = re.search(r"\d{4}-\d{2}-\d{2}", question)               # a date in the question?
        if m:
            d = m.group()
            pool.sort(key=lambda i: 0 if d in CHUNKS[i]["text"] else 1)   # stable: dated chunks first
        idxs = pool[:k]
    else:
        idxs = _select(question, k, mode)
    if not idxs:
        return ""            # empty context -> the system prompt tells the model to refuse
    return "\n\n".join(f"[{CHUNKS[i]['source']}]\n{CHUNKS[i]['text']}" for i in idxs)
