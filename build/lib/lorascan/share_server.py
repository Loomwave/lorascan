"""Reference share endpoint (spec: docs/superpowers/specs/2026-09-14-lorascan-share-endpoint.md).

Stdlib only: ThreadingHTTPServer + SQLite. One process, one database file. Routes:
  GET  /healthz                 200 "ok" (liveness/readiness)
  GET  /v1/watermark            header X-Lorascan-Submitter -> {"latest": {"hour": b|null, "day": b|null}}
  POST /v1/share                gzip JSON lorascan-share/2 -> {"accepted": n, "latest": b, "flagged": bool}
  GET  /v1/stats                counts per table, submitters, flagged rows
  GET  /                        the community map page (inline SVG, no JavaScript, no CDN)
  GET  /v1/map.json             the fleet aggregates behind that page (cells, band, when, submitters)
Idempotent: energy upserts on (submitter, freq_hz, bw_hz, bucket_s, bucket), cad on (submitter, freq_hz,
bw_hz, sf), decode on (submitter, freq_hz, network, preset); a re-send never double-counts. Documents
whose floor is above -70 dBm on > 90 % of channels are stored with flagged=1 (never merged into the map).
Run: lorascan-share-server --db /data/share.sqlite --port 8081   (behind TLS at share.lorascan.app)."""
from __future__ import annotations
import argparse
import gzip
import json
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FORMAT = "lorascan-share/2"
ENERGY_COLS = ("n_rows", "n_samples", "hours", "floor_p10_med", "p90_med", "peak_max", "busy_mean")
SCHEMA = """
CREATE TABLE IF NOT EXISTS submitters (submitter TEXT PRIMARY KEY, first_seen REAL, last_seen REAL, uploads INTEGER DEFAULT 0, tool TEXT, board TEXT, calibration TEXT, cell_lat REAL, cell_lon REAL, cell_size REAL);
CREATE TABLE IF NOT EXISTS energy (submitter TEXT, freq_hz INTEGER, bw_hz INTEGER, bucket_s INTEGER, bucket TEXT, n_rows INTEGER, n_samples INTEGER, hours REAL,
  floor_p10_med REAL, p90_med REAL, peak_max REAL, busy_mean REAL, generated TEXT, received_at REAL, flagged INTEGER DEFAULT 0,
  PRIMARY KEY (submitter, freq_hz, bw_hz, bucket_s, bucket));
CREATE TABLE IF NOT EXISTS cad (submitter TEXT, freq_hz INTEGER, bw_hz INTEGER, sf INTEGER, n_cad INTEGER, hits INTEGER, hit_rate REAL, longest_run INTEGER, generated TEXT, received_at REAL, flagged INTEGER DEFAULT 0,
  PRIMARY KEY (submitter, freq_hz, bw_hz, sf));
CREATE TABLE IF NOT EXISTS decode (submitter TEXT, freq_hz INTEGER, network TEXT, preset TEXT, dwell_s REAL, n_ok INTEGER, n_crc_err INTEGER, rssi_med REAL, generated TEXT, received_at REAL, flagged INTEGER DEFAULT 0,
  PRIMARY KEY (submitter, freq_hz, network, preset));
CREATE TABLE IF NOT EXISTS uploads (id INTEGER PRIMARY KEY, submitter TEXT, received_at REAL, bytes INTEGER, energy_rows INTEGER, flagged INTEGER, generated TEXT);
"""


