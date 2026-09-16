from lorascan.report.slot_recommend import recommend_slots


def _c(freq, bw, floor, busy):
    return {"freq_hz": freq, "bw_hz": bw, "floor_med": floor, "p90_med": floor+10,
            "peak_max": floor+30, "busy_mean": busy, "n_rows": 1, "n_samples": 10}


def test_quiet_500k_window_is_recommended():
    # two 500 kHz-measured centers; 910.0 quiet, 921.0 busy
    by_bw = [_c(910_000_000, 500_000, -119.0, 0.02), _c(921_000_000, 500_000, -119.0, 0.60),
             _c(910_000_000, 125_000, -119.0, 0.02), _c(921_000_000, 125_000, -119.0, 0.60)]
    r = recommend_slots(by_bw, [], [], width_hz=500_000)
    assert r["recommended"]["center_hz"] == 910_000_000
    assert r["windows"][0]["center_hz"] == 910_000_000  # quietest first
    assert r["recommended"]["busy_w"] == 0.02


def test_narrowband_carrier_in_window_penalises_it():
    # 908.0 is quiet at 500 kHz but a narrow carrier at 908.1 raises the floor inside the window
    by_bw = [_c(908_000_000, 500_000, -119.0, 0.02), _c(912_000_000, 500_000, -118.0, 0.03),
             _c(908_100_000, 62_500, -70.0, 0.02),   # loud carrier inside 908.0's window
             _c(912_000_000, 62_500, -119.0, 0.03)]
    r = recommend_slots(by_bw, [], [], width_hz=500_000)
    w908 = [w for w in r["windows"] if w["center_hz"] == 908_000_000][0]
    assert w908["carrier_pen"] > 0
    assert r["recommended"]["center_hz"] == 912_000_000  # the carrier pushed 908 down


def test_cad_and_decodes_in_window_raise_score():
    by_bw = [_c(909_000_000, 500_000, -119.0, 0.02), _c(913_000_000, 500_000, -119.0, 0.02)]
    cad = [{"freq_hz": 909_100_000, "sf": 9, "bw_hz": 125_000, "hit_rate": 0.4}]
    dec = [{"freq_hz": 909_050_000, "network": "meshcore", "preset": "us", "n_ok": 30}]
    r = recommend_slots(by_bw, cad, dec, width_hz=500_000)
    assert r["recommended"]["center_hz"] == 913_000_000  # 909 has CAD+decodes
    w909 = [w for w in r["windows"] if w["center_hz"] == 909_000_000][0]
    assert w909["cad_hit_max"] == 0.4 and w909["decoded_frames"] == 30


def test_excluded_window_never_recommended_but_listed():
    # 902.5 center → window 902.25–902.75 overlaps the 902.000–903.250 exclusion zone
    by_bw = [_c(902_500_000, 500_000, -119.0, 0.0), _c(915_000_000, 500_000, -110.0, 0.30)]
    r = recommend_slots(by_bw, [], [], width_hz=500_000)
    w902 = [w for w in r["windows"] if w["center_hz"] == 902_500_000][0]
    assert w902["excluded"] is True
    assert r["recommended"]["center_hz"] == 915_000_000  # excluded skipped even though quieter


def test_no_width_rows_returns_none():
    by_bw = [_c(915_000_000, 125_000, -110.0, 0.1)]  # no 500 kHz rows
    r = recommend_slots(by_bw, [], [], width_hz=500_000)
    assert r["recommended"] is None and r["windows"] == []
