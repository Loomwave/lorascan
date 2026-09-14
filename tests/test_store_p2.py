from lorascan.store.db import Store
from lorascan.measure.cad import CadRow
from lorascan.measure.decode import DecodeRow

def test_cad_rows_roundtrip_and_summary(tmp_path):
    s = Store(str(tmp_path / "t.db")); rid = s.new_run("watch", "x", "")
    s.add_cad(rid, CadRow(ts=1.0, freq_hz=906_875_000, bw_hz=250000, sf=11, symbols=2, n_cad=50, hits=10, longest_run=4, det_peak=25, det_min=10))
    s.add_cad(rid, CadRow(ts=2.0, freq_hz=906_875_000, bw_hz=250000, sf=11, symbols=2, n_cad=50, hits=30, longest_run=9, det_peak=25, det_min=10))
    s.add_cad(rid, CadRow(ts=3.0, freq_hz=906_875_000, bw_hz=125000, sf=7, symbols=2, n_cad=40, hits=0, longest_run=0, det_peak=22, det_min=10, timeouts=1))
    rows = list(s.iter_cad(rid)); assert len(rows) == 3 and rows[2].timeouts == 1
    summ = {(c["freq_hz"], c["sf"], c["bw_hz"]): c for c in s.cad_summary(rid)}
    assert summ[(906_875_000, 11, 250000)]["hits"] == 40 and summ[(906_875_000, 11, 250000)]["n_cad"] == 100 and abs(summ[(906_875_000, 11, 250000)]["hit_rate"] - 0.4) < 1e-9 and summ[(906_875_000, 11, 250000)]["longest_run"] == 9
    assert summ[(906_875_000, 7, 125000)]["hit_rate"] == 0.0

def test_decode_rows_roundtrip_and_summary(tmp_path):
    s = Store(str(tmp_path / "t.db")); rid = s.new_run("watch", "x", "")
    s.add_decode(rid, DecodeRow(ts=1.0, freq_hz=906_875_000, network="meshtastic", preset="LongFast", dwell_s=30.0, n_ok=3, n_crc_err=1, rssi_med=-95.0, snr_med=6.0, len_med=40))
    s.add_decode(rid, DecodeRow(ts=2.0, freq_hz=906_875_000, network="meshtastic", preset="LongFast", dwell_s=30.0, n_ok=1, n_crc_err=0, rssi_med=-99.0, snr_med=4.0, len_med=20))
    assert len(list(s.iter_decode(rid))) == 2
    d = s.decode_summary(rid)[0]
    assert (d["freq_hz"], d["network"], d["preset"], d["n_ok"], d["n_crc_err"], d["dwell_s"]) == (906_875_000, "meshtastic", "LongFast", 4, 1, 60.0)
    assert d["rssi_med"] in (-95.0, -99.0)