class ShareDB:
    def __init__(self, path: str):
        self.path = path
        self.lock = threading.Lock()
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.executescript(SCHEMA)
        self.con.commit()

    def latest(self, submitter: str) -> dict:
        out = {}
        for gran, secs in (("hour", 3600), ("day", 86400)):
            r = self.con.execute("SELECT MAX(bucket) FROM energy WHERE submitter = ? AND bucket_s = ?", (submitter, secs)).fetchone()
            out[gran] = r[0] if r and r[0] else None
        return out

    def ingest(self, doc: dict, nbytes: int) -> dict:
        now = time.time()
        gen = str(doc.get("generated", ""))
        sub = doc["submitter"]
        energy = doc.get("energy") or []
        hot = [e for e in energy if e.get("floor_p10_med") is not None and e["floor_p10_med"] > -70.0]
        flagged = 1 if energy and len(hot) > 0.9 * len(energy) else 0
        with self.lock:
            cell = doc.get("cell") or {}
            self.con.execute("INSERT INTO submitters (submitter, first_seen, last_seen, uploads, tool, board, calibration, cell_lat, cell_lon, cell_size) VALUES (?,?,?,1,?,?,?,?,?,?) "
                             "ON CONFLICT(submitter) DO UPDATE SET last_seen = excluded.last_seen, uploads = uploads + 1, tool = excluded.tool, board = excluded.board, calibration = excluded.calibration, cell_lat = excluded.cell_lat, cell_lon = excluded.cell_lon, cell_size = excluded.cell_size",
                             (sub, now, now, doc.get("tool"), doc.get("board"), doc.get("calibration"), cell.get("lat"), cell.get("lon"), cell.get("size_deg")))
            n = 0
            for e in energy:
                self.con.execute("INSERT INTO energy (submitter, freq_hz, bw_hz, bucket_s, bucket, n_rows, n_samples, hours, floor_p10_med, p90_med, peak_max, busy_mean, generated, received_at, flagged) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                                 "ON CONFLICT(submitter, freq_hz, bw_hz, bucket_s, bucket) DO UPDATE SET n_rows = excluded.n_rows, n_samples = excluded.n_samples, hours = excluded.hours, floor_p10_med = excluded.floor_p10_med, p90_med = excluded.p90_med, peak_max = excluded.peak_max, busy_mean = excluded.busy_mean, generated = excluded.generated, received_at = excluded.received_at, flagged = excluded.flagged WHERE excluded.generated >= energy.generated",
                                 (sub, int(e["freq_hz"]), int(e["bw_hz"]), int(e.get("bucket_s", 3600)), str(e["bucket"]), *[e.get(c) for c in ENERGY_COLS], gen, now, flagged))
                n += 1
            for c in doc.get("cad") or []:
                self.con.execute("INSERT INTO cad (submitter, freq_hz, bw_hz, sf, n_cad, hits, hit_rate, longest_run, generated, received_at, flagged) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                                 "ON CONFLICT(submitter, freq_hz, bw_hz, sf) DO UPDATE SET n_cad = excluded.n_cad, hits = excluded.hits, hit_rate = excluded.hit_rate, longest_run = excluded.longest_run, generated = excluded.generated, received_at = excluded.received_at, flagged = excluded.flagged WHERE excluded.generated >= cad.generated",
                                 (sub, int(c["freq_hz"]), int(c["bw_hz"]), int(c["sf"]), c.get("n_cad"), c.get("hits"), c.get("hit_rate"), c.get("longest_run"), gen, now, flagged))
            for d in doc.get("decode") or []:
                self.con.execute("INSERT INTO decode (submitter, freq_hz, network, preset, dwell_s, n_ok, n_crc_err, rssi_med, generated, received_at, flagged) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                                 "ON CONFLICT(submitter, freq_hz, network, preset) DO UPDATE SET dwell_s = excluded.dwell_s, n_ok = excluded.n_ok, n_crc_err = excluded.n_crc_err, rssi_med = excluded.rssi_med, generated = excluded.generated, received_at = excluded.received_at, flagged = excluded.flagged WHERE excluded.generated >= decode.generated",
                                 (sub, int(d["freq_hz"]), str(d["network"]), str(d["preset"]), d.get("dwell_s"), d.get("n_ok"), d.get("n_crc_err"), d.get("rssi_med"), gen, now, flagged))
            self.con.execute("INSERT INTO uploads (submitter, received_at, bytes, energy_rows, flagged, generated) VALUES (?,?,?,?,?,?)", (sub, now, nbytes, n, flagged, gen))
            self.con.commit()
        latest = max((str(e["bucket"]) for e in energy), default=None)
        return {"accepted": n, "latest": latest, "flagged": bool(flagged)}

    def count(self, table: str) -> int:
        return self.con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def rows(self, table: str) -> list:
        cur = self.con.execute(f"SELECT * FROM {table}")
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def submitters(self) -> list:
        return [r[0] for r in self.con.execute("SELECT submitter FROM submitters ORDER BY submitter")]

    def stats(self) -> dict:
        return {"energy": self.count("energy"), "cad": self.count("cad"), "decode": self.count("decode"), "submitters": self.count("submitters"),
                "uploads": self.count("uploads"), "flagged_energy": self.con.execute("SELECT COUNT(*) FROM energy WHERE flagged = 1").fetchone()[0],
                "cells": self.con.execute("SELECT COUNT(DISTINCT cell_lat || ',' || cell_lon) FROM submitters WHERE cell_lat IS NOT NULL").fetchone()[0]}


