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
    return [step_preflight, step_pins, step_validate]   # remaining Task-8 steps append here


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
    if not any(os.path.exists(f"/dev/spidev{b}.{c}") for b in (0, 1) for c in (0, 1, 2)):
        blockers.append("SPI is not enabled: `sudo raspi-config nonint do_spi 0` then reboot "
                        "(or add `dtparam=spi=on` to /boot/firmware/config.txt).")
    for mod, apt in (("spidev", "python3-spidev"), ("gpiod", "python3-libgpiod")):
        if importlib.util.find_spec(mod) is None:
            blockers.append(f"the {mod} module is missing: `sudo apt install {apt}`.")
    if not blockers:
        return StepResult(True, "host looks ready (SPI present, spidev + gpiod importable).")
    for b in blockers:
        w.io.say("  - " + b)
    return StepResult(False, "host is not ready yet.", "\n".join(blockers))


def step_pins(w) -> StepResult:
    from .profile import load_profile, dump_profile, BoardProfile
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
        if getattr(resolved, "location", None):
            w.state["location"] = resolved.location
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
    base = w.home if w.home is not None else os.path.expanduser("~")
    pdir = os.path.join(base, ".config", "lorascan", "profiles")
    os.makedirs(pdir, exist_ok=True)
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
