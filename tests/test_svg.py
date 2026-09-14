import re
from lorascan.report.svg import heatmap_svg, band_svg, sfmap_svg
from lorascan.store.db import Store
from lorascan.measure.energy import EnergyRow
from lorascan.report.html import render_report

def test_heatmap_svg_draws_one_rect_per_cell_and_labels_axes():
    data = {"freqs_mhz": [902.0, 902.2, 911.5], "buckets": ["2026-09-14T13:00:00Z", "2026-09-14T13:01:00Z"], "busy": [[0.0, 0.5], [None, 1.0], [0.2, 0.2]], "bucket_s": 60}
    s = heatmap_svg(data, "busy")
    assert s.startswith("<svg") and s.count("<rect") >= 5 and "911.5" in s and "13:00" in s and "viewBox" in s

def test_band_svg_bars_floor_to_peak_with_busy_colour():
    chans = [{"mhz": 902.0, "label": "", "floor_med": -110.0, "p90_med": -100.0, "peak_max": -80.0, "busy_mean": 0.0},
             {"mhz": 911.5, "label": "Loomwave fleet", "floor_med": -108.0, "p90_med": -60.0, "peak_max": -40.0, "busy_mean": 0.4}]
    s = band_svg(chans)
    assert s.count("<rect") >= 2 and "Loomwave fleet" in s and "-110" in s and "dBm" in s

def test_sfmap_svg_handles_empty():
    assert sfmap_svg({"freqs_mhz": [], "sfs": [], "z": []}) == ""

def test_report_embeds_static_svg_and_guards_plotly(tmp_path):
    s = Store(str(tmp_path / "t.db")); rid = s.new_run("quick", "x", "")
    for j in range(3):
        s.add_energy(rid, EnergyRow(ts=1e9 + 60 * j, freq_hz=902_000_000, bw_hz=125000, engine="poll", n=10, hist=[0]*33, floor_dbm=-110, p50=-105, p90=-100, peak=-90, busy_frac=0.1 * j))
    html = render_report(s, str(tmp_path / "r.html"))
    assert 'class="static"' in html and html.count("<svg") >= 2
    assert "typeof Plotly" in html and "cdnjs.cloudflare.com/ajax/libs/plotly.js" in html


def test_heatmap_svg_caps_columns_by_merging_buckets():
    from lorascan.report.svg import heatmap_svg, MAX_COLS
    n = 1200
    data = {"freqs_mhz": [902.0, 902.2], "buckets": ["2026-09-14T%02d:%02d:00Z" % (i // 60, i % 60) for i in range(n)],
            "busy": [[(i % 2) * 1.0 for i in range(n)], [None] * n], "bucket_s": 60}
    s = heatmap_svg(data, "busy")
    assert s.count("<rect") <= 2 * MAX_COLS + 40           # cells + colour bar
    assert "#" in s and "merged" in s                        # legend states the merge
    # 1200 -> 240 columns = 5 buckets per column; windows of the alternating row are 0,1,0,1,0 or 1,0,1,0,1
    from lorascan.report.svg import busy_colour, merge_columns
    nb, nz, per = merge_columns(data["buckets"], data["busy"])
    assert per == 5 and len(nb) == 240
    expected = sorted({sum((i % 2) for i in range(j, j + per)) / per for j in range(0, n, per)})
    assert expected == [0.4, 0.6] and set(nz[0]) == {0.4, 0.6} and nz[1] == [None] * 240
    assert busy_colour(0.4) in s and busy_colour(0.6) in s
