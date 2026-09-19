"""The `lorascan setup` guided wizard. Pure orchestration: all console I/O and hardware/network
calls go through the IO and Runner seams so every step is unit-tested with no TTY and no hardware."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class StepResult:
    ok: bool
    summary: str
    detail: str = ""


class IO:
    def ask(self, prompt, default=None): raise NotImplementedError
    def choose(self, prompt, options): raise NotImplementedError
    def confirm(self, prompt): raise NotImplementedError
    def say(self, msg): raise NotImplementedError


class ScriptedIO(IO):
    def __init__(self, answers):
        self._a = list(answers); self.said = []
    def _next(self): return self._a.pop(0) if self._a else "quit"
    def ask(self, prompt, default=None):
        v = self._next(); return default if (v == "" and default is not None) else v
    def choose(self, prompt, options):
        v = self._next()
        return (int(v) - 1) if v.isdigit() else 0
    def confirm(self, prompt):
        return self._next().strip().lower() in ("y", "yes", "true", "1")
    def say(self, msg): self.said.append(str(msg))


class TtyIO(IO):
    def ask(self, prompt, default=None):
        s = input(f"{prompt}{' [' + str(default) + ']' if default is not None else ''}: ").strip()
        return default if (s == "" and default is not None) else s
    def choose(self, prompt, options):
        print(prompt)
        for i, o in enumerate(options, 1):
            print(f"  {i}) {o}")
        while True:
            s = input("choice: ").strip()
            if s.isdigit() and 1 <= int(s) <= len(options):
                return int(s) - 1
            print("enter a number from the list")
    def confirm(self, prompt):
        return input(f"{prompt} [y/N]: ").strip().lower() in ("y", "yes")
    def say(self, msg): print(msg)


class Runner:
    def probe(self, profile): raise NotImplementedError
    def selftest(self, profile): raise NotImplementedError
    def import_daemon(self, source, config): raise NotImplementedError
    def endpoint_health(self, url): raise NotImplementedError
    def first_upload(self, db, cfg): raise NotImplementedError
    def sweep(self, profile): raise NotImplementedError


@dataclass
class Wizard:
    io: IO
    runner: Runner
    home: str | None = None
    state: dict = field(default_factory=dict)
    _steps: list = field(default_factory=list)


def run(w: Wizard) -> int:
    # Fail-closed at every step (spec): a step that returns ok=False offers fix/quit and
    # will not advance. Steps that only "soft-fail" (endpoint unreachable, no scan DB yet,
    # first-light sweep errored) return ok=True with a caveat summary, so they never loop.
    steps = w._steps if w._steps else _default_steps()
    for step in steps:
        while True:
            res = step(w)
            w.io.say(res.summary)
            if res.ok:
                break
            if not w.io.confirm("fix this and retry?"):
                w.io.say("setup not configured; nothing was written")
                return 2
    return 0


def _default_steps():
    return [step_preflight, step_pins, step_validate, step_location, step_upload, step_firstlight, step_write]


def diagnose(probe_result, selftest_result) -> str:
    if probe_result.get("verdict") != "GOOD":
        st = probe_result.get("status")
        if st == 0xFF or st is None:
            return ("no reply on SPI (status 0xFF): check SPI is enabled, the CS wiring, and power. "
                    "If your NSS is a GPIO line, confirm the pin number.")
        return f"probe returned status 0x{st:02X}: recheck the reset and SPI wiring."
    reason = (selftest_result or {}).get("reason", "")
    return {
        "busy-stuck": "the radio's BUSY line never cleared — the `busy` pin is probably wrong.",
        "init-timeout": "init timed out — check the `reset` and `dio1` pins.",
        "floor-out-of-range": "the noise floor is implausible — check the antenna/feedline or the `rxen` pin.",
    }.get(reason, f"selftest failed ({reason or 'unknown'}); recheck the pins.")


def step_preflight(w) -> StepResult:
    import os, importlib.util
    blockers = []
    spidev_nodes = [f"/dev/spidev{b}.{c}" for b in (0, 1) for c in (0, 1, 2)]
    present = [n for n in spidev_nodes if os.path.exists(n)]
    if not present:
        blockers.append("SPI is not enabled: `sudo raspi-config nonint do_spi 0` then reboot "
                        "(or add `dtparam=spi=on` to /boot/firmware/config.txt).")
    else:
        inaccessible = [n for n in present if not os.access(n, os.R_OK | os.W_OK)]
        if inaccessible:
            blockers.append(f"no read/write access to {inaccessible[0]}: add yourself to the `spi` "
                            f"group (`sudo usermod -aG spi $USER`), then log out and back in.")
    gpiochip = "/dev/gpiochip0"
    if os.path.exists(gpiochip) and not os.access(gpiochip, os.R_OK | os.W_OK):
        blockers.append(f"no read/write access to {gpiochip}: add yourself to the `gpio` group "
                        f"(`sudo usermod -aG gpio $USER`), then log out and back in.")
    for mod, apt in (("spidev", "python3-spidev"), ("gpiod", "python3-libgpiod")):
        if importlib.util.find_spec(mod) is None:
            blockers.append(f"the {mod} module is missing: `sudo apt install {apt}`.")
    if not blockers:
        return StepResult(True, "host looks ready (SPI present + accessible, spidev + gpiod importable).")
    for b in blockers:
        w.io.say("  - " + b)
    return StepResult(False, "host is not ready yet.", "\n".join(blockers))


def step_pins(w) -> StepResult:
    from .profile import load_profile, dump_profile, BoardProfile
    from . import paths
    import os
    choice = w.io.choose("Where should the radio pin settings come from?",
                         ["Import from meshtasticd or openHOP",
                          "Pick a shipped board profile",
                          "Enter the pins manually"])
    prof = None
    if choice == 0:
        source = w.io.ask("Which daemon? (meshtasticd/openhop)", "meshtasticd")
        config = w.io.ask("Config path (blank for the default)", "") or None
        resolved = w.runner.import_daemon(source, config)
        prof = resolved.profile
        loc = getattr(resolved, "location", None)
        if loc:
            w.state["location"] = (float(loc[0]), float(loc[1]))   # autoconf carries a 3rd "where from" item
    elif choice == 1:
        name = w.io.ask("Profile name (generic-spidev / nebra-duo-hat / meshtoad-v3-ch341)", "generic-spidev")
        prof = load_profile(name)
    else:
        w.io.say("Enter gpiochip0 line numbers (BCM on a Pi). NSS may be 'kernel' for a hardware CE.")
        nss = w.io.ask("nss (CS): 'kernel' or a GPIO number", "kernel")
        prof = BoardProfile(name="manual", bus_type="spidev", bus_dev=w.io.ask("spidev device", "/dev/spidev0.0"),
                            bus_hz=2_000_000,
                            pins={"nss": (int(nss) if nss.isdigit() else "kernel"),
                                  "reset": int(w.io.ask("reset", "22")), "busy": int(w.io.ask("busy", "23")),
                                  "dio1": int(w.io.ask("dio1", "24")),
                                  "rxen": None, "txen": None})
    pdir = paths.ensure_dir(paths.profiles_dir(w.home))      # <data dir>/profiles under -d, else ~/.config/lorascan/profiles
    path = os.path.join(pdir, f"{prof.name}.yaml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(dump_profile(prof, "written by lorascan setup"))
    w.state["profile_path"] = path
    return StepResult(True, f"radio profile written: {path}")


def step_validate(w) -> StepResult:
    prof = w.state["profile_path"]
    pr = w.runner.probe(prof)
    if pr.get("verdict") != "GOOD":
        return StepResult(False, "the radio did not answer.", diagnose(pr, None))
    st = w.runner.selftest(prof)
    if not st.get("ok"):
        return StepResult(False, "the radio answered but the self-test failed.", diagnose(pr, st))
    return StepResult(True, "radio validated: probe GOOD, self-test PASS.")


def step_location(w) -> StepResult:
    if w.state.get("location"):
        lat, lon = w.state["location"]
        if w.io.confirm(f"Use the location from the daemon config ({lat:.4f},{lon:.4f})?"):
            return StepResult(True, f"location set to {lat:.4f},{lon:.4f}.")
    while True:
        s = w.io.ask("Antenna location as lat,lon (blank to skip)", "")
        if not s:
            w.state.pop("location", None)
            return StepResult(True, "no location set (your share will carry no location).")
        try:
            lat, lon = (float(x) for x in s.split(","))
        except ValueError:
            w.io.say("  format is lat,lon e.g. 33.89,-84.25"); continue
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            w.state["location"] = (lat, lon)
            return StepResult(True, f"location set to {lat:.4f},{lon:.4f}.")
        w.io.say("  out of range: lat -90..90, lon -180..180")


def step_upload(w) -> StepResult:
    endpoint = w.io.ask("Community share endpoint", "https://share.lorascan.app")
    h = w.runner.endpoint_health(endpoint)
    if not h.get("ok"):
        w.io.say(f"  endpoint not reachable: {h.get('error') or h.get('status')}")
        w.state["endpoint"] = endpoint
        return StepResult(True, "endpoint saved but not verified; you can upload later with `lorascan upload`.")
    w.state["endpoint"] = endpoint
    w.io.say(f"  uploads are published on the community map at {endpoint.rstrip('/')}/ (your cell appears after the next refresh)")
    w.state.setdefault("granularity", "hour")
    if w.state.get("db") and w.io.confirm("Do a first upload now to confirm data flows?"):
        r = w.runner.first_upload(w.state["db"], w.state)
        if r.get("ok"):
            return StepResult(True, f"endpoint reachable; first upload accepted {r.get('accepted')} aggregates.")
        return StepResult(True, "endpoint reachable; first upload did not complete — retry later with `lorascan upload`.")
    return StepResult(True, "endpoint reachable and saved; upload after your first survey.")


def step_firstlight(w) -> StepResult:
    from .report.ascii import band_graph
    try:
        rows = w.runner.sweep(w.state["profile_path"])
    except Exception as e:
        return StepResult(True, f"(skipped the first-light graph: {e})")
    if not rows:
        return StepResult(True, "(no energy captured for the first-light graph)")
    w.io.say(band_graph(rows))
    return StepResult(True, "first light: the radio is hearing the band (above).")


def step_write(w) -> StepResult:
    from . import station_config as _sc
    from . import paths
    import os
    prof = w.state.get("profile_path")
    name = os.path.splitext(os.path.basename(prof))[0] if prof else None
    cfg = _sc.StationConfig(profile=name, location=w.state.get("location"),
                            endpoint=w.state.get("endpoint"), granularity=w.state.get("granularity", "hour"))
    path = _sc.save(cfg, path=paths.config_path(w.home))
    w.io.say(f"  wrote {path}")
    d = paths.data_dir()
    if d:
        w.io.say(f"  everything lorascan reads and writes lives in {d} — keep passing -d {d} (or set LORASCAN_DIR={d})")
        w.io.say(f"  next: `lorascan -d {d} scan survey --duration 2h` then `lorascan -d {d} share`")
    else:
        w.io.say(f"  config and profiles are in {os.path.dirname(path)} (use -d DIR to keep them, and every output, somewhere else)")
        w.io.say("  next: `lorascan scan survey --db site.db --duration 2h` then `lorascan share`")
    return StepResult(True, f"setup complete; config and profiles are in {os.path.dirname(path)}.")


# --------------------------------------------------------------------------------------------
# Non-interactive setup (Loomwave/lorascan#25): the same steps, every answer from a flag.
#
# For a cron job or a balena start script that must configure a station before openHOP starts:
# anything the daemon config cannot supply is passed as a flag and saved as the station config.
# Re-running is safe — same flags means "unchanged", exit 0, without touching the radio or the
# network; a flag that moves a value updates the config; the guided wizard is untouched.

DEFAULT_ENDPOINT = "https://share.lorascan.app"


class SetupNeedsInput(Exception):
    """A wizard prompt that no flag answers. `--non-interactive` stops here (exit 2) rather than
    silently accepting the wizard's default."""
    def __init__(self, prompt, flag):
        super().__init__('setup --non-interactive needs {0} (asked: "{1}")'.format(flag, prompt))
        self.prompt = prompt
        self.flag = flag


