"""Reference implementation of the share endpoint (docs/superpowers/specs/2026-09-14-lorascan-share-endpoint.md):
GET /v1/watermark, POST /v1/share (gzip JSON, idempotent upsert), limits, flags — driven by the real client."""
import gzip, json, threading, urllib.request, urllib.error
import pytest
from lorascan.share_server import make_share_server, ShareDB
from lorascan.share import build_share, upload_share
from lorascan.store.db import Store
from lorascan.measure.energy import EnergyRow
from lorascan.measure.cad import CadRow


def _store(tmp_path, hours=30, name="s.db", bw_hz=125_000):
    s = Store(str(tmp_path / name)); rid = s.new_run("survey", "fake", "")
    t0 = 1_757_800_000.0
    for h in range(hours):
        for f in (902_000_000, 911_500_000):
            s.add_energy(rid, EnergyRow(ts=t0 + h * 3600, freq_hz=f, bw_hz=bw_hz, engine="poll", n=100, floor_dbm=-110, p50=-105, p90=-100, peak=-80, busy_frac=0.1))
    s.add_cad(rid, CadRow(ts=t0, freq_hz=911_500_000, bw_hz=bw_hz, sf=9, symbols=2, n_cad=50, hits=20, longest_run=4, det_peak=23, det_min=10))
    return s


