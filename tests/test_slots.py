"""Loomwave/lorascan#2 (3): N kHz slot view over existing rows."""
import csv, os
from lorascan import cli
from lorascan.report.slots import slot_view
from lorascan.store.db import Store
from lorascan.measure.energy import EnergyRow
from lorascan.measure.cad import CadRow
from lorascan.measure.decode import DecodeRow


def test_slot_view_aggregates_worst_case_per_window_and_ranks():
    chans = [
        {"freq_hz": 902_000_000, "mhz": 902.0, "floor_med": -110, "p90_med": -100, "peak_max": -90, "busy_mean": 0.0, "n_rows": 5, "label": ""},
        {"freq_hz": 902_200_000, "mhz": 902.2, "floor_med": -105, "p90_med": -80, "peak_max": -40, "busy_mean": 0.3, "n_rows": 5, "label": ""},
        {"freq_hz": 902_400_000, "mhz": 902.4, "floor_med": -108, "p90_med": -95, "peak_max": -70, "busy_mean": 0.1, "n_rows": 5, "label": ""},
        {"freq_hz": 902_600_000, "mhz": 902.6, "floor_med": -112, "p90_med": -104, "peak_max": -95, "busy_mean": 0.0, "n_rows": 5, "label": ""},
    ]
    cad = [{"freq_hz": 902_200_000, "bw_hz": 125_000, "sf": 9, "n_cad": 50, "hits": 10, "hit_rate": 0.2, "longest_run": 2},
           {"freq_hz": 902_600_000, "bw_hz": 125_000, "sf": 7, "n_cad": 50, "hits": 1, "hit_rate": 0.02, "longest_run": 1}]
    dec = [{"freq_hz": 902_400_000, "network": "meshtastic", "preset": "LongFast", "n_ok": 3, "n_crc_err": 0}]
    slots = slot_view(chans, cad, dec, slot_hz=500_000)
    assert [(s["start_hz"], s["end_hz"]) for s in slots] == [(902_500_000, 903_000_000), (902_000_000, 902_500_000)]   # best first
    worst = slots[1]
    assert worst["n_channels"] == 3 and worst["floor_worst"] == -105 and worst["peak_max"] == -40 and worst["busy_max"] == 0.3
    assert worst["cad_hit_max"] == 0.2 and worst["cad_sf_max"] == 9 and worst["decoded"] == "meshtastic/LongFast:3"
    assert slots[0]["score"] < worst["score"] and slots[0]["decoded"] == ""


def test_cli_report_and_export_slot(tmp_path):
    s = Store(str(tmp_path / "s.db")); rid = s.new_run("quick", "fake", "")
    for f, b in ((902_000_000, 0.0), (902_200_000, 0.5), (903_000_000, 0.0)):
        s.add_energy(rid, EnergyRow(ts=1e9, freq_hz=f, bw_hz=125_000, engine="poll", n=10, floor_dbm=-110, p50=-100, p90=-95, peak=-80, busy_frac=b))
    s.close(); db = str(tmp_path / "s.db"); out = str(tmp_path / "r.html")
    assert cli.main(["report", "--db", db, "--out", out, "--slot", "500000"]) == 0
    html = open(out).read()
    assert "500 kHz slots" in html and "902.500" in html
    assert cli.main(["export", "--db", db, "--csv", str(tmp_path / "slots.csv"), "--table", "slots", "--slot", "500000"]) == 0
    rows = list(csv.DictReader(open(str(tmp_path / "slots.csv"))))
    assert len(rows) == 2 and rows[0]["start_mhz"] == "903.000" and float(rows[-1]["busy_max"]) == 0.5   # empty windows are not listed


def test_slot_score_weighs_worst_case_floor(tmp_path):
    """#4 field note: a steady carrier (busy ~0, high floor) must not top the sort."""
    chans = [
        {"freq_hz": 902_000_000, "mhz": 902.0, "floor_med": -110, "p90_med": -100, "peak_max": -90, "busy_mean": 0.15, "n_rows": 5, "label": ""},
        {"freq_hz": 902_600_000, "mhz": 902.6, "floor_med": -80, "p90_med": -78, "peak_max": -75, "busy_mean": 0.0, "n_rows": 5, "label": ""},   # carrier: 30 dB above the band's best floor
    ]
    slots = slot_view(chans, [], [], slot_hz=500_000)
    assert [s["start_hz"] for s in slots] == [902_000_000, 902_500_000]
    assert slots[0]["score"] == 0.15 and slots[1]["score"] == 3.0 and slots[1]["floor_penalty"] == 3.0     # (−80 − −110) / 10 dB
