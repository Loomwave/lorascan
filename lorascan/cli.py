"""lorascan command line: probe | selftest | scan quick|survey | report | export."""
from __future__ import annotations
import argparse
import csv
import signal
import sys
import time

from . import __version__
from .profile import load_profile, BoardProfile
from .hal import open_hal, HalError
from .radio.sx126x import Sx126x, SxError, DeviceError
from .measure.energy import polled_energy, EnergyRow
from .plan.grid import band_grid, KNOWN_CHANNELS, label_for
from .plan.quick import quick_plan
from .plan.survey import survey_plan
from .store.db import Store
from .report.html import render_report


def _profile(name: str) -> BoardProfile:
    if name == "fake":
        return BoardProfile(name="fake", bus_type="fake", bus_dev="", bus_hz=0,
                            pins={"nss": "kernel", "reset": 22, "busy": 23, "dio1": 24, "rxen": None, "txen": None})
    return load_profile(name)


def _duration(s: str | None) -> float | None:
    if not s:
        return None
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return float(s[:-1]) * units[s[-1]] if s[-1] in units else float(s)


def _open_radio(prof: BoardProfile, freq_hz: int = 911_500_000):
    hal = open_hal(prof)
    radio = Sx126x(hal, prof)
    radio.init(freq_hz)
    return hal, radio


class _Stop(Exception):
    pass


def cmd_probe(a) -> int:
    prof = _profile(a.profile)
    hal = open_hal(prof)
    r = Sx126x(hal, prof)
    p = r.probe_bytes()
    print(f"[probe] profile={prof.name} bus={prof.bus_type}:{prof.bus_dev} status=0x{p['status']:02X} mode={p['mode']} sync=0x{p['sync'][0]:02X},0x{p['sync'][1]:02X} -> {p['verdict']}")
    hal.close()
    return 0 if p["verdict"] == "GOOD" else 2


def cmd_selftest(a) -> int:
    prof = _profile(a.profile)
    hal, radio = _open_radio(prof)
    ok = True
    try:
        errs = radio.device_errors()
        print(f"[selftest] init OK, device errors 0x{errs:04X}, chip status 0x{radio.chip_status():02X}")
        for f in (902_500_000, 911_500_000):
            row = polled_energy(radio, f, 125, a.dwell, sample_gap_s=a.sample_gap)
            good = row.n >= 10 and -126 < row.floor_dbm < -1
            ok &= good
            print(f"[selftest] {f/1e6:.3f} MHz: n={row.n} discarded={row.discarded} floor={row.floor_dbm:.1f} p90={row.p90:.1f} peak={row.peak:.1f} busy={row.busy_frac*100:.1f}% {'ok' if good else 'BAD'}")
    finally:
        hal.close()
    print("[selftest] PASS" if ok else "[selftest] FAIL")
    return 0 if ok else 2


def _run_scan(a, kind: str) -> int:
    prof = _profile(a.profile)
    store = Store(a.db)
    grid = band_grid(a.start, a.stop, a.step)
    fake_clock = [0.0]
    clock = (lambda: fake_clock[0]) if a.fake_clock else time.monotonic
    run_id = store.new_run(kind, prof.name, a.note)
    hal, radio = _open_radio(prof, grid[0])
    if a.fake_clock:
        hal.sleep = lambda s: fake_clock.__setitem__(0, fake_clock[0] + s)  # type: ignore[attr-defined]
    activity: dict[int, float] = {}
    if kind == "quick":
        steps = quick_plan(grid, passes=a.passes, dwell_s=a.dwell, bw_khz=a.bw)
    else:
        steps = survey_plan(grid, dwell_s=a.dwell, revisit_max_s=a.revisit, activity=activity, bw_khz=a.bw, clock=clock)
    limit = _duration(a.duration)
    t_start = clock()
    stopped = {"flag": False}

    def _sig(*_):
        stopped["flag"] = True
    old = signal.signal(signal.SIGINT, _sig); old_t = signal.signal(signal.SIGTERM, _sig)
    n, failures = 0, 0
    store.add_event(run_id, "start", f"{kind} grid={len(grid)} dwell={a.dwell}")
    try:
        for step in steps:
            if stopped["flag"] or (limit is not None and clock() - t_start >= limit):
                break
            try:
                row = polled_energy(radio, step.freq_hz, step.bw_khz, step.dwell_s, clock=clock, sample_gap_s=a.sample_gap,
                                    busy_t_db=a.busy_t, offset_dbm=prof.scan_offset_dbm,
                                    ts=(time.time() if not a.fake_clock else 1_700_000_000.0 + clock()))
            except SxError as e:
                failures += 1
                store.add_event(run_id, "radio_error", f"{step.freq_hz} {e}")
                print(f"[scan] radio error at {step.freq_hz/1e6:.3f} MHz: {e}; re-initialising", file=sys.stderr)
                try:
                    radio.init(step.freq_hz)
                except SxError as e2:
                    store.add_event(run_id, "radio_reinit_failed", str(e2))
                    print(f"[scan] re-init failed: {e2}; stopping", file=sys.stderr)
                    break
                continue
            store.add_energy(run_id, row)
            activity[step.freq_hz] = 0.7 * activity.get(step.freq_hz, 0.0) + 0.3 * row.busy_frac
            n += 1
            if a.verbose or n % 50 == 0:
                print(f"[scan] {n:5d} {row.freq_hz/1e6:8.3f} MHz n={row.n:4d} floor={row.floor_dbm:6.1f} p90={row.p90:6.1f} peak={row.peak:6.1f} busy={row.busy_frac*100:5.1f}% {label_for(row.freq_hz)}")
    finally:
        signal.signal(signal.SIGINT, old); signal.signal(signal.SIGTERM, old_t)
        store.add_event(run_id, "stop", f"rows={n} failures={failures}")
        try:
            radio.standby()
        except Exception:
            pass
        hal.close()
        store.close()
    print(f"[scan] run #{run_id} {kind}: {n} rows, {failures} radio errors, db={a.db}")
    return 0


