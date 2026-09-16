from lorascan.report.slot_recommend import recommend_slots, grid_centers, grid_width_rows, recommend_grid_slots


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


# --- grid-aligned (.250/.750 MeshCore-500 grid) ---------------------------------------------------------

def test_grid_centers_default_spans_902_25_to_927_75():
    centers = grid_centers()
    assert centers[0] == 902_250_000
    assert centers[-1] == 927_750_000
    assert len(centers) == 52
    assert all(b - a == 500_000 for a, b in zip(centers, centers[1:]))
    for c in centers:
        assert c - 250_000 >= 902_000_000
        assert c + 250_000 <= 928_000_000


def test_grid_width_rows_aggregates_worst_case_and_omits_empty_centers():
    # two real 500 kHz rows both inside the 902_250_000 window (902_000_000..902_500_000)
    by_bw = [_c(902_400_000, 500_000, -119.0, 0.05), _c(902_450_000, 500_000, -110.0, 0.20)]
    centers = grid_centers()
    rows = grid_width_rows(by_bw, centers, 500_000)
    assert len(rows) == 1  # every other center has no in-window width-W data -> omitted
    row = rows[0]
    assert row["freq_hz"] == 902_250_000
    assert row["bw_hz"] == 500_000
    assert row["busy_mean"] == 0.20  # max of 0.05, 0.20
    assert row["floor_med"] == -110.0  # worst (highest) of -119.0, -110.0
    assert row["p90_med"] == -100.0  # max of -109.0, -100.0
    assert row["peak_max"] == -80.0  # max of -89.0, -80.0
    assert row["n_rows"] == 2
    assert row["n_samples"] == 20


def test_grid_width_rows_missing_optional_keys_default_to_zero():
    bare = {"freq_hz": 902_400_000, "bw_hz": 500_000, "floor_med": -119.0, "busy_mean": 0.05}
    rows = grid_width_rows([bare], grid_centers(), 500_000)
    assert len(rows) == 1
    assert rows[0]["p90_med"] == 0 and rows[0]["peak_max"] == 0
    assert rows[0]["n_rows"] == 0 and rows[0]["n_samples"] == 0


def test_recommend_grid_slots_picks_clean_grid_channel():
    by_bw = [_c(910_250_000, 500_000, -119.0, 0.02), _c(921_250_000, 500_000, -119.0, 0.60)]
    r = recommend_grid_slots(by_bw, [], [])
    assert r["grid"] is True
    assert r["base_hz"] == 902_250_000
    assert r["recommended"]["center_hz"] == 910_250_000
    assert r["recommended"]["center_mhz"] == 910.25
    frac = round(r["recommended"]["center_mhz"] % 0.5, 3)
    assert frac == 0.25  # on the .250/.750 grid


def test_recommend_grid_slots_no_width_data_returns_none():
    by_bw = [_c(910_000_000, 125_000, -110.0, 0.1)]  # no 500 kHz rows anywhere
    r = recommend_grid_slots(by_bw, [], [])
    assert r["recommended"] is None
    assert r["windows"] == []
    assert r["grid"] is True
    assert r["base_hz"] == 902_250_000


def test_recommend_grid_slots_narrow_carrier_penalises_its_grid_channel():
    # 910.25 window (910.0-910.5) is clean at 500 kHz but a loud narrow carrier sits inside it
    by_bw = [_c(910_250_000, 500_000, -119.0, 0.02), _c(915_250_000, 500_000, -119.0, 0.02),
             _c(910_300_000, 62_500, -70.0, 0.02)]  # loud carrier inside 910.25's window
    r = recommend_grid_slots(by_bw, [], [])
    w910 = [w for w in r["windows"] if w["center_hz"] == 910_250_000][0]
    assert w910["carrier_pen"] > 0
    assert r["recommended"]["center_hz"] == 915_250_000  # the carrier pushed 910.25 down