class SetupBadFlag(ValueError):
    """A flag value the wizard rejected (an unparsable/out-of-range --cell). Exit 2, no retry loop."""


@dataclass
class Flags:
    """The `setup --non-interactive` flags, as parsed by the CLI."""
    source: str | None = None          # --from meshtasticd|openhop
    config: str | None = None          # --config PATH (blank = the daemon's default path)
    profile: str | None = None         # --profile NAME_OR_PATH (instead of --from)
    cell: str | None = None            # --cell lat,lon  ("" or "none" = deliberately no location)
    endpoint: str | None = None        # --endpoint URL
    granularity: str | None = None     # --granularity hour|day
    skip_radio: bool = False           # --skip-radio: no preflight, no probe/selftest, no first light
    dry_run: bool = False              # --dry-run: print the target config, write nothing
    db: str | None = None              # --db: an existing scan database for the first upload


def flags_from_args(a) -> Flags:
    return Flags(source=getattr(a, "source", None), config=getattr(a, "config", None),
                 profile=getattr(a, "profile", None), cell=getattr(a, "cell", None),
                 endpoint=getattr(a, "endpoint", None), granularity=getattr(a, "granularity", None),
                 skip_radio=bool(getattr(a, "skip_radio", False)),
                 dry_run=bool(getattr(a, "dry_run", False)), db=getattr(a, "db", None))


