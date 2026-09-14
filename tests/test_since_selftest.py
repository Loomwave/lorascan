import time
from lorascan import cli
from lorascan.cli import _duration
from lorascan.store.db import Store
from lorascan.measure.energy import EnergyRow
from lorascan.report.html import build_data

def test_report_since_limits_the_window(tmp_path):
    s = Store(str(tmp_path / "t.db")); rid = s.new_run("survey", "x", "")
    now = time.time()
    for k, age in enumerate((3 * 3600, 2 * 3600, 600, 60)):
        s.add_energy(rid, EnergyRow(ts=now - age, freq_hz=902_000_000 + k * 200_000, bw_hz=125000, engine="scan", n=10, hist=[0]*33, floor_dbm=-110, p50=-105, p90=-100, peak=-90, busy_frac=0.0))
    d = build_data(s, since=now - 3600)
    assert [c["freq_hz"] for c in d["channels"]] == [902_400_000, 902_600_000]
    assert _duration("6h") == 21600 and _duration("90m") == 5400

def test_selftest_scan_engine_on_the_fake_falls_back_gracefully(capsys):
    rc = cli.main(["selftest", "--profile", "fake", "--dwell", "0.01", "--sample-gap", "0.001", "--engine", "scan"])
    out = capsys.readouterr()
    assert rc == 0 and "scan engine" in (out.out + out.err) and "PASS" in out.out
