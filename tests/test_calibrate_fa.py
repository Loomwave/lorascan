import os
from lorascan import cli
from lorascan.profile import load_profile, parse_mini_yaml
from lorascan.store.db import Store
from lorascan.measure.cad import CadRow
from lorascan.report.html import build_data

def test_calibrate_writes_the_offset_into_a_profile_copy(tmp_path, capsys):
    out = str(tmp_path / "cal.yaml")
    # the fake radio reads about -105 dBm; a known -60 dBm input therefore means an offset of about +45 dB
    rc = cli.main(["calibrate", "--profile", "fake", "--level", "-60", "--freq", "915.0", "--dwell", "0.02", "--sample-gap", "0.001", "--out", out])
    assert rc == 0 and os.path.exists(out)
    d = parse_mini_yaml(open(out).read())
    assert 40.0 <= d["cal"]["rssi_offset_db"] <= 50.0 and d["name"] == "fake-calibrated"
    assert "rssi_offset_db" in capsys.readouterr().out

def test_report_carries_the_cad_false_alarm_rate_when_a_quiet_reference_sweep_exists(tmp_path):
    s = Store(str(tmp_path / "t.db")); rid = s.new_run("quick", "x", "")
    s.add_cad(rid, CadRow(ts=1e9, freq_hz=921_000_000, bw_hz=125000, sf=9, symbols=2, n_cad=200, hits=3, longest_run=1, det_peak=23, det_min=10))
    s.add_event(rid, "cad_reference", "921000000 sf9 bw125")
    d = build_data(s)
    assert d["cad_false_alarm"] == {"freq_hz": 921_000_000, "sf": 9, "bw_hz": 125000, "rate": 0.015, "n_cad": 200}