class _RateLimit:
    def __init__(self, per_hour: int):
        self.per_hour = per_hour; self.hits: dict[str, list] = {}; self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.time()
        with self.lock:
            h = [t for t in self.hits.get(key, []) if now - t < 3600]
            if len(h) >= self.per_hour:
                self.hits[key] = h
                return False
            h.append(now); self.hits[key] = h
            return True


def validate(doc, header_submitter: str) -> str | None:
    if not isinstance(doc, dict):
        return "body is not a JSON object"
    if doc.get("format") != FORMAT:
        return f"format must be {FORMAT}"
    sub = doc.get("submitter")
    if not isinstance(sub, str) or not (8 <= len(sub) <= 64) or not all(c in "0123456789abcdefABCDEF" for c in sub):
        return "submitter must be a hex token of 8-64 characters"
    if header_submitter != sub:
        return "X-Lorascan-Submitter header does not match the body's submitter"
    if doc.get("granularity") not in ("hour", "day"):
        return "granularity must be hour or day"
    cell = doc.get("cell")
    if cell is not None:
        if not isinstance(cell, dict) or not all(isinstance(cell.get(k), (int, float)) for k in ("lat", "lon", "size_deg")):
            return "cell must be {lat, lon, size_deg} or null"
        if cell["size_deg"] < 0.01:
            return "cell.size_deg must be >= 0.01 (no precise positions)"
    for e in doc.get("energy") or []:
        if not all(k in e for k in ("freq_hz", "bw_hz", "bucket")):
            return "energy rows need freq_hz, bw_hz, bucket"
    return None


def tiles_config(url: str | None, attribution: str | None) -> dict:
    """Basemap tile source for the map page: flags beat LORASCAN_TILES_URL / LORASCAN_TILES_ATTRIBUTION, which beat the
    OpenStreetMap default. Whatever is in the URL (e.g. a Carto api_key) is visible to every visitor: restrict the key
    to the site's referrer at the provider."""
    import os
    from .share_page import DEFAULT_TILES
    u = url or os.environ.get("LORASCAN_TILES_URL") or DEFAULT_TILES["url"]
    a = attribution or os.environ.get("LORASCAN_TILES_ATTRIBUTION") or (DEFAULT_TILES["attribution"] if u == DEFAULT_TILES["url"] else "map tiles: see provider")
    return {"url": u, "attribution": a}