class FlagsIO(IO):
    """Answers the wizard's prompts from the flags, matching on the prompt text the steps above
    ask. A prompt no flag covers raises SetupNeedsInput naming the flag that would answer it, so
    an unattended run never blocks on stdin and never silently takes a default."""

    # (substring of the prompt, Flags field, the flag, blank-when-absent)
    _ASK = (("Which daemon?", "source", "--from", False),
            ("Config path", "config", "--config", True),       # blank = the daemon's default path
            ("Profile name", "profile", "--profile", False),
            ("lat,lon", "cell", "--cell", False),
            ("share endpoint", "endpoint", "--endpoint", False))

    def __init__(self, flags: Flags):
        self.flags = flags
        self.said = []
        self._asked = set()

    def ask(self, prompt, default=None):
        for needle, name, flag, blank_ok in self._ASK:
            if needle in prompt:
                value = getattr(self.flags, name)
                if value is None and blank_ok:
                    return ""
                if value is None:
                    raise SetupNeedsInput(prompt, flag)
                if prompt in self._asked:
                    # the wizard re-asks only when it rejected the answer; never loop on a flag
                    raise SetupBadFlag('setup --non-interactive: the wizard rejected {0} {1!r} '
                                       '(it asked again: "{2}")'.format(flag, value, prompt))
                self._asked.add(prompt)
                value = str(value).strip()
                return "" if value.lower() == "none" else value
        # the manual pin prompts: there is no flag for a hand-wired board
        raise SetupNeedsInput(prompt, "--from or --profile (manual pin entry needs the wizard)")

    def choose(self, prompt, options):
        if "radio pin settings" in prompt:
            if self.flags.source:
                return 0                                  # import from meshtasticd / openHOP
            if self.flags.profile:
                return 1                                  # a shipped board profile
            raise SetupNeedsInput(prompt, "--from or --profile")
        raise SetupNeedsInput(prompt, "a flag")

    def confirm(self, prompt):
        if "location from the daemon config" in prompt and self.flags.cell is not None:
            return False                                  # an explicit --cell overrides the daemon
        return True

    def say(self, msg):
        self.said.append(str(msg))
        print(msg)


