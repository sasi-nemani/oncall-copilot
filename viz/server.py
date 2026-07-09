"""Live agent-run visualizer — a tiny, dependency-free web server.

Run:  python -m viz.server   (then open http://localhost:8000)

Serves a single page and streams the agent's trace as Server-Sent Events (SSE)
so the browser can render the whole observe->act->observe flow in real time.
Stdlib only — no Flask, no JS frameworks.
"""
import os
import sys
import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Make "from src import ..." work when launched directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import llm, agent, agents, trace, config  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
# Honor Cloud Run's standard $PORT first, then VIZ_PORT, then 8000 for local dev.
PORT = int(os.getenv("PORT", os.getenv("VIZ_PORT", "8000")))
# Bind localhost by default (safe for local dev); a container/Cloud Run sets VIZ_HOST=0.0.0.0
# so the server is reachable from outside the container.
HOST = os.getenv("VIZ_HOST", "127.0.0.1")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):          # keep the console quiet
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self._serve_file("index.html", "text/html; charset=utf-8")
        elif parsed.path == "/run":
            self._run_sse(urllib.parse.parse_qs(parsed.query))
        else:
            self.send_error(404)

    def _serve_file(self, name, ctype):
        with open(os.path.join(HERE, name), "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sse(self, obj):
        # One SSE message per trace event; flush so the browser sees it instantly.
        self.wfile.write(f"data: {json.dumps(obj)}\n\n".encode("utf-8"))
        self.wfile.flush()

    def _run_sse(self, qs):
        question = (qs.get("q") or [""])[0].strip()
        provider = (qs.get("provider") or [config.PROVIDER])[0]
        mode = (qs.get("mode") or ["single"])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if not question:
            self._sse({"type": "error", "message": "Please enter a question."})
            self._sse({"type": "done"})
            return
        try:
            client = llm.get_client(provider)
            run_id = trace.new_run_id()
            self._sse({"type": "meta", "provider": provider,
                       "model": getattr(client, "model", "?"), "mode": mode, "run_id": run_id})
            # tee: stream to the browser AND log every event to logs/run-<id>.jsonl
            logger = trace.file_logger(run_id, question, provider, mode)
            sink = trace.tee(self._sse, logger)
            try:
                if mode == "multi":
                    agents.run(question, client, on_event=sink)
                else:
                    agent.answer(question, client, on_event=sink)
            finally:
                logger.close()
        except Exception as e:                              # stream the error, don't 500
            self._sse({"type": "error", "message": f"{type(e).__name__}: {e}"})
        self._sse({"type": "done"})


def main():
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"On-Call Copilot — live visualizer on http://{HOST}:{PORT}  "
          f"(default provider={config.PROVIDER}; Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
