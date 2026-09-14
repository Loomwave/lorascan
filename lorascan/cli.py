"""lorascan command line: probe | selftest | scan quick|survey | report | export."""
from __future__ import annotations
import argparse
import csv
import json
import os
import signal
import sys
import time

from . import __version__
from .profile import load_profile, BoardProfile, dump_profile
import dataclasses
from .hal import open_hal, HalError
from .hal.lock import DeviceBusy
from .radio.sx126x import Sx126x, SxError, DeviceError
from .measure.energy import polled_energy, EnergyRow
from .radio.scanpatch import upload_patch, scan_energy, version_string, ScanError
from .plan.grid import band_grid, KNOWN_CHANNELS, label_for
from .plan.quick import quick_plan
from .plan.survey import survey_plan, Step
from .plan.watch import watch_plan
from .plan.candidate import candidate_plan, parse_candidates
from .measure.cad import cad_sweep
from .measure.decode import decode_dwell
from .networks import NETWORKS, presets_on
from .store.db import Store
from .report.html import render_report
from .share import build_share, write_share, coarse_cell, submitter_token, DEFAULT_CELL_DEG, SHARE_FORMAT, choose_by_budget, parse_budget, upload_share, gzip_bytes


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
        if getattr(a, "engine", "poll") == "scan":
            try:
                upload_patch(radio)
                row = scan_energy(radio, 915_000_000, 125, nb_scan=2048, offset_dbm=prof.scan_offset_dbm)
                print(f"[selftest] scan engine: patch uploaded (version {version_string(radio)!r}), 915.000 MHz histogram of {row.n} samples, floor {row.floor_dbm:.0f} p90 {row.p90:.0f} peak {row.peak:.0f} dBm")
            except (ScanError, SxError) as e:
                print(f"[selftest] scan engine unavailable on this radio ({e}); the polled engine still works", file=sys.stderr)
            radio.init(915_000_000)
        for f in (902_500_000, 911_500_000):
            row = polled_energy(radio, f, 125, a.dwell, sample_gap_s=a.sample_gap)
            good = row.n >= 10 and -126 < row.floor_dbm < -1
            ok &= good
            print(f"[selftest] {f/1e6:.3f} MHz: n={row.n} discarded={row.discarded} floor={row.floor_dbm:.1f} p90={row.p90:.1f} peak={row.peak:.1f} busy={row.busy_frac*100:.1f}% {'ok' if good else 'BAD'}")
    finally:
        hal.close()
    print("[selftest] PASS" if ok else "[selftest] FAIL")
    return 0 if ok else 2


def _cad_steps(freqs, sfs=(7, 9, 11), bws=(125, 250), dwell_s=0.0):
    for f in freqs:
        for sf in sfs:
            for bw in bws:
                yield Step(f, bw, dwell_s, "cad", sf=sf)