def _existing_config(home):
    """(config, it-exists): the station config already on disk, if any."""
    import os
    from . import paths, station_config as _sc
    for p in paths.config_paths(home):
        if os.path.exists(p):
            return _sc.load(home), True
    return _sc.StationConfig(), False


def _parse_cell(s):
    """(location, was-given). '' or 'none' means a deliberate no-location station."""
    if s is None:
        return None, False
    t = str(s).strip()
    if t == "" or t.lower() == "none":
        return None, True
    try:
        lat, lon = (float(x) for x in t.split(","))
    except ValueError:
        raise SetupBadFlag("--cell {0!r} is not lat,lon (e.g. --cell 33.89,-84.25)".format(s))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise SetupBadFlag("--cell {0} is out of range (lat -90..90, lon -180..180)".format(s))
    return (lat, lon), True


def _profile_exists(name, home) -> bool:
    import os
    from . import paths
    if not name:
        return False
    if "/" in name or name.endswith((".yaml", ".yml")):
        return os.path.isfile(name)
    return any(os.path.isfile(os.path.join(d, name + ".yaml")) for d in paths.profile_dirs(home))


def plan(existing, flags: Flags, home=None):
    """(target config, changed field names): the existing config with whatever the flags specify
    written over it. 'profile' in the changed list means the profile has to be derived again —
    that is the only thing that needs the daemon config and the radio."""
    import os
    from . import station_config as _sc
    if flags.source and flags.profile:
        raise SetupBadFlag("--from and --profile are alternatives: --from reads the daemon's pins, "
                           "--profile names a board profile. Pass one.")
    if flags.config and not flags.source:
        raise SetupBadFlag("--config names the config of a daemon: pass --from meshtasticd|openhop too.")
    changed = []
    profile = existing.profile
    if flags.profile:
        name = os.path.splitext(os.path.basename(flags.profile))[0]
        if name != profile:
            profile = name
            changed.append("profile")
    location = existing.location
    cell, given = _parse_cell(flags.cell)
    if given and cell != location:
        location = cell
        changed.append("location")
    endpoint = existing.endpoint or DEFAULT_ENDPOINT
    if flags.endpoint and flags.endpoint != endpoint:
        endpoint = flags.endpoint
        changed.append("endpoint")
    granularity = existing.granularity or "hour"
    if flags.granularity and flags.granularity != granularity:
        granularity = flags.granularity
        changed.append("granularity")
    if "profile" not in changed and not _profile_exists(profile, home):
        changed.append("profile")          # nothing to point at (first run) or the file is gone
    return _sc.StationConfig(profile=profile, location=location, endpoint=endpoint,
                             granularity=granularity), changed


