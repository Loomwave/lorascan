"""`lorascan serve`: a tiny local web page for a running survey — the report re-rendered from the
database on every request (cached for refresh_s), plus /status.json. Stdlib only."""
from __future__ import annotations
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from .store.db import Store
from .report.html import render_report


def make_server(db_path: str, host: str = "0.0.0.0", port: int = 8080, refresh_s: int = 60, rssi_offset_db: float = 0.0):
    cache = {"html": "", "ts": 0.0}
    lock = threading.Lock()

    def page() -> str:
        with lock:
            if time.time() - cache["ts"] > refresh_s or not cache["html"]:
                store = Store(db_path)
                try:
                    html = render_report(store, "/dev/null", title="lorascan live", rssi_offset_db=rssi_offset_db)
                finally:
                    store.close()
                html = html.replace("<head>", f'<head><meta http-equiv="refresh" content="{refresh_s}">', 1)
                cache["html"], cache["ts"] = html, time.time()
            return cache["html"]

    def status() -> str:
        store = Store(db_path)
        try:
            out = []
            for r in store.runs():
                c = store.counts(r["id"])
                out.append({"id": r["id"], "kind": r["kind"], "profile": r["profile"], "note": r["note"], "first_ts": r["first_ts"], "last_ts": r["last_ts"],
                            "energy": c["energy"][0], "cad": c["cad"][0], "decode": c["decode"][0], "events": store.event_counts(r["id"])})
            return json.dumps({"db": db_path, "runs": out, "now": time.time()}, indent=1)
        finally:
            store.close()

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/status.json"):
                body, ctype = status().encode(), "application/json"
            elif self.path == "/" or self.path.startswith("/index"):
                body, ctype = page().encode(), "text/html; charset=utf-8"
            else:
                self.send_response(404); self.end_headers(); return
            self.send_response(200)
            self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)

        def log_message(self, *a):   # quiet
            pass

    return ThreadingHTTPServer((host, port), H)