def _run_scan(a, kind: str) -> int:
    prof = _profile(a.profile)
    store = Store(a.db)
    if prof.bus_type == "fake":
        a.fake_clock = True          # a simulated radio never sleeps; drive its time from a fake clock
    fake_clock = [0.0]
    clock = (lambda: fake_clock[0]) if a.fake_clock else time.monotonic
    run_id = store.new_run(kind, prof.name, a.note)
    mq = None
    if getattr(a, "mqtt", None):
        from .mqtt import MqttPublisher, parse_mqtt_url
        mq = MqttPublisher(parse_mqtt_url(a.mqtt))          # connects now: a bad broker fails before the radio is opened
        mq.publish_status({"run_id": run_id, "kind": kind, "profile": prof.name, "state": "running"})
    activity: dict[int, float] = {}
    cad_sfs = [int(x) for x in a.sfs.split(",")] if getattr(a, "sfs", None) else [7, 9, 11]
    cad_bws = [int(x) for x in a.bws.split(",")] if getattr(a, "bws", None) else [125, 250]
    if kind == "quick":
        grid = band_grid(a.start, a.stop, a.step)
        base = quick_plan(grid, passes=a.passes, dwell_s=a.dwell, bw_khz=a.bw)
        if a.cad:
            def _quick_with_cad():
                hot = set()
                for st in base:
                    yield st
                    if activity.get(st.freq_hz, 0.0) > 0.05:
                        hot.add(st.freq_hz)
                for st in _cad_steps(sorted(hot | {f for f, _ in KNOWN_CHANNELS}), cad_sfs, cad_bws):
                    yield st
                quiet = min(activity, key=activity.get) if activity else grid[-1]
                yield Step(quiet, 125, 0.0, "cad", sf=9, network="reference")     # CAD false-alarm reference on the quietest channel
            steps = _quick_with_cad()
        else:
            steps = base
    elif kind == "survey":
        grid = band_grid(a.start, a.stop, a.step)
        base = survey_plan(grid, dwell_s=a.dwell, revisit_max_s=a.revisit, activity=activity, bw_khz=a.bw, clock=clock)
        if a.cad:
            def _survey_with_cad():
                n = 0
                for st in base:
                    yield st
                    n += 1
                    if n % len(grid) == 0:      # one CAD pass over the currently hot + known channels per grid round
                        hot = [f for f, v in activity.items() if v > 0.05]
                        for c in _cad_steps(sorted(set(hot) | {f for f, _ in KNOWN_CHANNELS}), cad_sfs, cad_bws):
                            yield c
                        quiet = min(activity, key=activity.get) if activity else grid[-1]
                        yield Step(quiet, 125, 0.0, "cad", sf=9, network="reference")
            steps = _survey_with_cad()
        else:
            steps = base
    elif kind == "watch":
        freqs = [int(round(float(x) * 1e6)) for x in a.freqs.split(",")]
        grid = freqs
        steps = watch_plan(freqs, dwell_s=a.dwell, sfs=cad_sfs, bws=cad_bws, decode_dwell_s=a.decode_dwell, cycles=a.cycles)
    elif kind == "test":
        cands = parse_candidates(a.candidates)
        grid = [c[0] for c in cands]
        steps = candidate_plan(cands, dwell_s=a.dwell)
    else:
        raise SystemExit(f"unknown plan {kind}")
    hal, radio = _open_radio(prof, grid[0])
    if a.fake_clock:
        hal.sleep = lambda s: fake_clock.__setitem__(0, fake_clock[0] + s)  # type: ignore[attr-defined]
    limit = _duration(a.duration)
    t_start = clock()
    stopped = {"flag": False}

    def _sig(*_):
        stopped["flag"] = True
    old = signal.signal(signal.SIGINT, _sig); old_t = signal.signal(signal.SIGTERM, _sig)
    n, failures = 0, 0
    engine = a.engine
    scan_failures = 0
    nets = {nw.name: nw for nw in NETWORKS}
    if engine == "scan":
        upload_patch(radio)
        print(f"[scan] scan patch uploaded; chip version string {version_string(radio)!r}")
    store.add_event(run_id, "start", f"{kind} grid={len(grid)} dwell={a.dwell} engine={engine}")
    try:
        for step in steps:
            if stopped["flag"] or (limit is not None and clock() - t_start >= limit):
                break
            ts = time.time() if not a.fake_clock else 1_700_000_000.0 + clock()
            try:
                if step.layer == "cad":
                    ref = step.network == "reference"
                    row = cad_sweep(radio, step.freq_hz, step.sf, step.bw_khz, n_cad=(200 if ref else a.cad_n), clock=clock, ts=ts, cr=step.cr)
                    store.add_cad(run_id, row)
                    if mq: mq.publish_cad(row)
                    if ref:
                        store.add_event(run_id, "cad_reference", f"{step.freq_hz} sf{step.sf} bw{step.bw_khz}", ts=ts)
                    n += 1
                    if a.verbose:
                        print(f"[cad ] {n:5d} {row.freq_hz/1e6:8.3f} MHz sf{row.sf}/bw{row.bw_hz//1000} hits {row.hits}/{row.n_cad} run {row.longest_run} timeouts {row.timeouts}")
                    continue
                if step.layer == "decode":
                    net = nets[step.network]
                    preset = [p for p in net.presets if p.name == step.preset][0]
                    row = decode_dwell(radio, step.freq_hz, net, preset, step.dwell_s, clock=clock, ts=ts)
                    store.add_decode(run_id, row)
                    if mq: mq.publish_decode(row)
                    n += 1
                    if a.verbose:
                        print(f"[dec ] {n:5d} {row.freq_hz/1e6:8.3f} MHz {row.network}/{row.preset}: ok {row.n_ok} crc-err {row.n_crc_err} rssi {row.rssi_med:.0f} snr {row.snr_med:.1f}")
                    continue
                if engine == "scan":
                    try:
                        nb = a.nb_scan if a.nb_scan else max(256, min(65535, int(step.dwell_s / 8.2e-6)))
                        row = scan_energy(radio, step.freq_hz, step.bw_khz, nb_scan=nb, offset_dbm=prof.scan_offset_dbm,
                                          busy_t_db=a.busy_t, ts=ts, clock=clock)
                    except ScanError as e:
                        scan_failures += 1
                        store.add_event(run_id, "scan_engine_error", f"{step.freq_hz} {e}")
                        if scan_failures >= 2:
                            print(f"[scan] WARNING: spectral-scan engine failed twice ({e}); falling back to the polled engine", file=sys.stderr)
                            engine = "poll"
                            radio.init(step.freq_hz)      # back to the LoRa modem the polled engine expects
                        continue
                else:
                    radio.ensure_lora(step.freq_hz)
                    row = polled_energy(radio, step.freq_hz, step.bw_khz, step.dwell_s, clock=clock, sample_gap_s=a.sample_gap,
                                        busy_t_db=a.busy_t, offset_dbm=prof.scan_offset_dbm, ts=ts)
            except SxError as e:
                failures += 1
                store.add_event(run_id, "radio_error", f"{step.freq_hz} {e}")
                print(f"[scan] radio error at {step.freq_hz/1e6:.3f} MHz: {e}; re-initialising", file=sys.stderr)
                try:
                    radio.init(step.freq_hz)
                    if engine == "scan":
                        upload_patch(radio)
                except SxError as e2:
                    store.add_event(run_id, "radio_reinit_failed", str(e2))
                    print(f"[scan] re-init failed: {e2}; stopping", file=sys.stderr)
                    break
                continue
            store.add_energy(run_id, row)
            if mq: mq.publish_energy(row, label_for(row.freq_hz))
            activity[step.freq_hz] = 0.7 * activity.get(step.freq_hz, 0.0) + 0.3 * row.busy_frac
            n += 1
            if a.verbose or n % 50 == 0:
                print(f"[scan] {n:5d} {row.freq_hz/1e6:8.3f} MHz n={row.n:5d} floor={row.floor_dbm:6.1f} p90={row.p90:6.1f} peak={row.peak:6.1f} busy={row.busy_frac*100:5.1f}% {label_for(row.freq_hz)}")
    finally:
        signal.signal(signal.SIGINT, old); signal.signal(signal.SIGTERM, old_t)
        store.add_event(run_id, "stop", f"rows={n} failures={failures}")
        if mq:
            mq.publish_status({"run_id": run_id, "kind": kind, "profile": prof.name, "state": "stopped", "rows": n, "failures": failures, "mqtt_errors": mq.errors})
            mq.close()
            if mq.errors:
                store.add_event(run_id, "mqtt_errors", str(mq.errors))
        try:
            radio.standby()
        except Exception:
            pass
        hal.close()
        store.close()
    print(f"[scan] run #{run_id} {kind}: {n} rows, {failures} radio errors, db={a.db}")
    return 0


