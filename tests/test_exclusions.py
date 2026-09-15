"""Loomwave/lorascan#9 (pinztrek): band edges and the 33 cm repeater segments are not options for a new
deployment — collect data there, but never recommend them; shade them in the visuals (Matt)."""
import csv, json, re
from lorascan import cli
from lorascan.exclusions import DEFAULT_EXCLUSIONS, parse_exclusions, excluded, tag_channels
from lorascan.report.slots import slot_view
from lorascan.report.html import build_data, render_from_data
from lorascan.report.svg import band_svg, heatmap_svg
from lorascan.store.db import Store
from lorascan.measure.energy import EnergyRow


def test_defaults_and_parsing():
    assert DEFAULT_EXCLUSIONS == [(902_000_000, 903_250_000), (926_750_000, 928_000_000)]
    assert parse_exclusions("902.0-903.25,926.75-928.0") == DEFAULT_EXCLUSIONS
    assert parse_exclusions("910-911") == [(910_000_000, 911_000_000)] and parse_exclusions("") == [] and parse_exclusions(None) == DEFAULT_EXCLUSIONS
    assert excluded(902_400_000, DEFAULT_EXCLUSIONS) and excluded(926_800_000, DEFAULT_EXCLUSIONS) and not excluded(903_250_000, DEFAULT_EXCLUSIONS) and not excluded(915_000_000, DEFAULT_EXCLUSIONS)


def _chan(mhz, busy, floor=-110.0):
    return {"freq_hz": int(round(mhz * 1e6)), "mhz": mhz, "label": "", "floor_med": floor, "p90_med": floor + 10, "peak_max": -60.0, "busy_mean": busy, "n_rows": 5, "n_samples": 500}


def test_quietest_lists_viable_channels_first_and_marks_excluded(tmp_path):
    s = Store(str(tmp_path / "q.db")); rid = s.new_run("quick", "fake", "")
    for mhz, busy in ((902.2, 0.01), (902.4, 0.02), (915.0, 0.05), (921.0, 0.03), (927.0, 0.0)):
        s.add_energy(rid, EnergyRow(ts=1e9, freq_hz=int(round(mhz * 1e6)), bw_hz=125_000, engine="poll", n=10, floor_dbm=-110, p50=-100, p90=-95, peak=-80, busy_frac=busy))
    d = build_data(s)
    assert [c["mhz"] for c in d["quietest"][:2]] == [921.0, 915.0]                    # viable first, in quietness order
    assert [c["excluded"] for c in d["quietest"]] == [False, False, True, True, True]
    assert d["exclusions"] == [[902.0, 903.25], [926.75, 928.0]]
    d2 = build_data(s, exclusions=[])
    assert d2["quietest"][0]["mhz"] == 927.0 and d2["exclusions"] == []


def test_slot_view_ranks_viable_windows_first():
    chans = [_chan(902.2, 0.0), _chan(903.5, 0.2), _chan(927.2, 0.0), _chan(915.2, 0.1)]
    slots = slot_view(chans, [], [], slot_hz=500_000, exclusions=DEFAULT_EXCLUSIONS)
    assert [w["start_mhz"] for w in slots] == [915.0, 903.5, 902.0, 927.0]                # excluded windows sink, keep their score
    assert [w["excluded"] for w in slots] == [False, False, True, True]
    slots = slot_view(chans, [], [], slot_hz=500_000, exclusions=[(903_000_000, 904_000_000)])
    assert slots[-1]["start_mhz"] == 903.5 and slots[-1]["excluded"] is True                  # a window that OVERLAPS a zone is excluded


def test_svgs_shade_the_zones():
    chans = [_chan(902.2, 0.0), _chan(915.2, 0.1), _chan(927.2, 0.0)]
    b = band_svg(chans, exclusions=DEFAULT_EXCLUSIONS)
    assert b.count('class="excl"') == 2 and "not an option" in b
    heat = {"freqs_mhz": [902.2, 915.2, 927.2], "buckets": ["2026-09-15T00:00:00Z", "2026-09-15T00:01:00Z"], "busy": [[0, 0], [0.1, 0.1], [0, 0]], "bucket_s": 60}
    h = heatmap_svg(heat, "busy", exclusions=DEFAULT_EXCLUSIONS)
    assert h.count('class="excl"') == 2
    assert 'class="excl"' not in band_svg(chans, exclusions=[])


def test_cli_report_and_export_honour_exclude_flags(tmp_path):
    s = Store(str(tmp_path / "e.db")); rid = s.new_run("quick", "fake", "")
    for mhz, busy in ((902.2, 0.0), (915.0, 0.1), (927.2, 0.0)):
        s.add_energy(rid, EnergyRow(ts=1e9, freq_hz=int(round(mhz * 1e6)), bw_hz=125_000, engine="poll", n=10, floor_dbm=-110, p50=-100, p90=-95, peak=-80, busy_frac=busy))
    s.close(); db = str(tmp_path / "e.db"); out = str(tmp_path / "r.html")
    assert cli.main(["report", "--db", db, "--out", out, "--slot", "500000"]) == 0
    html = open(out).read()
    assert "excluded" in html and "903.250" in html and 'class="excl"' in html
    dj = json.loads(re.search(r'<script id="lorascan-data" type="application/json">(.*?)</script>', html, re.S).group(1).replace("<\\/", "</"))
    assert dj["quietest"][0]["mhz"] == 915.0 and dj["slots"][0]["start_mhz"] == 915.0
    assert cli.main(["report", "--db", db, "--out", out, "--no-exclude"]) == 0
    assert 'class="excl"' not in open(out).read()
    assert cli.main(["report", "--db", db, "--out", out, "--exclude", "914-916"]) == 0
    dj = json.loads(re.search(r'<script id="lorascan-data" type="application/json">(.*?)</script>', open(out).read(), re.S).group(1).replace("<\\/", "</"))
    assert dj["quietest"][-1]["mhz"] == 915.0 and dj["quietest"][-1]["excluded"] is True
    assert cli.main(["export", "--db", db, "--csv", str(tmp_path / "s.csv"), "--table", "slots", "--slot", "500000"]) == 0
    rows = list(csv.DictReader(open(str(tmp_path / "s.csv"))))
    assert rows[0]["start_mhz"] == "915.000" and rows[0]["excluded"] == "False" and rows[-1]["excluded"] == "True"
