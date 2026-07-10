# On-Call Copilot — app image. Runs the live visualizer by default (the web entrypoint for
# Cloud Run in Phase B); CI overrides the command to run the eval inside the same image.
FROM python:3.12-slim

WORKDIR /app

# 1) Dependencies FIRST, on their own layer. Deps change rarely, so Docker caches this layer
#    and a code-only change rebuilds in seconds instead of re-installing everything.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2) Then the application code (changes often -> its own cheap layer).
COPY src ./src
COPY evals ./evals
COPY data ./data
COPY mcp_server ./mcp_server
COPY viz ./viz
COPY scripts ./scripts
COPY guardrails.json models.json app.py trace_demo.py ./

# Materialize corpus + index + eval set inside the image, all from ONE deterministic build
# (seeded, fixed base date). Regenerating the eval set here — not just copying the committed one —
# guarantees the questions can never drift from the corpus they're built against.
RUN python scripts/generate_corpus.py 40 && python -m src.ingest && python scripts/generate_evalset.py

# 3) Run as a non-root user — least privilege: a compromised container isn't root.
RUN useradd --create-home appuser && chown -R appuser /app
USER appuser

# 4) Cloud Run sets $PORT; VIZ_HOST=0.0.0.0 makes the server reachable from outside the container.
ENV PORT=8080 VIZ_HOST=0.0.0.0 PYTHONUNBUFFERED=1
EXPOSE 8080

CMD ["python", "viz/server.py"]
