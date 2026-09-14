import json, re
from lorascan.store.db import Store
from lorascan.measure.energy import EnergyRow
from lorascan.measure.cad import CadRow
from lorascan.measure.decode import DecodeRow
from lorascan.report.html import render_report, build_data

def test_report_includes_sf_map_and_decoded_networks(tmp_path):
    s = Store(str(tmp_path / "t.db")); rid = s.new_run("watch", "x", "")
    s.add_energy(rid, EnergyRow(ts=1e9, freq_hz=906_875_000, bw_hz=125000, engine="scan", n=100, hist=[0]*33, floor_dbm=-110, p50=-108, p90=-100, peak=-70, busy_frac=0.3))
    s.add_cad(rid, CadRow(ts=1e9, freq_hz=906_875_000, bw_hz=250000, sf=11, symbols=2, n_cad=50, hits=20, longest_run=6, det_peak=25, det_min=10))
    s.add_decode(rid, DecodeRow(ts=1e9, freq_hz=906_875_000, network="meshtastic", preset="LongFast", dwell_s=30, n_ok=5, n_crc_err=0, rssi_med=-95, snr_med=6, len_med=40))
    d = build_data(s)
    assert d["sfmap"]["freqs_mhz"] == [906.875] and d["sfmap"]["sfs"] == [11] and d["sfmap"]["z"][0][0] == 0.4
    assert d["channels"][0]["decoded"] == "meshtastic/LongFast 5"
    html = render_report(s, str(tmp_path / "r.html"))
    assert "LoRa presence by spreading factor" in html and "meshtastic/LongFast" in html