def _noninteractive_steps(flags: Flags):
    if not flags.skip_radio:
        return _default_steps()
    radio = (step_preflight, step_validate, step_firstlight)
    return [s for s in _default_steps() if s not in radio]


def _fmt(value) -> str:
    if value is None:
        return "(none)"
    if isinstance(value, tuple):
        return "{0},{1}".format(value[0], value[1])
    return str(value)


def _say_dry_run(io, cfg_path, existing, have, target, changed, flags) -> None:
    io.say("setup: dry run, nothing written")
    io.say("  config: {0}{1}".format(cfg_path, "" if have else " (would be created)"))
    fields = (("profile", target.profile, existing.profile), ("location", target.location, existing.location),
              ("endpoint", target.endpoint, existing.endpoint), ("granularity", target.granularity, existing.granularity))
    for name, now, before in fields:
        if name == "profile" and "profile" in changed and not flags.profile:
            shown = "(imported from the {0} config)".format(flags.source or "daemon")
        else:
            shown = _fmt(now)
        if not have:
            io.say("    {0:<12} {1}  (new)".format(name + ":", shown))
        elif name in changed:
            io.say("    {0:<12} {1}  (was {2})".format(name + ":", shown, _fmt(before)))
        else:
            io.say("    {0:<12} {1}".format(name + ":", shown))
    if have and not changed:
        io.say("  nothing would change; a real run would exit 0 without touching the radio.")
    else:
        steps = ", ".join(s.__name__.replace("step_", "") for s in _noninteractive_steps(flags)) \
            if "profile" in changed or not have else "write"
        io.say("  steps a real run would take: {0}".format(steps))


def run_noninteractive(w: Wizard, flags: Flags) -> int:
    """`lorascan setup --non-interactive`. Exit 0 ok/unchanged/dry-run, 1 a step failed,
    2 a missing input or a bad flag value (raised as SetupNeedsInput / SetupBadFlag)."""
    import sys
    from . import paths, station_config as _sc
    from dataclasses import replace

    cfg_path = paths.config_path(w.home)
    existing, have = _existing_config(w.home)
    target, changed = plan(existing, flags, w.home)
    w.io = FlagsIO(replace(flags, endpoint=target.endpoint))      # the resolved endpoint answers the prompt

    if flags.dry_run:
        _say_dry_run(w.io, cfg_path, existing, have, target, changed, flags)
        return 0
    if have and not changed:
        w.io.say("setup: unchanged ({0})".format(cfg_path))
        return 0
    if have and "profile" not in changed:
        # only config fields moved: write them, leave the radio and the endpoint alone
        w.io.say("setup: updated {0}".format(", ".join(changed)))
        w.io.say("  wrote {0}".format(_sc.save(target, path=cfg_path)))
        return 0

    w.state.setdefault("granularity", target.granularity)
    if target.location:
        w.state["location"] = target.location                     # kept unless --cell overrides it
    if flags.db:
        w.state.setdefault("db", flags.db)
    for step in _noninteractive_steps(flags):
        res = step(w)
        w.io.say(res.summary)
        if res.ok:
            continue
        if step is step_upload and flags.endpoint:
            w.state["endpoint"] = flags.endpoint                  # as the wizard does: save, verify later
            continue
        print("lorascan: setup failed: {0}\n{1}".format(res.summary, res.detail).rstrip(), file=sys.stderr)
        return 1
    if flags.skip_radio:
        w.io.say("  radio checks skipped (--skip-radio): no preflight, probe, self-test or first light — "
                 "run `lorascan selftest` once the radio is free.")
    if have:
        w.io.say("setup: updated {0}".format(", ".join(changed)))
    else:
        w.io.say("setup: written {0}".format(cfg_path))
    return 0