def make_share_server(host: str, port: int, db_path: str, max_gzip: int = 4_000_000, max_inflated: int = 64_000_000, rate_per_hour: int = 60, tiles: dict | None = None):
    db = ShareDB(db_path)
    tiles = tiles or tiles_config(None, None)
    rl = _RateLimit(rate_per_hour)

    class H(BaseHTTPRequestHandler):
        server_version = "lorascan-share/1"

        def log_message(self, fmt, *args):
            print(f"[share-server] {self.address_string()} {fmt % args}", flush=True)

        def _send(self, code: int, obj, ctype="application/json"):
            body = (json.dumps(obj) if ctype == "application/json" else str(obj)).encode()
            self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/healthz":
                return self._send(200, "ok", "text/plain")
            if path in ("/", "/index.html"):
                from .share_page import map_data, render_map_page
                with db.lock:
                    m = map_data(db)
                return self._send(200, render_map_page(m, tiles), "text/html; charset=utf-8")
            if path == "/v1/map.json":
                from .share_page import map_data
                with db.lock:
                    m = map_data(db)
                return self._send(200, m)
            if path == "/v1/watermark":
                sub = self.headers.get("X-Lorascan-Submitter", "")
                return self._send(200, {"latest": db.latest(sub) if sub else {"hour": None, "day": None}})
            if path == "/v1/stats":
                return self._send(200, db.stats())
            return self._send(404, "not found", "text/plain")

        def do_POST(self):
            if self.path.split("?")[0] != "/v1/share":
                return self._send(404, "not found", "text/plain")
            n = int(self.headers.get("Content-Length") or 0)
            if n > max_gzip:
                left = n                                   # drain so the client sees the 413 instead of a broken pipe
                while left > 0:
                    chunk = self.rfile.read(min(left, 1 << 16))
                    if not chunk:
                        break
                    left -= len(chunk)
                return self._send(413, f"body too large (> {max_gzip} bytes)", "text/plain")
            raw = self.rfile.read(n)
            sub = self.headers.get("X-Lorascan-Submitter", "")
            if not rl.allow(sub or self.address_string()):
                return self._send(429, f"more than {rate_per_hour} uploads per hour for this submitter", "text/plain")
            try:
                if (self.headers.get("Content-Encoding") or "").lower() == "gzip":
                    d = gzip.GzipFile(fileobj=__import__("io").BytesIO(raw))
                    data = d.read(max_inflated + 1)
                    if len(data) > max_inflated:
                        return self._send(413, f"inflated body too large (> {max_inflated} bytes)", "text/plain")
                else:
                    data = raw
                doc = json.loads(data.decode())
            except Exception as e:
                return self._send(400, f"cannot decode body: {type(e).__name__}", "text/plain")
            err = validate(doc, sub)
            if err:
                return self._send(400, err, "text/plain")
            try:
                res = db.ingest(doc, len(raw))
            except Exception as e:
                return self._send(400, f"cannot store document: {type(e).__name__}: {e}", "text/plain")
            self._send(200, res)

    srv = ThreadingHTTPServer((host, port), H)
    srv.daemon_threads = True
    srv.db_path = db_path            # type: ignore[attr-defined]
    return srv


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="lorascan-share-server", description="lorascan community share endpoint (reference implementation)")
    p.add_argument("--host", default="0.0.0.0"); p.add_argument("--port", type=int, default=8081); p.add_argument("--db", default="/data/share.sqlite")
    p.add_argument("--max-gzip", type=int, default=4_000_000); p.add_argument("--max-inflated", type=int, default=64_000_000); p.add_argument("--rate-per-hour", type=int, default=60)
    p.add_argument("--tiles-url", default=None, help="basemap tile URL template (default OpenStreetMap; env LORASCAN_TILES_URL), e.g. a Carto raster URL with api_key")
    p.add_argument("--tiles-attribution", default=None, help="attribution HTML for the tiles (env LORASCAN_TILES_ATTRIBUTION)")
    a = p.parse_args(argv)
    srv = make_share_server(a.host, a.port, a.db, a.max_gzip, a.max_inflated, a.rate_per_hour, tiles=tiles_config(a.tiles_url, a.tiles_attribution))
    print(f"[share-server] listening on {a.host}:{a.port}, db {a.db}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
