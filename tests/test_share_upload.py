"""Spec §4a low-bandwidth options 1+2+3 (Matt 2026-09-14): granularity + gzip, incremental idempotent upload, budget."""
import gzip, json, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import pytest
from lorascan import cli
from lorascan.share import build_share, choose_by_budget, parse_budget, upload_share, filter_after_watermark, SHARE_FORMAT
from lorascan.store.db import Store
from lorascan.measure.energy import EnergyRow


def _store(tmp_path, hours=30):
    s = Store(str(tmp_path / "s.db")); rid = s.new_run("survey", "fake", "")
    t0 = 1_757_800_000.0  # 2025-09-13T22:26:40Z
    for h in range(hours):
        for f in (902_000_000, 911_500_000):
            s.add_energy(rid, EnergyRow(ts=t0 + h * 3600, freq_hz=f, bw_hz=125_000, engine="poll", n=100, floor_dbm=-110, p50=-105, p90=-100, peak=-80, busy_frac=0.1 * (h % 3)))
    return s


def test_day_granularity_buckets_by_day_and_adds_when_matrix(tmp_path):
    s = _store(tmp_path)
    d = build_share(s, None, "tok", "fake", granularity="day")
    assert d["format"] == SHARE_FORMAT == "lorascan-share/2" and d["granularity"] == "day"
    assert all(len(e["bucket"]) == 10 for e in d["energy"]) and d["energy"][0]["bucket_s"] == 86400
    assert len(d["energy"]) == 2 * 3          # 30 hours span 3 UTC days x 2 channels
    assert len(d["when"]) == 7 and len(d["when"][0]) == 24 and any(v is not None for r in d["when"] for v in r)
    h = build_share(s, None, "tok", "fake", granularity="hour")
    assert h["energy"][0]["bucket_s"] == 3600 and len(h["energy"]) == 60 and "when" not in h


def test_budget_parse_and_choice(tmp_path):
    assert parse_budget("20k/day") == 20_000 and parse_budget("1M/day") == 1_000_000 and parse_budget("500/day") == 500
    with pytest.raises(ValueError):
        parse_budget("fast")
    s = _store(tmp_path)
    doc, size = choose_by_budget(s, None, "tok", "fake", budget_per_day=10_000_000)
    assert doc["granularity"] == "hour"
    doc, size = choose_by_budget(s, None, "tok", "fake", budget_per_day=150)
    assert doc["granularity"] == "day" and doc["cad"] == [] and doc["decode"] == [] and size > 0


def test_filter_after_watermark_is_inclusive_of_the_last_bucket(tmp_path):
    d = build_share(_store(tmp_path), None, "tok", "fake", granularity="hour")
    buckets = sorted({e["bucket"] for e in d["energy"]})
    kept = filter_after_watermark(d, buckets[10])
    assert sorted({e["bucket"] for e in kept["energy"]}) == buckets[10:]
    assert filter_after_watermark(d, None)["energy"] == d["energy"]


class _Srv(BaseHTTPRequestHandler):
    log = []; fail_first = [0]; latest = [None]
    def log_message(self, *a): pass
    def do_GET(self):
        assert self.path == "/v1/watermark"
        self.log.append(("GET", self.headers.get("X-Lorascan-Submitter")))
        body = json.dumps({"latest": {"hour": self.latest[0], "day": None}}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        assert self.path == "/v1/share"
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        if self.fail_first[0]:
            self.fail_first[0] -= 1
            self.send_response(503); self.end_headers(); return
        assert self.headers.get("Content-Encoding") == "gzip"
        doc = json.loads(gzip.decompress(raw))
        self.log.append(("POST", self.headers.get("X-Lorascan-Submitter"), len(raw), len(doc["energy"])))
        body = json.dumps({"accepted": len(doc["energy"]), "latest": max(e["bucket"] for e in doc["energy"])}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)


@pytest.fixture
def server():
    _Srv.log.clear(); _Srv.fail_first[0] = 0; _Srv.latest[0] = None
    srv = HTTPServer(("127.0.0.1", 0), _Srv); t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_upload_is_gzipped_incremental_and_reports(tmp_path, server):
    d = build_share(_store(tmp_path), None, "tok123", "fake", granularity="hour")
    buckets = sorted({e["bucket"] for e in d["energy"]})
    _Srv.latest[0] = buckets[20]
    res = upload_share(d, server, retries=1, backoff_s=0)
    posts = [x for x in _Srv.log if x[0] == "POST"]
    assert posts[0][1] == "tok123" and posts[0][3] == 2 * 10 and posts[0][2] < len(json.dumps(d).encode())
    assert res["accepted"] == 20 and res["sent"] == 20 and res["skipped"] == 40


def test_upload_retries_on_503(tmp_path, server):
    _Srv.fail_first[0] = 2
    res = upload_share(build_share(_store(tmp_path, 2), None, "tok", "fake"), server, retries=3, backoff_s=0)
    assert res["accepted"] == 4 and res["attempts"] == 3


def test_cli_share_to_and_upload_file(tmp_path, server, capsys):
    s = _store(tmp_path, 3); s.close(); db = str(tmp_path / "s.db"); out = str(tmp_path / "sh.json"); tok = str(tmp_path / "token")
    assert cli.main(["share", "--db", db, "--out", out, "--token-path", tok, "--granularity", "day", "--to", server]) == 0
    assert "uploaded" in capsys.readouterr().out
    assert cli.main(["upload", out, "--to", server]) == 0
    posts = [x for x in _Srv.log if x[0] == "POST"]
    assert len(posts) == 2 and posts[1][1] == open(tok).read().strip()
