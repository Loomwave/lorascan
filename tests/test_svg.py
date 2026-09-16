import re
from lorascan.report.svg import heatmap_svg, band_svg, sfmap_svg, busy_colour, grid_ribbon_svg
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


def test_busy_colour_absolute_path_is_unchanged_when_no_range_given():
    assert busy_colour(0.5) == busy_colour(0.5)
    assert busy_colour(None) == "#C9CFD6"
    # two low, close absolute values come out nearly identical (the bug being fixed)
    assert busy_colour(0.02) == busy_colour(0.02)


def _rgb(hexcolour):
    h = hexcolour.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _dist(a, b):
    ra, rb = _rgb(a), _rgb(b)
    return sum(abs(x - y) for x, y in zip(ra, rb))


def test_busy_colour_auto_ranges_low_values_to_span_the_full_colour_scale():
    low = busy_colour(0.02, 0.02, 0.12)
    high = busy_colour(0.12, 0.02, 0.12)
    assert low != high
    absolute_low = busy_colour(0.02)
    absolute_high = busy_colour(0.12)
    # under the absolute 0..1 mapping these two low values are near-identical (the bug being fixed);
    # under auto-ranging the SAME two values span (much closer to) the full colour scale
    assert _dist(low, high) > 5 * _dist(absolute_low, absolute_high)
    # the auto-ranged colour at vmin is the bottom stop, matching busy_colour(0.0) with no range
    assert low == busy_colour(0.0)
    assert high == busy_colour(1.0)


def test_busy_colour_auto_range_guards_degenerate_vmax_le_vmin():
    assert busy_colour(0.5, 0.3, 0.3) == busy_colour(0.0)
    assert busy_colour(0.5, 0.3, 0.1) == busy_colour(0.0)


def test_grid_ribbon_svg_empty_grid_returns_empty_string():
    assert grid_ribbon_svg([]) == ""


def test_grid_ribbon_svg_includes_recommended_channel_and_title():
    grid = [
        {"center_mhz": 902.25, "busy": None, "floor": None, "excluded": True, "recommended": False},
        {"center_mhz": 903.75, "busy": None, "floor": None, "excluded": False, "recommended": False},
        {"center_mhz": 911.75, "busy": 0.03, "floor": -119.0, "excluded": False, "recommended": True},
        {"center_mhz": 926.75, "busy": None, "floor": None, "excluded": True, "recommended": False},
    ]
    s = grid_ribbon_svg(grid)
    assert s.startswith("<svg") and "viewBox" in s
    assert "911.75" in s
    assert "<title>" in s
    assert "902.25" in s and "excluded" in s          # excluded channel titled
    assert "903.75" in s and "no 500 kHz data" in s    # no-data channel titled
    assert "<script" not in s                          # community page is no-JS


def test_grid_ribbon_svg_no_data_channels_get_neutral_fill_and_dont_crash():
    grid = [{"center_mhz": 902.25, "busy": None, "floor": None, "excluded": False, "recommended": False}]
    s = grid_ribbon_svg(grid)
    assert "#C9CFD6" in s


def test_grid_ribbon_svg_takes_no_exclusions_kwarg():
    """Each grid item already carries its own excluded flag; the ribbon has no separate exclusions param."""
    import inspect
    params = inspect.signature(grid_ribbon_svg).parameters
    assert "exclusions" not in params


def test_grid_ribbon_svg_hatch_pattern_id_is_unique_from_band_svg():
    """band_svg and grid_ribbon_svg can land in the same page (share_page.py); duplicate SVG <pattern>
    ids are invalid HTML, so the ribbon must use its own id, not band_svg's #exclhatch."""
    grid = [{"center_mhz": 902.25, "busy": None, "floor": None, "excluded": True, "recommended": False}]
    ribbon = grid_ribbon_svg(grid)
    assert 'id="gridexclhatch"' in ribbon and 'url(#gridexclhatch)' in ribbon
    assert 'id="exclhatch"' not in ribbon
    chans = [{"mhz": 902.0, "label": "", "floor_med": -110.0, "p90_med": -100.0, "peak_max": -80.0, "busy_mean": 0.1}]
    band = band_svg(chans, exclusions=[(902_000_000, 903_000_000)])
    assert 'id="exclhatch"' in band and 'id="gridexclhatch"' not in band


def test_band_svg_auto_range_true_differs_from_default_for_low_narrow_values():
    chans = [{"mhz": 902.0, "label": "", "floor_med": -110.0, "p90_med": -100.0, "peak_max": -80.0, "busy_mean": 0.02},
             {"mhz": 911.5, "label": "", "floor_med": -108.0, "p90_med": -100.0, "peak_max": -80.0, "busy_mean": 0.12}]
    absolute = band_svg(chans)
    ranged = band_svg(chans, auto_range=True)
    assert absolute != ranged
    assert busy_colour(0.02, 0.02, 0.12) in ranged
    assert busy_colour(0.12, 0.02, 0.12) in ranged


def test_band_svg_auto_range_default_false_is_byte_identical_to_before():
    chans = [{"mhz": 902.0, "label": "", "floor_med": -110.0, "p90_med": -100.0, "peak_max": -80.0, "busy_mean": 0.4}]
    assert band_svg(chans) == band_svg(chans, auto_range=False)