def cmd_calibrate(a) -> int:
    """Measure a known input level (a signal generator or a reference transmitter at a measured level)
    and write the offset that makes the tool read it correctly into a copy of the profile (spec §8)."""
    prof = _profile(a.profile)
    hal, radio = _open_radio(prof, int(round(a.freq * 1e6)))
    try:
        row = polled_energy(radio, int(round(a.freq * 1e6)), a.bw, a.dwell, sample_gap_s=a.sample_gap)
    finally:
        hal.close()
    offset = round(a.level - row.p50, 1)
    newp = dataclasses.replace(prof, name=prof.name + "-calibrated", rssi_offset_db=offset)
    out = a.out or (prof.name + "-calibrated.yaml")
    with open(out, "w") as f:
        f.write(dump_profile(newp, f"calibrated {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}: known level {a.level} dBm at {a.freq} MHz read as p50 {row.p50} dBm (n={row.n})"))
    print(f"[calibrate] measured p50 {row.p50:.1f} dBm for a known {a.level:.1f} dBm input -> rssi_offset_db {offset:+.1f}; wrote {out}")
    return 0


def cmd_serve(a) -> int:
    from .serve import make_server
    srv = make_server(a.db, a.host, a.port, a.refresh)
    print(f"[serve] http://{a.host}:{a.port}/  (report re-rendered every {a.refresh} s from {a.db}; Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def cmd_status(a) -> int:
    store = Store(a.db)
    now = time.time()
    runs = store.runs()
    if not runs:
        print(f"[status] {a.db}: no runs")
        return 0
    for r in runs:
        c = store.counts(r["id"]); ev = store.event_counts(r["id"])
        last = max([x[1] for x in c.values() if x[1]] or [0])
        age = f"{now - last:.0f} s" if last else "-"
        state = "running" if "stop" not in ev else "finished"
        print(f"[status] run #{r['id']} {r['kind']} ({r['profile']}) {state}: {c['energy'][0]} energy, {c['cad'][0]} cad, {c['decode'][0]} decode rows; "
              f"last row {age} ago; events {ev}; note: {r['note']}")
    return 0


def cmd_report(a) -> int:
    from .report.html import render_report_from_share, build_data_from_share, build_data, write_svgs
    if a.from_share:
        with open(a.from_share) as f:
            doc = json.load(f)
        render_report_from_share(doc, a.out, title=a.title, slot_hz=a.slot)
        print(f"[report] wrote {a.out} from share document {a.from_share}")
        if a.svg:
            for p in write_svgs(build_data_from_share(doc), a.svg):
                print(f"[report] wrote {p}")
        return 0
    store = Store(a.db)
    prof_offset = a.rssi_offset
    if prof_offset is None:
        runs = store.runs()
        try:
            prof_offset = load_profile(runs[-1]["profile"]).rssi_offset_db if runs and runs[-1]["profile"] not in ("fake",) else 0.0
        except FileNotFoundError:
            prof_offset = 0.0
    since = (time.time() - _duration(a.since)) if a.since else None
    render_report(store, a.out, title=a.title, run_id=a.run, bucket_s=a.bucket, rssi_offset_db=prof_offset, since=since, slot_hz=a.slot)
    print(f"[report] wrote {a.out}")
    if a.svg:
        for p in write_svgs(build_data(store, a.run, a.bucket, prof_offset, since), a.svg):
            print(f"[report] wrote {p}")
    return 0


def cmd_export(a) -> int:
    """CSV export of every table (Loomwave/lorascan#1: v0.1.0-0.1.2 wrote only energy).

    --csv X.csv writes energy to X.csv, CAD to X-cad.csv and decodes to X-decode.csv (side files
    only when the table has rows); --table picks one table and writes it to --csv exactly."""
    store = Store(a.db)
    stem, ext = os.path.splitext(a.csv)
    ext = ext or ".csv"
    tables = {
        "energy": (["ts", "freq_hz", "bw_hz", "engine", "n", "floor_dbm", "p50", "p90", "peak", "busy_frac", "discarded", "hist"],
                   lambda: ([r.ts, r.freq_hz, r.bw_hz, r.engine, r.n, r.floor_dbm, r.p50, r.p90, r.peak, r.busy_frac, r.discarded, " ".join(map(str, r.hist))] for r in store.iter_energy(a.run))),
        "cad": (["ts", "freq_hz", "bw_hz", "sf", "symbols", "n_cad", "hits", "hit_rate", "longest_run", "det_peak", "det_min", "timeouts"],
                lambda: ([r.ts, r.freq_hz, r.bw_hz, r.sf, r.symbols, r.n_cad, r.hits, round(r.hits / r.n_cad, 4) if r.n_cad else "", r.longest_run, r.det_peak, r.det_min, r.timeouts] for r in store.iter_cad(a.run))),
        "decode": (["ts", "freq_hz", "network", "preset", "dwell_s", "n_ok", "n_crc_err", "rssi_med", "snr_med", "len_med"],
                   lambda: ([r.ts, r.freq_hz, r.network, r.preset, r.dwell_s, r.n_ok, r.n_crc_err, r.rssi_med, r.snr_med, r.len_med] for r in store.iter_decode(a.run))),
    }
    if a.table == "slots":
        from .report.html import build_data
        from .report.slots import slot_view
        d = build_data(store, a.run)
        slots = slot_view(d["channels"], store.cad_summary(a.run), store.decode_summary(a.run), a.slot or 500_000)
        cols = ["start_mhz", "end_mhz", "n_channels", "score", "busy_max", "busy_mean", "floor_worst", "floor_best", "peak_max", "cad_hit_max", "cad_sf_max", "decoded", "decoded_frames", "labels"]
        with open(a.csv, "w", newline="") as f:
            w = csv.writer(f); w.writerow(cols)
            for sl in slots:
                w.writerow([f"{sl['start_mhz']:.3f}", f"{sl['end_mhz']:.3f}"] + [sl[c] for c in cols[2:]])
        print(f"[export] wrote {a.csv} ({len(slots)} rows)")
        return 0
    wanted = [a.table] if a.table != "all" else ["energy", "cad", "decode"]
    for t in wanted:
        path = a.csv if (t == "energy" or a.table != "all") else f"{stem}-{t}{ext}"
        header, rows = tables[t]
        rows = list(rows())
        if not rows and t != "energy" and a.table == "all":
            print(f"[export] {t}: no rows, {path} not written")
            continue
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)
        print(f"[export] wrote {path} ({len(rows)} rows)")
    return 0


