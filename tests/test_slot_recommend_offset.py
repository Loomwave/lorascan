"""Loomwave/lorascan#13: on a calibrated board the recommendation's floor must read in the same
units as the quietest-channels table (raw floor + rssi_offset_db), while the PICK stays identical."""
from lorascan.report.slot_recommend import recommend_slots, recommend_grid_slots


def _c(freq, bw, floor, busy):
    return {"freq_hz": freq, "bw_hz": bw, "floor_med": floor, "p90_med": floor + 10,
            "peak_max": floor + 30, "busy_mean": busy, "n_rows": 1, "n_samples": 10}


def _by_bw():
    return [_c(910_000_000, 500_000, -119.0, 0.02), _c(921_000_000, 500_000, -119.0, 0.60),
            _c(910_000_000, 125_000, -119.0, 0.02), _c(921_000_000, 125_000, -119.0, 0.60)]


def test_calibrated_floor_is_offset_in_the_recommendation():
    r = recommend_slots(_by_bw(), [], [], width_hz=500_000, rssi_offset_db=-4.5)
    rec = r["recommended"]
    assert rec["floor_w"] == -123.5           # -119.0 + (-4.5), same as the quietest table shows
    assert rec["peak_w"] == -93.5             # -89.0 + (-4.5)
    assert "floor -124 dBm" in rec["why"]     # the terminal `why` line agrees with the field


def test_offset_does_not_change_the_pick_or_the_score():
    raw = recommend_slots(_by_bw(), [], [], width_hz=500_000)
    cal = recommend_slots(_by_bw(), [], [], width_hz=500_000, rssi_offset_db=-4.5)
    assert cal["recommended"]["center_hz"] == raw["recommended"]["center_hz"]
    assert [w["center_hz"] for w in cal["windows"]] == [w["center_hz"] for w in raw["windows"]]
    assert [w["score"] for w in cal["windows"]] == [w["score"] for w in raw["windows"]]
    assert [w["carrier_pen"] for w in cal["windows"]] == [w["carrier_pen"] for w in raw["windows"]]


def test_default_offset_leaves_floors_raw():
    r = recommend_slots(_by_bw(), [], [], width_hz=500_000)
    assert r["recommended"]["floor_w"] == -119.0


def test_grid_recommender_takes_the_offset_too():
    by_bw = [_c(910_250_000, 500_000, -119.0, 0.02), _c(921_250_000, 500_000, -119.0, 0.60)]
    r = recommend_grid_slots(by_bw, [], [], width_hz=500_000, rssi_offset_db=-4.5)
    assert r["grid"] is True
    assert r["recommended"]["floor_w"] == -123.5
