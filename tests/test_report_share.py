"""Loomwave/lorascan#3: render from a share document (no DB) and write standalone SVG files."""
import os
from lorascan import cli
from lorascan.share import build_share
from lorascan.report.html import build_data_from_share, render_report_from_share, render_report, write_svgs
from lorascan.store.db import Store
from lorascan.measure.energy import EnergyRow
from lorascan.measure.cad import CadRow


def _store(tmp_path, hours=26):
    s = Store(str(tmp_path / "s.db")); rid = s.new_run("survey", "fake", "")
    t0 = 1_757_800_000.0
    for h in range(hours):
        for i, f in enumerate((902_000_000, 911_500_000, 921_000_000)):
            s.add_energy(rid, EnergyRow(ts=t0 + h * 3600, freq_hz=f, bw_hz=125_000, engine="poll", n=100, floor_dbm=-110 + i, p50=-105, p90=-100 + 10 * i, peak=-80, busy_frac=0.1 * i))
    s.add_cad(rid, CadRow(ts=t0, freq_hz=911_500_000, bw_hz=125_000, sf=9, symbols=2, n_cad=50, hits=20, longest_run=4, det_peak=23, det_min=10))
    return s


def test_build_data_from_share_matches_the_report_shape(tmp_path):
    s = _store(tmp_path)
    doc = build_share(s, (34.1, -84.4), "tok", "fake", granularity="hour")
    d = build_data_from_share(doc)
    assert [c["mhz"] for c in d["channels"]] == [902.0, 911.5, 921.0] and d["channels"][1]["label"] == "Loomwave fleet"
    assert d["heat"]["bucket_s"] == 3600 and len(d["heat"]["buckets"]) == 26 and len(d["heat"]["busy"]) == 3
    assert d["channels"][2]["busy_mean"] == 0.2 and d["channels"][0]["floor_med"] == -110
    assert d["sfmap"]["sfs"] == [9] and d["sfmap"]["z"][0][0] == 0.4
    assert d["quietest"][0]["mhz"] == 911.5 and d["span_s"] == 25 * 3600 and d["card"] is None     # 902.0 is quieter but excluded (#9)
    assert d["quietest"][-1]["mhz"] == 902.0 and d["quietest"][-1]["excluded"] is True
    day = build_data_from_share(build_share(s, None, "tok", "fake", granularity="day"))
    assert day["heat"]["bucket_s"] == 86400 and any(v is not None for r in day["when"] for v in r)


def test_render_from_share_and_svg_files(tmp_path):
    s = _store(tmp_path)
    doc = build_share(s, None, "tok", "fake", granularity="hour")
    out = str(tmp_path / "r.html")
    html = render_report_from_share(doc, out, title="site X")
    assert "site X" in html and html.count("<svg") >= 3 and "from share document" in html
    paths = write_svgs(build_data_from_share(doc), str(tmp_path / "svg"))
    names = sorted(os.path.basename(p) for p in paths)
    assert names == ["band.svg", "heatmap.svg", "sfmap.svg"]          # hour docs carry no when-matrix
    assert open(paths[0]).read().startswith("<?xml") and "<svg" in open(paths[0]).read()
    day = build_share(s, None, "tok", "fake", granularity="day")
    assert "when.svg" in {os.path.basename(p) for p in write_svgs(build_data_from_share(day), str(tmp_path / "svg2"))}


def test_cli_report_from_share_and_svg_dir(tmp_path):
    s = _store(tmp_path); s.close(); db = str(tmp_path / "s.db")
    share = str(tmp_path / "share.json"); out = str(tmp_path / "r.html"); svg = str(tmp_path / "figs")
    assert cli.main(["share", "--db", db, "--out", share, "--token-path", str(tmp_path / "tok"), "--dry-run"]) == 0
    assert cli.main(["report", "--from-share", share, "--out", out, "--svg", svg]) == 0
    assert os.path.exists(out) and os.path.exists(os.path.join(svg, "heatmap.svg"))
    assert cli.main(["report", "--db", db, "--out", out, "--svg", svg]) == 0 and os.path.exists(os.path.join(svg, "band.svg"))
