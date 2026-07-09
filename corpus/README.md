# Corpus — structured + unstructured incident data

This folder is **generated**, not committed (the data itself is gitignored; this README isn't).
Regenerate it any time:

```bash
python scripts/generate_corpus.py 40     # 40 incidents (default); pass any N
```

A handful of **incidents** are the single source of truth; every artifact below is rendered from
them, so the corpus is internally coherent — a chat that names deploy `checkout-v93` refers to a
deploy that really exists in `deploys.csv`, with a matching alert and metric series.

## `structured/` — schema-aware records
| File | Format | Rows | Fields |
|---|---|---|---|
| `incidents.json` | JSON | one per incident | id, service, severity, opened_at, closed_at, summary, root_cause, fix |
| `deploys.csv` | CSV | incident deploys + clean noise | id, service, at, author, status |
| `alerts.csv` | CSV | one per incident | id, service, metric, severity, state, fired_at, resolved_at |
| `metrics.jsonl` | JSONL | 6 samples per incident | service, metric, at, value (normal → spike → recover) |

## `unstructured/` — prose
| Folder | Format | One per incident |
|---|---|---|
| `chats/` | `.txt` | a Slack-style incident-channel thread |
| `emails/` | `.eml` | the incident notification email |
| `postmortems/` | `.md` | the postmortem writeup |

## Why both modalities
The JD asks for "pipelines for structured **and** unstructured data." `src/ingest.py` reads this
folder and unifies both into one retrievable index (`index/chunks.jsonl`): prose is **chunked**,
structured records are **serialized into natural-language lines** (so semantic search can match a
CSV row), each with `{source, type, incident_id}` metadata.
