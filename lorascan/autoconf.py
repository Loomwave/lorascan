"""Auto-configuration from a running meshtasticd or openHOP daemon (Loomwave/lorascan#7):
READ the radio config the daemon uses -> HOLD (stop only that unit, verify nothing else holds the
device) -> SCAN (the normal plan) -> RESTORE (always, verified) -> SHARE (location from the daemon's
config, labelled). Nothing here transmits; the daemon's TX power is never copied into the profile."""
from __future__ import annotations
import glob
import os
import subprocess
import time
from dataclasses import dataclass, field
from .profile import BoardProfile, load_profile
from .yamlmini import parse_yaml


class AutoConfError(Exception):
    pass


@dataclass
class Ran:
    rc: int
    out: str
    err: str


def _default_run(argv) -> Ran:
    p = subprocess.run(list(argv), capture_output=True, text=True)
    return Ran(p.returncode, p.stdout, p.stderr)


@dataclass
class Resolved:
    profile: BoardProfile
    service: str
    source: str
    usb: tuple | None = None
    hint_max_power: int | None = None
    location: tuple | None = None        # (lat, lon, "where it came from")
    device: str = ""                     # what to check with fuser: /dev/spidevX.Y or the USB node
    notes: list = field(default_factory=list)


def _profile_from_lora(lora: dict, name: str) -> tuple[BoardProfile, tuple | None]:
    spidev = str(lora.get("spidev", "")).strip()
    if spidev.lower() == "ch341":
        base = load_profile("meshtoad-v3-ch341")            # the known-good CH341 + SX1262 pin block
        pins = dict(base.pins)
        for src, dst in (("CS", "nss"), ("IRQ", "dio1"), ("Reset", "reset"), ("Busy", "busy"), ("RXen", "rxen"), ("TXen", "txen")):
            if src in lora and lora[src] is not None:
                pins[dst] = int(lora[src])
        vid = lora.get("USB_VID", 0x1A86); pid = lora.get("USB_PID", 0x5512)
        p = BoardProfile(name=name, bus_type="ch341", bus_dev="auto", bus_hz=0, pins=pins, tcxo_v=1.8 if lora.get("DIO3_TCXO_VOLTAGE", True) else 0.0,
                         dio2_rf_switch=bool(lora.get("DIO2_AS_RF_SWITCH", True)), rx_boosted=True, max_tx_dbm=-9)
        return p, (int(vid), int(pid))
    if not spidev:
        raise AutoConfError("Lora block has no 'spidev' key (neither spidevX.Y nor ch341)")
    dev = spidev if spidev.startswith("/dev/") else "/dev/" + spidev
    pins = {"nss": "kernel", "reset": lora.get("Reset"), "busy": lora.get("Busy"), "dio1": lora.get("IRQ"), "rxen": lora.get("RXen"), "txen": lora.get("TXen")}
    for k in ("reset", "busy", "dio1"):
        if pins[k] is None:
            raise AutoConfError(f"Lora block lacks the {k} pin ({ {'reset': 'Reset', 'busy': 'Busy', 'dio1': 'IRQ'}[k] })")
    pins = {k: (int(v) if isinstance(v, (int, float)) else v) for k, v in pins.items()}
    if "CS" in lora and lora["CS"] is not None:
        pins["nss"] = int(lora["CS"])
    p = BoardProfile(name=name, bus_type="spidev", bus_dev=dev, bus_hz=2_000_000, pins=pins, tcxo_v=1.8 if lora.get("DIO3_TCXO_VOLTAGE", True) else 0.0,
                     dio2_rf_switch=bool(lora.get("DIO2_AS_RF_SWITCH", True)), rx_boosted=True, max_tx_dbm=-9)
    return p, None


def _service_for_config(config: str, default: str, run) -> str:
    """The unit whose ExecStart names this config file; default when none does."""
    try:
        units = run(["systemctl", "list-units", "--type=service", "--all", "--no-legend", "--plain"])
    except Exception:
        return default
    names = []
    for line in units.out.splitlines():
        tok = line.strip().split()
        if tok and tok[0].endswith(".service") and ("meshtastic" in tok[0] or "openhop" in tok[0]):
            names.append(tok[0])
    for u in names:
        try:
            show = run(["systemctl", "show", u, "-p", "ExecStart", "-p", "Id"])
        except Exception:
            continue
        if os.path.basename(config) in show.out:
            return u
    return default


