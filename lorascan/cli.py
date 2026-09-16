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
from . import networks as netmod
from .autoconf import AutoConfError
from .exclusions import parse_exclusions
from .networks import presets_on
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


HAL_BACKOFF_S = [1, 2, 4, 8, 16]      # Loomwave/lorascan#6: bus-level recovery backoff per consecutive failure
HAL_MAX_CONSECUTIVE = 5               # give up (hal_giveup event, clean stop) after this many failures in a row


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
    from . import station_config as _sc
    prof_name = a.profile
    if prof_name == "generic-spidev":                 # the argparse default = "not chosen"
        prof_name = _sc.load().profile or prof_name
    prof = _profile(prof_name)
    store = Store(a.db)
    if prof.bus_type == "fake":
        a.fake_clock = True          # a simulated radio never sleeps; drive its time from a fake clock
    fake_clock = [0.0]
    clock = (lambda: fake_clock[0]) if a.fake_clock else time.monotonic
    user_nets = netmod.load_user_networks(a.networks) if getattr(a, "networks", None) else netmod.load_default_user_networks()
    if user_nets:
        netmod.set_networks(netmod.merge_networks(netmod.BUILTIN_NETWORKS, user_nets))
        print(f"[scan] user network table: {', '.join(n.name + '(' + str(len(n.presets)) + ')' for n in user_nets)}")
    bws_energy = [int(x) for x in str(a.bw).split(",")]
    cad_grid = [int(a.start + a.cad_grid / 2 + k * a.cad_grid) for k in range((a.stop - a.start) // a.cad_grid)] if getattr(a, "cad_grid", None) else []
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
        base = quick_plan(grid, passes=a.passes, dwell_s=a.dwell, bw_khz=bws_energy)
        if a.cad or cad_grid:
            def _quick_with_cad():
                hot = set()
                for st in base:
                    yield st
                    if activity.get(st.freq_hz, 0.0) > 0.05:
                        hot.add(st.freq_hz)
                if a.cad:
                    for st in _cad_steps(sorted(hot | {f for f, _ in KNOWN_CHANNELS}), cad_sfs, cad_bws):
                        yield st
                for st in _cad_steps(cad_grid, cad_sfs, cad_bws):          # dense whole-band CAD grid (#2 item 2)
                    yield st
                quiet = min(activity, key=activity.get) if activity else grid[-1]
                yield Step(quiet, 125, 0.0, "cad", sf=9, network="reference")     # CAD false-alarm reference on the quietest channel
            steps = _quick_with_cad()
        else:
            steps = base
    elif kind == "survey":
        grid = band_grid(a.start, a.stop, a.step)
        base = survey_plan(grid, dwell_s=a.dwell, revisit_max_s=a.revisit, activity=activity, bw_khz=bws_energy, clock=clock)
        if a.cad or cad_grid:
            def _survey_with_cad():
                n = 0
                per_round = len(grid) * len(bws_energy)
                for st in base:
                    yield st
                    n += 1
                    if n % per_round == 0:      # one CAD pass per grid round: hot + known channels (--cad) and/or the dense grid (--cad-grid)
                        if a.cad:
                            hot = [f for f, v in activity.items() if v > 0.05]
                            for c in _cad_steps(sorted(set(hot) | {f for f, _ in KNOWN_CHANNELS}), cad_sfs, cad_bws):
                                yield c
                        for c in _cad_steps(cad_grid, cad_sfs, cad_bws):
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
    hal_consecutive = 0
    engine = a.engine
    scan_failures = 0
    nets = {nw.name: nw for nw in netmod.NETWORKS}
    if engine == "scan":
        upload_patch(radio)
        print(f"[scan] scan patch uploaded; chip version string {version_string(radio)!r}")
    store.add_event(run_id, "start", f"{kind} grid={len(grid)} dwell={a.dwell} engine={engine}")
    step_iter = iter(steps)
    retry_step = None
    try:
        while True:
            step = retry_step if retry_step is not None else next(step_iter, None)
            retry_step = None
            if step is None or stopped["flag"] or (limit is not None and clock() - t_start >= limit):
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
            except (HalError, OSError) as e:
                # bus-level failure (pyusb timeout / resource busy, spidev EIO): reopen the HAL, not just the chip (#6)
                failures += 1
                hal_consecutive += 1
                store.add_event(run_id, "hal_error", f"{step.freq_hz} {e}")
                print(f"[scan] bus error at {step.freq_hz/1e6:.3f} MHz: {e}; reopening the radio ({hal_consecutive}/{HAL_MAX_CONSECUTIVE})", file=sys.stderr)
                if hal_consecutive >= HAL_MAX_CONSECUTIVE:
                    store.add_event(run_id, "hal_giveup", f"{hal_consecutive} consecutive bus errors")
                    print("[scan] giving up: the radio did not come back; stopping cleanly", file=sys.stderr)
                    break
                try:
                    hal.close()
                except Exception:
                    pass
                back = HAL_BACKOFF_S[min(hal_consecutive - 1, len(HAL_BACKOFF_S) - 1)]
                (hal.sleep if a.fake_clock else time.sleep)(back)
                try:
                    if hal_consecutive >= 2 and hasattr(hal, "reset_device"):
                        hal.reset_device()
                    hal, radio = _open_radio(prof, step.freq_hz)
                    if a.fake_clock:
                        hal.sleep = lambda s: fake_clock.__setitem__(0, fake_clock[0] + s)  # type: ignore[attr-defined]
                    if engine == "scan":
                        upload_patch(radio)
                    store.add_event(run_id, "hal_recovered", f"after {hal_consecutive} failure(s), {back} s backoff")
                    hal_consecutive = 0
                except (HalError, OSError, SxError, DeviceError, DeviceBusy) as e2:
                    store.add_event(run_id, "hal_reopen_failed", str(e2))
                    print(f"[scan] reopen failed: {e2}", file=sys.stderr)
                retry_step = step                # the interrupted visit is measured again, not skipped
                continue
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
    zones = [] if a.no_exclude else parse_exclusions(a.exclude)
    if a.from_share:
        with open(a.from_share) as f:
            doc = json.load(f)
        render_report_from_share(doc, a.out, title=a.title, slot_hz=a.slot, exclusions=zones)
        print(f"[report] wrote {a.out} from share document {a.from_share}")
        if a.svg:
            for p in write_svgs(build_data_from_share(doc, exclusions=zones), a.svg):
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
    render_report(store, a.out, title=a.title, run_id=a.run, bucket_s=a.bucket, rssi_offset_db=prof_offset, since=since, slot_hz=a.slot, exclusions=zones)
    print(f"[report] wrote {a.out}")
    if a.svg:
        for p in write_svgs(build_data(store, a.run, a.bucket, prof_offset, since, exclusions=zones), a.svg):
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
        zones = [] if a.no_exclude else parse_exclusions(a.exclude)
        d = build_data(store, a.run, exclusions=zones)
        slots = slot_view(d["channels"], store.cad_summary(a.run), store.decode_summary(a.run), a.slot or 500_000, exclusions=zones)
        cols = ["start_mhz", "end_mhz", "n_channels", "excluded", "score", "busy_max", "busy_mean", "floor_worst", "floor_best", "floor_penalty", "peak_max", "cad_hit_max", "cad_sf_max", "decoded", "decoded_frames", "labels"]
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
    from . import station_config as _sc
    _cfg = _sc.load()
    to = _sc.resolve(a.to, _cfg.endpoint, None)
    cellarg = a.cell if a.cell is not None else (f"{_cfg.location[0]},{_cfg.location[1]}" if _cfg.location else None)
    gran = _sc.resolve(getattr(a, "granularity", None), _cfg.granularity, "hour")
    store = Store(a.db)
    cell = None
    if cellarg:
        lat, lon = (float(x) for x in cellarg.split(","))
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
        doc = build_share(store, cell, token, profile_name, offset, a.cell_size, a.run, granularity=gran)
    write_share(doc, a.out)
    print(f"[share] wrote {a.out}: {len(doc['energy'])} energy aggregates ({doc['granularity']}), {len(doc['cad'])} CAD rows, {len(doc['decode'])} decode rows, cell={doc['cell']}; {len(gzip_bytes(doc))} bytes gzipped")
    if a.dry_run or not to:
        if not a.dry_run:
            print("[share] no --to given: nothing uploaded; send the file later with `lorascan upload FILE --to URL`", file=sys.stderr)
        return 0
    return _upload(doc, to)


def _upload(doc: dict, to: str) -> int:
    r = upload_share(doc, to)
    print(f"[share] uploaded to {to}: sent {r['sent']} energy aggregates ({r['bytes']} bytes gzipped), skipped {r['skipped']} already at the endpoint (watermark {r['watermark']}), accepted {r.get('accepted')}, attempts {r['attempts']}")
    return 0


def cmd_auto(a) -> int:
    """Loomwave/lorascan#7: read the daemon's radio config, hold it, scan, restore, share."""
    from . import autoconf as A
    from .profile import dump_profile
    r = A.resolve(a.source, root=a.root, config=a.config)
    tail = list(a.scan_args)
    if tail and tail[0] == "--":
        tail = tail[1:]
    plan = tail[0] if tail and tail[0] in ("quick", "survey", "watch", "test") else "survey"
    rest = tail[1:] if tail and tail[0] == plan else tail
    prof_path = a.profile_out or os.path.join(os.path.expanduser("~/.config/lorascan/profiles"), f"{r.profile.name}.yaml")
    dev = r.device
    if r.usb and r.profile.bus_type == "ch341":
        dev = A.usb_node(*r.usb) or ""
    scan_argv = ["scan", plan, "--profile", prof_path] + rest
    print(f"[auto] source: {r.source}")
    print(f"[auto] radio: {r.profile.bus_type} {r.profile.bus_dev} pins {r.profile.pins}" + (f" usb {r.usb[0]:04x}:{r.usb[1]:04x}" if r.usb else "") + (f" (daemon max power {r.hint_max_power} dBm; lorascan never transmits)" if r.hint_max_power else ""))
    print(f"[auto] would stop {r.service}, verify {dev or 'the USB device'} is free, run: lorascan {' '.join(scan_argv)}, then restore {r.service}")
    if r.location:
        print(f"[auto] location for the share: {r.location[0]:.4f},{r.location[1]:.4f} from {r.location[2]}")
    else:
        print("[auto] location for the share: none in the daemon's config (pass --cell lat,lon to share one)")
    for n in r.notes:
        print(f"[auto] note: {n}")
    if a.dry_run:
        print("[auto] dry run: nothing stopped, nothing written")
        return 0
    os.makedirs(os.path.dirname(prof_path), exist_ok=True)
    with open(prof_path, "w") as f:
        f.write(dump_profile(r.profile, f"auto-configured from {r.source} ({a.source}) by lorascan auto"))
    print(f"[auto] wrote profile {prof_path}")
    result = {"rc": 1}
    def scan():
        result["rc"] = main(scan_argv)
        return result["rc"]
    A.with_radio_held(r.service, dev or None, scan)
    if result["rc"] != 0:
        return result["rc"]
    if a.to:
        db = rest[rest.index("--db") + 1] if "--db" in rest else "lorascan.db"
        cell = a.cell or (f"{r.location[0]},{r.location[1]}" if r.location else None)
        share_argv = ["share", "--db", db, "--out", a.share_out, "--to", a.to] + (["--cell", cell] if cell else []) + ["--granularity", a.granularity]
        print(f"[auto] sharing: lorascan {' '.join(share_argv)}" + (f" (location from {r.location[2]})" if cell and not a.cell and r.location else ""))
        return main(share_argv)
    return 0


def cmd_syncfind(a) -> int:
    """Loomwave/lorascan#5: sweep sync words at one PHY hypothesis and report the ones that decode."""
    from .syncfind import parse_syncs, sync_find
    prof = _profile(a.profile)
    syncs = parse_syncs(a.syncs)
    freq = int(round(a.freq * 1e6))
    store = Store(a.db)
    run_id = store.new_run("syncfind", prof.name, f"{a.freq} MHz sf{a.sf} bw{a.bw} cr{a.cr} {len(syncs)} syncs x {a.sync_dwell} s")
    hal, radio = _open_radio(prof, freq)
    fake = prof.bus_type == "fake"
    if fake:
        clk = [0.0]
        hal.sleep = lambda s: clk.__setitem__(0, clk[0] + s)  # type: ignore[attr-defined]
        clock = lambda: clk[0]
    else:
        clock = time.monotonic
    print(f"[syncfind] {a.freq:.3f} MHz sf{a.sf}/bw{a.bw}/cr{a.cr}: {len(syncs)} sync words x {a.sync_dwell} s = {len(syncs) * a.sync_dwell / 60:.1f} min")
    found = []
    def progress(s, row):
        store.add_decode(run_id, row)
        if row.n_ok or row.n_crc_err:
            found.append(row)
            print(f"[syncfind] sync 0x{s:02X}: {row.n_ok} CRC-ok frames, {row.n_crc_err} header/CRC errors, rssi {row.rssi_med:.0f} dBm snr {row.snr_med:.1f}")
        elif a.verbose:
            print(f"[syncfind] sync 0x{s:02X}: nothing")
    try:
        sync_find(radio, freq, a.sf, a.bw, a.cr, syncs, a.sync_dwell, preamble=a.preamble, clock=clock, progress=progress)
    finally:
        try:
            radio.standby()
        except Exception:
            pass
        hal.close()
        store.add_event(run_id, "stop", f"syncs={len(syncs)} found={len([r for r in found if r.n_ok])}")
        store.close()
    ok = sorted((r for r in found if r.n_ok), key=lambda r: -r.n_ok)
    if ok:
        print("[syncfind] FOUND: " + ", ".join(f"{r.network} ({r.n_ok} frames, {r.rssi_med:.0f} dBm)" for r in ok)
              + f"  -> add to networks.yaml as  mynet/{ok[0].preset.replace('/', '-')}: {{sync: 0x{int(ok[0].network[5:], 16):02X}, sf: {a.sf}, bw: {a.bw}, cr: {a.cr}, freqs: {a.freq}}}")
    else:
        hdr = [r for r in found if r.n_crc_err]
        print("[syncfind] found none with CRC-valid frames" + (f"; header/CRC errors only at {', '.join(r.network for r in hdr)} (right sync, wrong CR/SF/implicit header?)" if hdr else "")
              + "; if CAD is hot here, try the other CRs (5-8), the neighbouring SF, or a longer --sync-dwell")
    return 0


def cmd_upload(a) -> int:
    """Store-and-forward: upload a share file written earlier, from any machine (the submitter token is inside)."""
    from . import station_config as _sc
    to = _sc.resolve(a.to, _sc.load().endpoint, None)
    if not to:
        raise ValueError("no endpoint: pass --to or run `lorascan setup`")
    with open(a.file) as f:
        doc = json.load(f)
    if doc.get("format") != SHARE_FORMAT:
        raise ValueError(f"{a.file}: format {doc.get('format')!r}, this lorascan sends {SHARE_FORMAT}")
    return _upload(doc, to)


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
        sp.add_argument("--bw", default="125", help="measurement bandwidth kHz (62/125/250/500), or a list 62,125,250,500 measured back to back per channel")
        sp.add_argument("--cad-grid", type=int, default=None, help="quick/survey: CAD-sweep the whole band once per round on windows this wide (Hz, e.g. 500000) at every --sfs x --bws")
        sp.add_argument("--networks", default=None, help="user network table (default ~/.config/lorascan/networks.yaml or /etc/lorascan/networks.yaml if present)")
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
    sp.add_argument("--exclude", default=None, help="MHz zones measured but never recommended, e.g. 902.0-903.25,926.75-928.0 (the default: band edges + 33 cm repeater segments)")
    sp.add_argument("--no-exclude", action="store_true", help="recommend any channel, including band edges and repeater segments")
    sp.add_argument("--svg", default=None, help="also write standalone heatmap.svg / band.svg / sfmap.svg / when.svg into this directory")
    sp.add_argument("--from-share", default=None, help="render from a share document (lorascan share output) instead of a database"); sp.set_defaults(fn=cmd_report)
    sp = sub.add_parser("export", help="CSV: energy to --csv, CAD to <stem>-cad.csv, decodes to <stem>-decode.csv (or one table with --table)"); sp.add_argument("--db", default="lorascan.db"); sp.add_argument("--csv", required=True); sp.add_argument("--run", type=int, default=None)
    sp.add_argument("--table", choices=("all", "energy", "cad", "decode", "slots"), default="all", help="one table to --csv exactly, or all (default); slots = the N kHz window view")
    sp.add_argument("--slot", type=int, default=None, help="window width Hz for --table slots (default 500000)")
    sp.add_argument("--exclude", default=None, help="MHz zones never recommended, e.g. 902.0-903.25,926.75-928.0 (the default)"); sp.add_argument("--no-exclude", action="store_true"); sp.set_defaults(fn=cmd_export)
    sp = sub.add_parser("share", help="write the opt-in community share file (aggregates + coarse cell; no upload yet)")
    sp.add_argument("--db", default="lorascan.db"); sp.add_argument("--out", default="lorascan-share.json"); sp.add_argument("--run", type=int, default=None)
    sp.add_argument("--cell", default=None, help="lat,lon of the antenna; rounded to --cell-size degrees (omit for no location)")
    sp.add_argument("--cell-size", type=float, default=DEFAULT_CELL_DEG); sp.add_argument("--rssi-offset", type=float, default=None)
    sp.add_argument("--token-path", default=os.path.expanduser("~/.config/lorascan/token")); sp.add_argument("--dry-run", action="store_true")
    sp.add_argument("--granularity", choices=("hour", "day"), default="hour", help="aggregate per hour (~53 KB/day gzipped) or per day (~3 KB/day)")
    sp.add_argument("--budget", default=None, help="bytes per day of survey, e.g. 20k/day: picks the coarsest document that fits")
    sp.add_argument("--to", default=None, help="upload endpoint, e.g. https://share.lorascan.app (gzip, incremental; omit to only write the file)")
    sp.set_defaults(fn=cmd_share)
    sp = sub.add_parser("auto", help="configure from a running meshtasticd / openHOP daemon: read its radio config, stop that unit, scan, restore it, share")
    sp.add_argument("--from", dest="source", required=True, choices=("meshtasticd", "openhop")); sp.add_argument("--config", default=None, help="the daemon config to use (a config.d board file for meshtasticd; required when several boards are active)")
    sp.add_argument("--root", default=None, help=argparse.SUPPRESS); sp.add_argument("--dry-run", action="store_true", help="print the resolved profile, the scan command and the unit that would be stopped; touch nothing")
    sp.add_argument("--profile-out", default=None, help="where to write the resolved profile (default ~/.config/lorascan/profiles/<name>.yaml)")
    sp.add_argument("--to", default=None, help="share endpoint to upload to after the scan"); sp.add_argument("--cell", default=None, help="lat,lon for the share (default: the daemon's config location if any)")
    sp.add_argument("--granularity", choices=("hour", "day"), default="day"); sp.add_argument("--share-out", default="lorascan-share.json")
    sp.add_argument("scan_args", nargs=argparse.REMAINDER, help="-- then the plan and its options, e.g. -- survey --db x.db --duration 2h --cad-grid 500000")
    sp.set_defaults(fn=cmd_auto)
    sp = sub.add_parser("syncfind", help="sweep 8-bit sync words at one freq/SF/BW/CR and report the ones that decode (names an undocumented LoRa net)"); radio_args(sp)
    sp.add_argument("--db", default="lorascan.db"); sp.add_argument("--freq", type=float, required=True, help="MHz"); sp.add_argument("--sf", type=int, required=True); sp.add_argument("--bw", type=int, default=125)
    sp.add_argument("--cr", type=int, default=5); sp.add_argument("--preamble", type=int, default=8); sp.add_argument("--syncs", default=None, help="e.g. 0x12,0x34 or 0x00-0x7F (default all 256)")
    sp.add_argument("--sync-dwell", type=float, default=1.0, help="seconds per sync word"); sp.add_argument("--verbose", "-v", action="store_true"); sp.set_defaults(fn=cmd_syncfind)
    sp = sub.add_parser("upload", help="upload a share file written earlier (store-and-forward from any machine)")
    sp.add_argument("file"); sp.add_argument("--to", default=None, help="upload endpoint (default: the station config's share endpoint)"); sp.set_defaults(fn=cmd_upload)
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return int(a.fn(a))
    except (HalError, DeviceBusy, DeviceError, SxError, FileNotFoundError, ValueError, RuntimeError, AutoConfError) as e:
        print(f"lorascan: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