def cmd_share(a) -> int:
    store = Store(a.db)
    cell = None
    if a.cell:
        lat, lon = (float(x) for x in a.cell.split(","))
        cell = coarse_cell(lat, lon, a.cell_size)
    runs = store.runs()
    profile_name = runs[-1]["profile"] if runs else "unknown"
    try:
        offset = a.rssi_offset if a.rssi_offset is not None else (load_profile(profile_name).rssi_offset_db if profile_name != "fake" else 0.0)
    except FileNotFoundError:
        offset = 0.0
    token = submitter_token(a.token_path)
    if a.budget:
        doc, size = choose_by_budget(store, cell, token, profile_name, parse_budget(a.budget), offset, a.cell_size, a.run)
        print(f"[share] budget {a.budget}: chose granularity={doc['granularity']} tables={'energy' + (',cad,decode' if doc['cad'] or doc['decode'] else '')} ({size} bytes gzipped for the whole span)")
    else:
        doc = build_share(store, cell, token, profile_name, offset, a.cell_size, a.run, granularity=a.granularity)
    write_share(doc, a.out)
    print(f"[share] wrote {a.out}: {len(doc['energy'])} energy aggregates ({doc['granularity']}), {len(doc['cad'])} CAD rows, {len(doc['decode'])} decode rows, cell={doc['cell']}; {len(gzip_bytes(doc))} bytes gzipped")
    if a.dry_run or not a.to:
        if not a.dry_run:
            print("[share] no --to given: nothing uploaded; send the file later with `lorascan upload FILE --to URL`", file=sys.stderr)
        return 0
    return _upload(doc, a.to)