def cmd_report(a) -> int:
    store = Store(a.db)
    prof_offset = a.rssi_offset
    if prof_offset is None:
        runs = store.runs()
        try:
            prof_offset = load_profile(runs[-1]["profile"]).rssi_offset_db if runs and runs[-1]["profile"] not in ("fake",) else 0.0
        except FileNotFoundError:
            prof_offset = 0.0
    render_report(store, a.out, title=a.title, run_id=a.run, bucket_s=a.bucket, rssi_offset_db=prof_offset)
    print(f"[report] wrote {a.out}")
    return 0


def cmd_export(a) -> int:
    store = Store(a.db)
    with open(a.csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ts", "freq_hz", "bw_hz", "engine", "n", "floor_dbm", "p50", "p90", "peak", "busy_frac", "discarded", "hist"])
        for r in store.iter_energy(a.run):
            w.writerow([r.ts, r.freq_hz, r.bw_hz, r.engine, r.n, r.floor_dbm, r.p50, r.p90, r.peak, r.busy_frac, r.discarded, " ".join(map(str, r.hist))])
    print(f"[export] wrote {a.csv}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lorascan", description="LoRa-chipset band scanner for 902-928 MHz (receive-only)")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def radio_args(sp):
        sp.add_argument("--profile", default="generic-spidev", help="board profile name or path ('fake' = simulated radio)")
        sp.add_argument("--dwell", type=float, default=0.4, help="seconds per channel visit")
        sp.add_argument("--sample-gap", type=float, default=0.0007, help="seconds between RSSI polls")

    sp = sub.add_parser("probe", help="first-light SPI check (reset, GetStatus, sync-word read)"); sp.add_argument("--profile", default="generic-spidev"); sp.set_defaults(fn=cmd_probe)
    sp = sub.add_parser("selftest", help="init + two short energy reads"); radio_args(sp); sp.set_defaults(fn=cmd_selftest)
    sc = sub.add_parser("scan", help="run a scan plan"); ssub = sc.add_subparsers(dest="plan", required=True)
    for kind in ("quick", "survey"):
        sp = ssub.add_parser(kind); radio_args(sp)
        sp.add_argument("--db", default="lorascan.db"); sp.add_argument("--note", default="")
        sp.add_argument("--start", type=int, default=902_000_000); sp.add_argument("--stop", type=int, default=928_000_000); sp.add_argument("--step", type=int, default=200_000)
        sp.add_argument("--bw", type=int, default=125, help="measurement bandwidth kHz (62/125/250/500)")
        sp.add_argument("--busy-t", type=float, default=8.0, help="busy threshold dB above floor")
        sp.add_argument("--duration", default=None, help="stop after e.g. 15m, 2h, 3d")
        sp.add_argument("--fake-clock", action="store_true", help=argparse.SUPPRESS)
        sp.add_argument("-v", "--verbose", action="store_true")
        if kind == "quick":
            sp.add_argument("--passes", type=int, default=2)
        else:
            sp.add_argument("--revisit", type=float, default=600.0, help="max seconds between visits of any channel")
        sp.set_defaults(fn=lambda a, k=kind: _run_scan(a, k))
    sp = sub.add_parser("report"); sp.add_argument("--db", default="lorascan.db"); sp.add_argument("--out", default="lorascan-report.html"); sp.add_argument("--title", default="lorascan report")
    sp.add_argument("--run", type=int, default=None); sp.add_argument("--bucket", type=int, default=60); sp.add_argument("--rssi-offset", type=float, default=None); sp.set_defaults(fn=cmd_report)
    sp = sub.add_parser("export"); sp.add_argument("--db", default="lorascan.db"); sp.add_argument("--csv", required=True); sp.add_argument("--run", type=int, default=None); sp.set_defaults(fn=cmd_export)
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return int(a.fn(a))
    except (HalError, DeviceError, SxError, FileNotFoundError) as e:
        print(f"lorascan: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
