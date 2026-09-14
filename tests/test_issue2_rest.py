"""Loomwave/lorascan#2 items 1, 2, 4: multi-bw energy in one run, dense whole-band CAD grid, user network table."""
import os
from lorascan import cli
from lorascan.plan.grid import band_grid
from lorascan.plan.quick import quick_plan
from lorascan.plan.survey import survey_plan
from lorascan.store.db import Store
from lorascan import networks as N


def test_quick_and_survey_plans_take_a_bandwidth_list():
    grid = band_grid()
    steps = list(quick_plan(grid, passes=1, dwell_s=0.1, bw_khz=[62, 125]))
    assert len(steps) == 2 * len(grid) + 2 * 14 and {s.bw_khz for s in steps} == {62, 125}
    assert steps[0].freq_hz == steps[1].freq_hz and steps[0].bw_khz != steps[1].bw_khz    # both widths back to back per channel
    it = survey_plan(grid[:3], dwell_s=0.1, revisit_max_s=600, activity={}, bw_khz=[125, 500], clock=lambda: 0.0)
    first = [next(it) for _ in range(6)]
    assert [(s.freq_hz, s.bw_khz) for s in first] == [(grid[0], 125), (grid[0], 500), (grid[1], 125), (grid[1], 500), (grid[2], 125), (grid[2], 500)]


def test_cli_scan_quick_multi_bw_writes_rows_for_each_width(tmp_path):
    db = str(tmp_path / "q.db")
    assert cli.main(["scan", "quick", "--profile", "fake", "--db", db, "--passes", "1", "--dwell", "0.005", "--sample-gap", "0.001", "--bw", "62,125"]) == 0
    s = Store(db); rows = list(s.iter_energy())
    assert len(rows) == 2 * 144 and {r.bw_hz for r in rows} == {62_000, 125_000}


def test_cli_cad_grid_sweeps_the_whole_band(tmp_path):
    db = str(tmp_path / "g.db")
    assert cli.main(["scan", "quick", "--profile", "fake", "--db", db, "--passes", "1", "--dwell", "0.005", "--sample-gap", "0.001",
                     "--cad-grid", "500000", "--sfs", "7,9", "--bws", "125", "--cad-n", "3"]) == 0
    s = Store(db); cads = list(s.iter_cad())
    centres = sorted({c.freq_hz for c in cads if (c.freq_hz - 902_250_000) % 500_000 == 0})   # the grid; the 53rd freq is the false-alarm reference
    assert len(centres) == 52 and centres[0] == 902_250_000 and centres[-1] == 927_750_000     # 52 windows of 500 kHz, centred
    assert len(cads) >= 52 * 2 and {c.sf for c in cads} >= {7, 9}


def test_user_network_table_merges_over_builtin(tmp_path):
    p = str(tmp_path / "networks.yaml")
    with open(p, "w") as f:
        f.write("# network/preset: {sync, sf, bw (kHz), cr, preamble, crc, iq, freqs (MHz, space separated)}\n"
                "fort2/main: {sync: 0x3C, sf: 8, bw: 250, cr: 6, preamble: 12, freqs: 905.0 906.5}\n"
                "fort2/slow: {sync: 0x3C, sf: 11, bw: 125, freqs: 905.0}\n"
                "meshcore/us-narrow: {sync: 0x12, sf: 7, bw: 62, preamble: 32, freqs: 910.525 912.0}\n")   # overrides a built-in preset
    nets = N.load_user_networks(p)
    assert [n.name for n in nets] == ["fort2", "meshcore"]
    fort = nets[0]; assert fort.sync_word == 0x3C and [(q.name, q.sf, q.bw_khz, q.cr, q.preamble, q.freqs_hz) for q in fort.presets] == [
        ("main", 8, 250, 6, 12, (905_000_000, 906_500_000)), ("slow", 11, 125, 5, 8, (905_000_000,))]
    merged = N.merge_networks(N.NETWORKS, nets)
    names = [n.name for n in merged]
    assert names.count("meshcore") == 1 and "fort2" in names
    mc = [n for n in merged if n.name == "meshcore"][0]
    assert {q.name for q in mc.presets} == {"us-narrow", "us-legacy"} and [q for q in mc.presets if q.name == "us-narrow"][0].freqs_hz == (910_525_000, 912_000_000)
    N.set_networks(merged)
    try:
        assert ("fort2", "main") in [(n, q.name) for n, q in N.presets_on(906_500_000)]
    finally:
        N.set_networks(N.BUILTIN_NETWORKS)


def test_cli_networks_flag_and_default_path(tmp_path, monkeypatch):
    p = str(tmp_path / "nets.yaml")
    with open(p, "w") as f:
        f.write("mine/x: {sync: 0x12, sf: 9, bw: 125, freqs: 905.0}\n")
    db = str(tmp_path / "w.db")
    rc = cli.main(["scan", "watch", "--profile", "fake", "--db", db, "--freqs", "905.0", "--cycles", "1", "--dwell", "0.005", "--sample-gap", "0.001",
                   "--sfs", "9", "--bws", "125", "--cad-n", "2", "--decode-dwell", "0.01", "--networks", p])
    assert rc == 0
    s = Store(db)
    assert any(d.network == "mine" and d.preset == "x" for d in s.iter_decode())
    N.set_networks(N.BUILTIN_NETWORKS)