def _upload(doc: dict, to: str) -> int:
    r = upload_share(doc, to)
    print(f"[share] uploaded to {to}: sent {r['sent']} energy aggregates ({r['bytes']} bytes gzipped), skipped {r['skipped']} already at the endpoint (watermark {r['watermark']}), accepted {r.get('accepted')}, attempts {r['attempts']}")
    return 0


def cmd_upload(a) -> int:
    """Store-and-forward: upload a share file written earlier, from any machine (the submitter token is inside)."""
    with open(a.file) as f:
        doc = json.load(f)
    if doc.get("format") != SHARE_FORMAT:
        raise ValueError(f"{a.file}: format {doc.get('format')!r}, this lorascan sends {SHARE_FORMAT}")
    return _upload(doc, a.to)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lorascan", description="LoRa-chipset band scanner for 902-928 MHz (receive-only)")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def radio_args(sp):
        sp.add_argument("--profile", default="generic-spidev", help="board profile name or path ('fake' = simulated radio)")
        sp.add_argument("--dwell", type=float, default=0.4, help="seconds per channel visit")
        sp.add_argument("--sample-gap", type=float, default=0.0007, help="seconds between RSSI polls")

    sp = sub.add_parser("probe", help="first-light SPI check (reset, GetStatus, sync-word read)"); sp.add_argument("--profile", default="generic-spidev"); sp.set_defaults(fn=cmd_probe)
    sp = sub.add_parser("selftest", help="init + two short energy reads (+ one on-chip scan with --engine scan)"); radio_args(sp); sp.add_argument("--engine", choices=("poll", "scan"), default="poll"); sp.set_defaults(fn=cmd_selftest)
    sc = sub.add_parser("scan", help="run a scan plan"); ssub = sc.add_subparsers(dest="plan", required=True)
    plans = {"quick": ssub, "survey": ssub, "watch": ssub, "test": sub}
    for kind, parent in plans.items():
        sp = parent.add_parser(kind, help={"quick": "whole band, < 1 h", "survey": "continuous, adaptive revisit", "watch": "fixed channel list, all layers, high time resolution", "test": "candidate frequencies + LoRa settings -> report card"}[kind]); radio_args(sp)
        sp.add_argument("--db", default="lorascan.db"); sp.add_argument("--note", default="")
        sp.add_argument("--cad", action="store_true", help="quick/survey: add CAD sweeps on hot + known channels")
        sp.add_argument("--cad-n", type=int, default=50, help="CADs per sweep")
        sp.add_argument("--sfs", default=None, help="CAD spreading factors, e.g. 7,9,11")
        sp.add_argument("--bws", default=None, help="CAD/energy bandwidths kHz, e.g. 125,250")
        if kind == "watch":
            sp.add_argument("--freqs", required=True, help="MHz list, e.g. 906.875,911.5,921.0")
            sp.add_argument("--decode-dwell", type=float, default=10.0, help="seconds per decode attempt")
            sp.add_argument("--cycles", type=int, default=None, help="rounds over the list (default: until --duration/Ctrl-C)")
        if kind == "test":
            sp.add_argument("--candidates", required=True, help="MHz/SF/BW[/CR] list, e.g. 905.0/9/125,921.0/11/250/8")
        sp.add_argument("--start", type=int, default=902_000_000); sp.add_argument("--stop", type=int, default=928_000_000); sp.add_argument("--step", type=int, default=200_000)
        sp.add_argument("--bw", type=int, default=125, help="measurement bandwidth kHz (62/125/250/500)")
        sp.add_argument("--busy-t", type=float, default=8.0, help="busy threshold dB above floor")
        sp.add_argument("--engine", choices=("poll", "scan"), default="poll", help="poll = host-polled GetRssiInst; scan = on-chip histogram (Semtech scan patch, experimental)")
        sp.add_argument("--nb-scan", type=int, default=None, help="samples per on-chip scan (engine=scan); default = dwell / 8.2 us, max 65535")
        sp.add_argument("--duration", default=None, help="stop after e.g. 15m, 2h, 3d")
        sp.add_argument("--mqtt", default=None, help="publish rows to a broker: mqtt://[user:pass@]host[:port][/prefix] (needs paho-mqtt)")
        sp.add_argument("--fake-clock", action="store_true", help=argparse.SUPPRESS)
        sp.add_argument("-v", "--verbose", action="store_true")
        if kind == "quick":
            sp.add_argument("--passes", type=int, default=2)
        if kind == "survey":
            sp.add_argument("--revisit", type=float, default=600.0, help="max seconds between visits of any channel")
        if kind == "test":
            sp.set_defaults(dwell=30.0)
        sp.set_defaults(fn=lambda a, k=kind: _run_scan(a, k))
    sp = sub.add_parser("calibrate", help="turn a known input level into rssi_offset_db in a copy of the profile"); radio_args(sp)
    sp.add_argument("--level", type=float, required=True, help="known input level at the antenna port, dBm"); sp.add_argument("--freq", type=float, default=915.0, help="MHz")
    sp.add_argument("--bw", type=int, default=125); sp.add_argument("--out", default=None); sp.set_defaults(fn=cmd_calibrate, dwell=2.0)
    sp = sub.add_parser("serve", help="live web page of a database (for a running survey)"); sp.add_argument("--db", default="lorascan.db"); sp.add_argument("--host", default="0.0.0.0"); sp.add_argument("--port", type=int, default=8080); sp.add_argument("--refresh", type=int, default=60); sp.set_defaults(fn=cmd_serve)
    sp = sub.add_parser("status", help="runs, row counts and last-row age in a database"); sp.add_argument("--db", default="lorascan.db"); sp.set_defaults(fn=cmd_status)
    sp = sub.add_parser("report"); sp.add_argument("--db", default="lorascan.db"); sp.add_argument("--out", default="lorascan-report.html"); sp.add_argument("--title", default="lorascan report")
    sp.add_argument("--run", type=int, default=None); sp.add_argument("--since", default=None, help="only the last e.g. 6h / 2d"); sp.add_argument("--bucket", type=int, default=None, help="heat map bucket seconds (default: auto, <= 600 columns)"); sp.add_argument("--rssi-offset", type=float, default=None)
    sp.add_argument("--slot", type=int, default=None, help="add an N Hz slot table (e.g. 500000): worst case per window, best first")
    sp.add_argument("--svg", default=None, help="also write standalone heatmap.svg / band.svg / sfmap.svg / when.svg into this directory")
    sp.add_argument("--from-share", default=None, help="render from a share document (lorascan share output) instead of a database"); sp.set_defaults(fn=cmd_report)
    sp = sub.add_parser("export", help="CSV: energy to --csv, CAD to <stem>-cad.csv, decodes to <stem>-decode.csv (or one table with --table)"); sp.add_argument("--db", default="lorascan.db"); sp.add_argument("--csv", required=True); sp.add_argument("--run", type=int, default=None)
    sp.add_argument("--table", choices=("all", "energy", "cad", "decode", "slots"), default="all", help="one table to --csv exactly, or all (default); slots = the N kHz window view")
    sp.add_argument("--slot", type=int, default=None, help="window width Hz for --table slots (default 500000)"); sp.set_defaults(fn=cmd_export)
    sp = sub.add_parser("share", help="write the opt-in community share file (aggregates + coarse cell; no upload yet)")
    sp.add_argument("--db", default="lorascan.db"); sp.add_argument("--out", default="lorascan-share.json"); sp.add_argument("--run", type=int, default=None)
    sp.add_argument("--cell", default=None, help="lat,lon of the antenna; rounded to --cell-size degrees (omit for no location)")
    sp.add_argument("--cell-size", type=float, default=DEFAULT_CELL_DEG); sp.add_argument("--rssi-offset", type=float, default=None)
    sp.add_argument("--token-path", default=os.path.expanduser("~/.config/lorascan/token")); sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--granularity", choices=("hour", "day"), default="hour", help="aggregate per hour (~53 KB/day gzipped) or per day (~3 KB/day)")
    sp.add_argument("--budget", default=None, help="bytes per day of survey, e.g. 20k/day: picks the coarsest document that fits")
    sp.add_argument("--to", default=None, help="upload endpoint, e.g. https://share.lorascan.app (gzip, incremental; omit to only write the file)")
    sp.set_defaults(fn=cmd_share)
    sp = sub.add_parser("upload", help="upload a share file written earlier (store-and-forward from any machine)")
    sp.add_argument("file"); sp.add_argument("--to", required=True); sp.set_defaults(fn=cmd_upload)
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return int(a.fn(a))
    except (HalError, DeviceBusy, DeviceError, SxError, FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"lorascan: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