def resolve_meshtasticd(root: str = "/etc/meshtasticd", config: str | None = None, run=None) -> Resolved:
    run = run or _default_run
    if config:
        src = config
        d = parse_yaml(open(config).read())
        lora = d.get("Lora") or {}
        if str(lora.get("Module", "")).lower() == "auto" or not lora.get("spidev"):
            raise AutoConfError(f"{config}: Lora.Module is auto / has no spidev; pass the config.d board file instead")
    else:
        main = os.path.join(root, "config.yaml")
        d = parse_yaml(open(main).read()) if os.path.exists(main) else {}
        lora = d.get("Lora") or {}
        if str(lora.get("Module", "auto")).lower() == "auto" or not lora.get("spidev"):
            cdir = os.path.join(root, "config.d")
            boards = sorted(f for f in glob.glob(os.path.join(cdir, "*.yaml")) if (parse_yaml(open(f).read()).get("Lora") or {}).get("spidev"))
            if not boards:
                raise AutoConfError(f"no active board with a Lora.spidev in {cdir} (config.yaml Lora.Module is auto)")
            if len(boards) > 1:
                raise AutoConfError("several active boards in config.d: " + ", ".join(os.path.basename(b) for b in boards) + " — pick one with --config PATH")
            src = boards[0]
            lora = parse_yaml(open(src).read())["Lora"]
        else:
            src = main
    prof, usb = _profile_from_lora(lora, "auto-meshtasticd")
    service = _service_for_config(src, "meshtasticd.service", run) if config else "meshtasticd.service"
    r = Resolved(prof, service, src, usb=usb, hint_max_power=lora.get("SX126X_MAX_POWER"), device=prof.bus_dev if prof.bus_type == "spidev" else "usb")
    gps = d.get("GPS") or {}
    if gps.get("SerialPath"):
        r.notes.append(f"meshtasticd GPS.SerialPath={gps['SerialPath']} (live fix not read; pass --cell lat,lon for the share)")
    return r


def resolve_openhop(config: str = "/etc/openhop_repeater/config.yaml", run=None) -> Resolved:
    d = parse_yaml(open(config).read())
    ch = d.get("ch341")
    if not isinstance(ch, dict):
        raise AutoConfError(f"{config}: no ch341: block (openHOP's radio is a CH341 USB stick)")
    vid, pid = ch.get("vid", 0x1A86), ch.get("pid", 0x5512)
    vid = int(vid, 0) if isinstance(vid, str) else int(vid); pid = int(pid, 0) if isinstance(pid, str) else int(pid)
    base = load_profile("meshtoad-v3-ch341")
    prof = BoardProfile(name="auto-openhop", bus_type="ch341", bus_dev="auto", bus_hz=0, pins=dict(base.pins), tcxo_v=base.tcxo_v,
                        dio2_rf_switch=base.dio2_rf_switch, rx_boosted=True, max_tx_dbm=-9)
    r = Resolved(prof, "openhop-repeater.service", config, usb=(vid, pid), device="usb")
    gps = d.get("gps") or {}
    loc = gps.get("location") if isinstance(gps.get("location"), dict) else None
    if loc and loc.get("lat") is not None and loc.get("lon") is not None:
        r.location = (float(loc["lat"]), float(loc["lon"]), "openhop config gps.location")
    elif isinstance(d.get("location"), dict) and d["location"].get("lat") is not None:
        r.location = (float(d["location"]["lat"]), float(d["location"]["lon"]), "openhop config location")
    return r


def resolve(source: str, root: str | None = None, config: str | None = None, run=None) -> Resolved:
    if source == "meshtasticd":
        return resolve_meshtasticd(root or "/etc/meshtasticd", config, run)
    if source == "openhop":
        return resolve_openhop(config or "/etc/openhop_repeater/config.yaml", run)
    raise AutoConfError(f"unknown --from {source!r} (meshtasticd | openhop)")


def usb_node(vid: int, pid: int) -> str | None:
    """/dev/bus/usb/BBB/DDD of the first device with this VID:PID, via sysfs (no pyusb needed)."""
    for d in glob.glob("/sys/bus/usb/devices/*"):
        try:
            v = int(open(os.path.join(d, "idVendor")).read().strip(), 16); p = int(open(os.path.join(d, "idProduct")).read().strip(), 16)
            if (v, p) == (vid, pid):
                bus = int(open(os.path.join(d, "busnum")).read()); dev = int(open(os.path.join(d, "devnum")).read())
                return f"/dev/bus/usb/{bus:03d}/{dev:03d}"
        except (OSError, ValueError):
            continue
    return None


def holders(device: str, run) -> str:
    r = run(["fuser", "-v", device])
    return (r.out + r.err).strip() if r.rc == 0 else ""


def with_radio_held(service: str, device: str | None, fn, run=None, settle_s: float = 2.0, log=print):
    """Stop ONE unit, verify the device is free, run fn(), and ALWAYS start the unit back and verify it."""
    run = run or _default_run
    log(f"[auto] stopping {service}")
    r = run(["systemctl", "stop", service])
    if r.rc != 0:
        raise AutoConfError(f"systemctl stop {service} failed: {r.err.strip() or r.out.strip()}")
    try:
        if settle_s:
            time.sleep(settle_s)
        if device:
            h = holders(device, run)
            if h:
                raise AutoConfError(f"{device} is still held after stopping {service}: {h} — not scanning over a live connection")
        return fn()
    finally:
        log(f"[auto] restoring {service}")
        r = run(["systemctl", "start", service])
        a = run(["systemctl", "is-active", service])
        if r.rc != 0 or a.out.strip() != "active":
            log(f"[auto] WARNING: {service} did not come back (start rc={r.rc}, is-active={a.out.strip() or a.rc}); start it by hand: systemctl start {service}")
        else:
            log(f"[auto] {service} is active again")
