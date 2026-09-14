import json, re
from lorascan.store.db import Store
from lorascan.measure.energy import EnergyRow
from lorascan.report.html import render_report, quietest

def fill(s, rid, offset_hours=0.0):
    t0 = 1_700_000_000.0
    for k, f in enumerate([902_000_000, 906_875_000, 911_500_000, 921_000_000]):
        for j in range(3):
            busy = 0.5 if f == 911_500_000 else 0.0
            s.add_energy(rid, EnergyRow(ts=t0 + j * 60 + offset_hours * 3600, freq_hz=f, bw_hz=125000, engine="poll", n=200, hist=[0]*33,
                                        floor_dbm=-110.0 - k, p50=-108.0, p90=-100.0 if busy else -108.0, peak=-70.0 if busy else -105.0, busy_frac=busy))

def test_report_contains_plotly_data_and_calibration_note(tmp_path):
    s = Store(str(tmp_path / "t.db")); rid = s.new_run("quick", "nebra-duo-hat", "bench")
    fill(s, rid)
    out = tmp_path / "r.html"
    html = render_report(s, str(out), rssi_offset_db=0.0)
    assert out.exists() and "Plotly.newPlot" in html and "cdnjs.cloudflare.com/ajax/libs/plotly.js" in html
    assert "911.5" in html and "relative (uncalibrated)" in html
    m = re.search(r'id="lorascan-data" type="application/json">(.*?)</script>', html, re.S)
    data = json.loads(m.group(1))
    assert len(data["channels"]) == 4 and data["channels"][2]["freq_hz"] == 911_500_000 and data["channels"][2]["label"] == "Loomwave fleet"
    assert data["heat"]["freqs_mhz"] == [902.0, 906.875, 911.5, 921.0] and len(data["heat"]["buckets"]) == 3

def test_calibrated_report_drops_the_uncalibrated_note(tmp_path):
    s = Store(str(tmp_path / "t.db")); rid = s.new_run("quick", "x", ""); fill(s, rid)
    html = render_report(s, str(tmp_path / "r.html"), rssi_offset_db=3.0)
    assert "relative (uncalibrated)" not in html and "calibrated" in html

def test_quietest_ranks_by_busy_then_floor():
    chans = [{"freq_hz": 911_500_000, "busy_mean": 0.5, "floor_med": -110.0, "p90_med": -100.0, "peak_max": -70.0},
             {"freq_hz": 902_000_000, "busy_mean": 0.0, "floor_med": -110.0, "p90_med": -108.0, "peak_max": -105.0},
             {"freq_hz": 921_000_000, "busy_mean": 0.0, "floor_med": -113.0, "p90_med": -110.0, "peak_max": -106.0}]
    assert [c["freq_hz"] for c in quietest(chans, 2)] == [921_000_000, 902_000_000]
