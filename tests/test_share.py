import json, os
from lorascan.store.db import Store
from lorascan.measure.energy import EnergyRow
from lorascan.measure.cad import CadRow
from lorascan.measure.decode import DecodeRow
from lorascan.share import build_share, coarse_cell, submitter_token, SHARE_FORMAT

def fill(s, rid):
    t0 = 1_700_000_000.0
    for j in range(4):
        s.add_energy(rid, EnergyRow(ts=t0 + j * 900, freq_hz=911_500_000, bw_hz=125000, engine="scan", n=1000, hist=[0]*33, floor_dbm=-110 - j, p50=-105, p90=-90, peak=-60, busy_frac=0.25))
    s.add_cad(rid, CadRow(ts=t0, freq_hz=911_500_000, bw_hz=125000, sf=9, symbols=2, n_cad=50, hits=5, longest_run=2, det_peak=23, det_min=10))
    s.add_decode(rid, DecodeRow(ts=t0, freq_hz=911_500_000, network="loomwave", preset="fleet", dwell_s=12, n_ok=7, n_crc_err=1, rssi_med=-40, snr_med=11, len_med=103))

def test_coarse_cell_rounds_to_a_tenth_of_a_degree():
    assert coarse_cell(34.1234, -84.3789) == (34.1, -84.4) and coarse_cell(34.1234, -84.3789, 1.0) == (34.0, -84.0)
    assert coarse_cell(None, None) is None

def test_build_share_has_only_aggregates_and_the_cell(tmp_path):
    s = Store(str(tmp_path / "t.db")); rid = s.new_run("survey", "nebra-duo-hat", "x"); fill(s, rid)
    doc = build_share(s, cell=(34.1, -84.4), token="abc123", profile_name="nebra-duo-hat", rssi_offset_db=0.0)
    assert doc["format"] == SHARE_FORMAT and doc["cell"] == {"lat": 34.1, "lon": -84.4, "size_deg": 0.1} and doc["submitter"] == "abc123"
    assert doc["calibration"] == "relative (uncalibrated)" and doc["board"] == "nebra-duo-hat"
    e = doc["energy"][0]
    assert e["freq_hz"] == 911_500_000 and e["bw_hz"] == 125000 and e["hours"] >= 0.0 and "floor_p10_med" in e and "busy_mean" in e and "hist" not in e
    assert doc["cad"][0]["sf"] == 9 and abs(doc["cad"][0]["hit_rate"] - 0.1) < 1e-9
    assert doc["decode"][0]["network"] == "loomwave" and doc["decode"][0]["n_ok"] == 7 and "payload" not in json.dumps(doc)
    assert "lat" not in json.dumps(doc["energy"])       # no location outside the cell block

def test_submitter_token_is_created_once(tmp_path):
    p = str(tmp_path / "token")
    a = submitter_token(p); b = submitter_token(p)
    assert a == b and len(a) >= 16 and os.path.exists(p)