@pytest.fixture
def server(tmp_path):
    srv = make_share_server("127.0.0.1", 0, str(tmp_path / "share.sqlite"), max_gzip=4_000_000, max_inflated=64_000_000, rate_per_hour=60)
    t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
    yield srv, f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def _post(url, body: bytes, submitter="0123abcd0123abcd", gz=True, ctype="application/json"):
    req = urllib.request.Request(url + "/v1/share", data=body, method="POST", headers={"Content-Type": ctype, "X-Lorascan-Submitter": submitter, **({"Content-Encoding": "gzip"} if gz else {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_first_upload_then_incremental_resend_never_double_counts(tmp_path, server):
    srv, url = server
    doc = build_share(_store(tmp_path), None, "a1b2c3d4e5f60718", "fake", granularity="hour")
    r1 = upload_share(doc, url, retries=1, backoff_s=0)
    assert r1["accepted"] == 60 and r1["sent"] == 60 and r1["watermark"] is None
    db = ShareDB(srv.db_path)
    assert db.count("energy") == 60 and db.count("cad") == 1
    r2 = upload_share(doc, url, retries=1, backoff_s=0)                 # nothing new: only the last bucket goes again
    assert r2["sent"] == 2 and r2["skipped"] == 58 and r2["accepted"] == 2 and db.count("energy") == 60
    w = json.loads(urllib.request.urlopen(urllib.request.Request(url + "/v1/watermark", headers={"X-Lorascan-Submitter": "a1b2c3d4e5f60718"})).read())
    assert w["latest"]["hour"] == max(e["bucket"] for e in doc["energy"]) and w["latest"]["day"] is None


def test_unknown_submitter_watermark_is_nulls_and_two_submitters_are_separate(tmp_path, server):
    srv, url = server
    w = json.loads(urllib.request.urlopen(urllib.request.Request(url + "/v1/watermark", headers={"X-Lorascan-Submitter": "0000000000000000"})).read())
    assert w == {"latest": {"hour": None, "day": None}}
    a = build_share(_store(tmp_path, 3, "a.db"), None, "aaaaaaaaaaaaaaaa", "fake", granularity="day")
    b = build_share(_store(tmp_path, 3, "b.db"), None, "bbbbbbbbbbbbbbbb", "fake", granularity="day")
    upload_share(a, url, retries=1, backoff_s=0); upload_share(b, url, retries=1, backoff_s=0)
    assert ShareDB(srv.db_path).count("energy") == 4 and ShareDB(srv.db_path).submitters() == ["aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"]


def test_validation_rejects_bad_format_mismatch_and_oversize(tmp_path, server):
    srv, url = server
    doc = build_share(_store(tmp_path, 2), None, "0123abcd0123abcd", "fake")
    bad = dict(doc); bad["format"] = "lorascan-share/1"
    code, msg = _post(url, gzip.compress(json.dumps(bad).encode()))
    assert code == 400 and "format" in msg
    code, msg = _post(url, gzip.compress(json.dumps(doc).encode()), submitter="feedfacefeedface")
    assert code == 400 and "submitter" in msg
    code, msg = _post(url, b"\x00" * 4_000_001)
    assert code == 413
    code, msg = _post(url, b"not gzip", gz=True)
    assert code == 400


def test_miscalibrated_upload_is_stored_flagged_not_merged(tmp_path, server):
    srv, url = server
    doc = build_share(_store(tmp_path, 2), None, "c0ffee11c0ffee11", "fake")
    for e in doc["energy"]:
        e["floor_p10_med"] = -60.0                                         # every channel floor above -70 dBm
    r = upload_share(doc, url, retries=1, backoff_s=0)
    assert r["accepted"] == 4 and r.get("flagged") is True
    db = ShareDB(srv.db_path)
    assert db.count("energy") == 4 and all(row["flagged"] == 1 for row in db.rows("energy"))


def test_rate_limit_per_submitter(tmp_path):
    srv = make_share_server("127.0.0.1", 0, str(tmp_path / "r.sqlite"), rate_per_hour=2)
    t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()
    url = f"http://127.0.0.1:{srv.server_port}"
    doc = build_share(_store(tmp_path, 1), None, "dead0000beef0000", "fake")
    body = gzip.compress(json.dumps(doc).encode())
    codes = [_post(url, body, submitter="dead0000beef0000")[0] for _ in range(3)]
    srv.shutdown()
    assert codes == [200, 200, 429]


def test_dump_json_has_bw_hz_and_matching_count(tmp_path, server):
    srv, url = server
    doc = build_share(_store(tmp_path, 2, bw_hz=500_000), None, "a1b2c3d4e5f60718", "fake", granularity="hour")
    r = upload_share(doc, url, retries=1, backoff_s=0)
    assert r["accepted"] == 4
    resp = urllib.request.urlopen(url + "/v1/dump.json")
    out = json.loads(resp.read().decode())
    assert resp.status == 200
    assert out["energy"]
    assert all("bw_hz" in row for row in out["energy"])
    assert any(row["bw_hz"] == 500_000 for row in out["energy"])
    assert out["count"] == len(out["energy"])
    assert "generated" in out
    assert isinstance(out["submitters"], list) and any(s["submitter"] == "a1b2c3d4e5f60718" for s in out["submitters"])


def test_dump_json_excludes_flagged_uploads(tmp_path, server):
    srv, url = server
    doc = build_share(_store(tmp_path, 2), None, "c0ffee11c0ffee11", "fake")
    for e in doc["energy"]:
        e["floor_p10_med"] = -60.0                                         # every channel floor above -70 dBm -> flagged
    r = upload_share(doc, url, retries=1, backoff_s=0)
    assert r.get("flagged") is True
    resp = urllib.request.urlopen(url + "/v1/dump.json")
    out = json.loads(resp.read().decode())
    assert not any(row["submitter"] == "c0ffee11c0ffee11" for row in out["energy"])
    assert not any("flagged" in row for row in out["energy"])


def test_dump_csv_header_and_row_count(tmp_path, server):
    srv, url = server
    doc = build_share(_store(tmp_path, 2), None, "a1b2c3d4e5f60718", "fake", granularity="hour")
    upload_share(doc, url, retries=1, backoff_s=0)
    resp = urllib.request.urlopen(url + "/v1/dump.csv")
    assert resp.status == 200
    text = resp.read().decode()
    lines = text.splitlines()
    assert lines[0] == "submitter,freq_hz,bw_hz,bucket_s,bucket,hours,floor_p10_med,p90_med,peak_max,busy_mean"
    db = ShareDB(srv.db_path)
    n_unflagged = sum(1 for row in db.rows("energy") if row["flagged"] != 1)
    assert len(lines) - 1 == n_unflagged
