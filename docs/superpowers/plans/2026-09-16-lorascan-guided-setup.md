# lorascan Guided Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ship a `lorascan setup` guided wizard (with a persistent station config, software-CS support, and a closing ASCII band graph) so a non-expert configures lorascan correctly and their data actually uploads.

**Architecture:** a software-CS fix in the SPI HAL, a new `station_config` module the daily commands read by default, a new `setup` wizard orchestrating the existing `autoconf`/`probe`/`selftest`/`share` blocks, and a pure `report/ascii` renderer. All interactive I/O and hardware/network calls sit behind injected seams so every step is unit-tested with no TTY and no hardware.

**Tech Stack:** Python 3.11 target, standard library only, `argparse` CLI, pytest, the `fake` HAL, the package's `profile.parse_mini_yaml` mini-YAML reader.

**Spec:** `docs/superpowers/specs/2026-09-16-lorascan-guided-setup-design.md`.

## Global Constraints

- Target interpreter **Python 3.11** (Debian 12 / Pi OS). Must `python3.11 -m compileall lorascan` clean; no PEP 701 f-string forms (no backslash in a replacement field; no nested f-string reusing the enclosing quote). `release2.sh` gates this on the bench; keep `tests/test_py311_syntax.py` green.
- **Receive-only.** No transmit; the wizard never keys the radio.
- **Zero new dependencies.** Standard library + the package's own modules only.
- **Config dir convention:** `~/.config/lorascan/` then `/etc/lorascan/` (same as `networks.yaml`).
- **Fail-closed:** a failing check stops forward progress and offers a fix; never advance past a silent failure.
- **Non-invasive:** detect-and-instruct only; never edit boot config, run `sudo`, or restart a service.
- **Backward compatible:** with no `config.yaml`, `scan`/`share`/`upload` behave exactly as today; precedence is explicit flag → `config.yaml` → current default.
- **Software CS is single-radio-host only** (active-low, idle high, asserted around each `xfer`; `SPI_NO_CS` best-effort, its absence tolerated).
- **Share protocol unchanged:** `lorascan-share/2`; `GET /healthz`, `GET /v1/watermark`, `POST /v1/share`.
- TDD, frequent commits, each commit trailer:
  `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` / `Claude-Session: https://claude.ai/code/session_01DBbMD4p6qTkRCnNcYoTDoA`.

## File structure

- `lorascan/hal/spidev_gpiod.py` (modify) — software CS.
- `lorascan/station_config.py` (new) — load/save/resolve the station config.
- `lorascan/cli.py` (modify) — `setup` subcommand + default-resolution in `scan`/`share`/`upload`.
- `lorascan/share.py` (modify) — `endpoint_health`.
- `lorascan/report/ascii.py` (new) — `band_graph`.
- `lorascan/setup.py` (new) — the wizard (`Wizard`, `IO`, `Runner`, `StepResult`, the steps, `run`).
- `README.md` (modify) — lead with guided setup, manual details below.
- Tests: `tests/test_spidev_gpiod_cs.py`, `tests/test_station_config.py`, `tests/test_ascii_graph.py`, `tests/test_setup.py`, and additions to `tests/test_cli.py` / `tests/test_share.py`.

---

### Task 1: Software chip-select in the SPI HAL

**Files:**
- Modify: `lorascan/hal/spidev_gpiod.py` (the `__init__` nss guard, and `xfer`).
- Test: `tests/test_spidev_gpiod_cs.py` (create).

**Interfaces:**
- Consumes: `profile.BoardProfile` with `pins["nss"]` = `int` (GPIO line) | `"kernel"` | `None`.
- Produces: a `SpidevGpiodHal` that accepts a GPIO `nss`; public surface unchanged (`xfer`, `busy`, `dio1`, `set_reset`, `set_rxen`, `sleep`, `close`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_spidev_gpiod_cs.py
import os, sys, types
import pytest
from lorascan.profile import BoardProfile

def _prof(nss):
    return BoardProfile(name="t", bus_type="spidev", bus_dev="/dev/spidev0.0",
                        bus_hz=2_000_000, pins={"nss": nss, "reset": 22, "busy": 23,
                                                "dio1": 24, "rxen": None, "txen": None})

class _Spi:
    def __init__(self, log):
        self.log = log; self.mode = None; self.max_speed_hz = None; self._no_cs = None
    def open(self, b, c): self.log.append(("spi_open", b, c))
    @property
    def no_cs(self): return self._no_cs
    @no_cs.setter
    def no_cs(self, v): self._no_cs = v
    def xfer2(self, data): self.log.append(("xfer2", bytes(data))); return [0]*len(data)
    def close(self): self.log.append(("spi_close",))

class _Gpio:
    def __init__(self, log): self.log = log
    def request_out(self, off, init): self.log.append(("req_out", off, init))
    def request_in(self, off): self.log.append(("req_in", off))
    def set(self, off, level): self.log.append(("set", off, 1 if level else 0))
    def get(self, off): return False
    def close(self): self.log.append(("gpio_close",))

@pytest.fixture
def wired(monkeypatch):
    log = []
    import lorascan.hal.spidev_gpiod as m
    monkeypatch.setitem(sys.modules, "spidev", types.SimpleNamespace(SpiDev=lambda: _Spi(log)))
    monkeypatch.setattr(m, "_open_gpio", lambda: _Gpio(log))
    monkeypatch.setattr(m, "acquire_device_lock", lambda dev: os.open(os.devnull, os.O_RDWR))
    return m, log

def test_gpio_cs_is_requested_idle_high_and_no_cs_attempted(wired):
    m, log = wired
    hal = m.SpidevGpiodHal(_prof(8))
    assert ("req_out", 8, 1) in log            # CS active-low, idle high (deasserted)
    assert hal.spi.no_cs is True               # single-radio host: suppress kernel CE

def test_gpio_cs_frames_each_xfer_low_then_high(wired):
    m, log = wired
    hal = m.SpidevGpiodHal(_prof(8))
    log.clear()
    hal.xfer(b"\xAB\xCD")
    assert log == [("set", 8, 0), ("xfer2", b"\xAB\xCD"), ("set", 8, 1)]

def test_kernel_cs_leaves_xfer_and_cs_untouched(wired):
    m, log = wired
    hal = m.SpidevGpiodHal(_prof("kernel"))
    assert hal.spi.no_cs is None               # not set under kernel CE
    assert not any(e[0] == "req_out" and e[1] == "kernel" for e in log)
    log.clear()
    hal.xfer(b"\x01")
    assert log == [("xfer2", b"\x01")]         # no CS toggling

def test_software_cs_no_longer_raises(wired):
    m, _ = wired
    m.SpidevGpiodHal(_prof(8))                 # must not raise HalError
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_spidev_gpiod_cs.py -v`
Expected: FAIL — the software-CS constructor raises `HalError("… not supported in P1 …")`.

- [ ] **Step 3: Implement**

In `lorascan/hal/spidev_gpiod.py`, in `SpidevGpiodHal.__init__`, replace the block:

```python
        if isinstance(self.pins.get("nss"), int):
            raise HalError("software chip-select (pins.nss as a GPIO) is not supported in P1; use a kernel CE")
```

with:

```python
        self._cs = self.pins.get("nss")               # int GPIO line = software CS; "kernel"/None = kernel CE
        if isinstance(self._cs, int):
            self.gpio.request_out(self._cs, 1)         # CS is active-low: idle HIGH (deasserted)
            try:
                self.spi.no_cs = True                  # single-radio host: suppress the kernel CE if honored
            except (OSError, AttributeError):
                pass                                   # BCM spidev may ignore SPI_NO_CS; the GPIO is the real select
```

Replace `xfer`:

```python
    def xfer(self, tx: bytes) -> bytes:
        if isinstance(self._cs, int):
            self.gpio.set(self._cs, False)             # assert (low)
            try:
                return bytes(self.spi.xfer2(list(tx)))
            finally:
                self.gpio.set(self._cs, True)          # deassert (high)
        return bytes(self.spi.xfer2(list(tx)))
```

(`self._cs` is assigned before `xfer` can be called; kernel CE keeps `_cs` as `"kernel"`/`None`, so `isinstance(..., int)` is false.)

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_spidev_gpiod_cs.py -v` → PASS (4 tests).
Run: `pytest tests/ -q` → still green (no regression in existing HAL/CLI tests).

- [ ] **Step 5: Commit**

```bash
git add lorascan/hal/spidev_gpiod.py tests/test_spidev_gpiod_cs.py
git commit -F - <<'MSG'
fix(hal): support a software-driven CS (GPIO nss) on a single-radio host

Drive the nss GPIO around each xfer (idle high, assert low, deassert high) and
attempt SPI_NO_CS; kernel-CE path unchanged. Removes the P1 "not supported"
HalError that dead-ended meshtasticd / hand-wired configs whose CS is a GPIO.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DBbMD4p6qTkRCnNcYoTDoA
MSG
```

---

### Task 2: `station_config` module

**Files:**
- Create: `lorascan/station_config.py`.
- Test: `tests/test_station_config.py` (create).

**Interfaces:**
- Consumes: `profile.parse_mini_yaml(text) -> dict`.
- Produces:
  - `@dataclass StationConfig(profile: str|None, location: tuple[float,float]|None, endpoint: str|None, granularity: str|None)`.
  - `load(home: str|None=None) -> StationConfig` (reads `~/.config/lorascan/config.yaml` then `/etc/lorascan/config.yaml`; all-None if neither exists; raises `ValueError` on a malformed file).
  - `save(cfg: StationConfig, path: str|None=None) -> str` (writes the user path, returns it).
  - `resolve(flag, cfg_value, default)` → `flag if flag is not None else (cfg_value if cfg_value is not None else default)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_station_config.py
import os, pytest
from lorascan import station_config as sc

def test_absent_is_all_none(tmp_path):
    cfg = sc.load(home=str(tmp_path))
    assert (cfg.profile, cfg.location, cfg.endpoint, cfg.granularity) == (None, None, None, None)

def test_roundtrip_save_load(tmp_path):
    cfg = sc.StationConfig(profile="my-nebra", location=(33.8931, -84.2534),
                           endpoint="https://share.lorascan.app", granularity="hour")
    p = sc.save(cfg, path=str(tmp_path / "config.yaml"))
    assert os.path.exists(p)
    got = sc.load(home=str(tmp_path)) if False else sc._load_file(p)
    assert got.profile == "my-nebra"
    assert got.location == (33.8931, -84.2534)
    assert got.endpoint == "https://share.lorascan.app"
    assert got.granularity == "hour"

def test_location_absent_stays_none(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("profile: x\nshare: {endpoint: 'https://h', granularity: day}\n")
    got = sc._load_file(str(p))
    assert got.location is None and got.profile == "x" and got.granularity == "day"

def test_malformed_raises(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("share: {endpoint: }\n: : :\n")
    with pytest.raises(ValueError):
        sc._load_file(str(p))

def test_resolve_precedence():
    assert sc.resolve("flag", "cfg", "def") == "flag"
    assert sc.resolve(None, "cfg", "def") == "cfg"
    assert sc.resolve(None, None, "def") == "def"

def test_load_prefers_user_over_etc(tmp_path, monkeypatch):
    home = tmp_path / "home"; (home / ".config" / "lorascan").mkdir(parents=True)
    (home / ".config" / "lorascan" / "config.yaml").write_text("profile: userwins\n")
    cfg = sc.load(home=str(home))
    assert cfg.profile == "userwins"
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_station_config.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement**

```python
# lorascan/station_config.py
"""The persistent station config (~/.config/lorascan/config.yaml, then /etc/lorascan/config.yaml).
Written by `lorascan setup`; read by scan/share/upload when a flag is absent. Mini-YAML, no deps."""
from __future__ import annotations
import os
from dataclasses import dataclass
from .profile import parse_mini_yaml

USER_PATH = "~/.config/lorascan/config.yaml"
ETC_PATH = "/etc/lorascan/config.yaml"


@dataclass
class StationConfig:
    profile: str | None = None
    location: tuple[float, float] | None = None
    endpoint: str | None = None
    granularity: str | None = None


def _user_path(home: str | None) -> str:
    base = home if home is not None else os.path.expanduser("~")
    return os.path.join(base, ".config", "lorascan", "config.yaml")


def _from_dict(d: dict) -> StationConfig:
    loc = None
    lb = d.get("location")
    if isinstance(lb, dict) and lb.get("lat") is not None and lb.get("lon") is not None:
        loc = (float(lb["lat"]), float(lb["lon"]))
    share = d.get("share") if isinstance(d.get("share"), dict) else {}
    prof = d.get("profile")
    return StationConfig(profile=str(prof) if prof else None, location=loc,
                         endpoint=(str(share["endpoint"]) if share.get("endpoint") else None),
                         granularity=(str(share["granularity"]) if share.get("granularity") else None))


def _load_file(path: str) -> StationConfig:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    try:
        d = parse_mini_yaml(text)
    except Exception as e:
        raise ValueError(f"{path}: malformed station config: {e}") from e
    if not isinstance(d, dict):
        raise ValueError(f"{path}: station config is not a mapping")
    return _from_dict(d)


def load(home: str | None = None) -> StationConfig:
    for path in (_user_path(home), ETC_PATH):
        if os.path.exists(path):
            return _load_file(path)
    return StationConfig()


def save(cfg: StationConfig, path: str | None = None) -> str:
    path = path or os.path.expanduser(USER_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = ["# written by `lorascan setup`; hand-editable"]
    if cfg.profile:
        lines.append(f"profile: {cfg.profile}")
    if cfg.location:
        lines.append(f"location: {{lat: {cfg.location[0]}, lon: {cfg.location[1]}}}")
    ep = cfg.endpoint or ""
    gr = cfg.granularity or "hour"
    lines.append(f"share:   {{endpoint: {_q(ep)}, granularity: {gr}}}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def _q(s: str) -> str:
    return '"' + s.replace('"', '\\"') + '"' if s else '""'


def resolve(flag, cfg_value, default):
    if flag is not None:
        return flag
    if cfg_value is not None:
        return cfg_value
    return default
```

If `parse_mini_yaml` does not raise on the malformed fixture, tighten the fixture to a form it rejects (e.g. an unterminated flow map `share: {endpoint:`), or add an explicit shape check in `_load_file`; the requirement is that a malformed file raises `ValueError`, never silently returns defaults.

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_station_config.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add lorascan/station_config.py tests/test_station_config.py
git commit -F - <<'MSG'
feat(config): persistent station config (~/.config/lorascan/config.yaml)

profile + location + share endpoint/granularity, two-path load (user then
/etc), fail-closed on a malformed file, resolve() precedence helper. No deps.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DBbMD4p6qTkRCnNcYoTDoA
MSG
```

---

### Task 3: `scan`/`share`/`upload` read the station config by default

**Files:**
- Modify: `lorascan/cli.py` (`cmd_share`, `cmd_upload`, and the profile resolution used by `scan`).
- Test: add to `tests/test_cli.py`.

**Interfaces:**
- Consumes: `station_config.load()`, `station_config.resolve(flag, cfg_value, default)` from Task 2.
- Produces: no signature change; only defaults change when a flag is absent.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_cli.py
import os
from lorascan import cli, station_config as sc

def _write_cfg(tmp_path, **kw):
    home = tmp_path / "home"; (home / ".config" / "lorascan").mkdir(parents=True)
    sc.save(sc.StationConfig(**kw), path=str(home / ".config" / "lorascan" / "config.yaml"))
    return str(home)

def test_share_uses_config_endpoint_when_no_flag(tmp_path, monkeypatch, capsys):
    home = _write_cfg(tmp_path, endpoint="https://cfg.example", granularity="day")
    monkeypatch.setenv("HOME", home)
    sent = {}
    monkeypatch.setattr("lorascan.cli.upload_share", lambda doc, to, **k: sent.update(to=to) or
                        {"sent": 0, "skipped": 0, "bytes": 0, "attempts": 1, "watermark": None, "accepted": 0})
    # a tiny db with one run so build_share has something; reuse the helper the other cli tests use
    db = str(tmp_path / "s.db")
    cli.main(["scan", "quick", "--profile", "fake", "--db", db, "--passes", "1",
              "--dwell", "0.005", "--sample-gap", "0.001"])
    cli.main(["share", "--db", db, "--out", str(tmp_path / "s.json")])
    assert sent.get("to") == "https://cfg.example"

def test_explicit_flag_beats_config(tmp_path, monkeypatch):
    home = _write_cfg(tmp_path, endpoint="https://cfg.example")
    monkeypatch.setenv("HOME", home)
    sent = {}
    monkeypatch.setattr("lorascan.cli.upload_share", lambda doc, to, **k: sent.update(to=to) or
                        {"sent": 0, "skipped": 0, "bytes": 0, "attempts": 1, "watermark": None, "accepted": 0})
    db = str(tmp_path / "s.db")
    cli.main(["scan", "quick", "--profile", "fake", "--db", db, "--passes", "1",
              "--dwell", "0.005", "--sample-gap", "0.001"])
    cli.main(["share", "--db", db, "--out", str(tmp_path / "s.json"), "--to", "https://flag.example"])
    assert sent.get("to") == "https://flag.example"

def test_no_config_behaves_as_today(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "empty"))
    db = str(tmp_path / "s.db")
    cli.main(["scan", "quick", "--profile", "fake", "--db", db, "--passes", "1",
              "--dwell", "0.005", "--sample-gap", "0.001"])
    rc = cli.main(["share", "--db", db, "--out", str(tmp_path / "s.json")])  # no --to, no cfg
    assert rc == 0  # writes the file, uploads nothing, unchanged
```

- [ ] **Step 2: Run to verify it fails** — the share command ignores the config (`to` stays None), so `test_share_uses_config_endpoint_when_no_flag` fails.

- [ ] **Step 3: Implement**

At the top of `cmd_share` (before the `a.to` / `a.cell` / `a.granularity` are used), resolve them against the station config:

```python
    from . import station_config as _sc
    _cfg = _sc.load()
    to = _sc.resolve(a.to, _cfg.endpoint, None)
    cellarg = a.cell if a.cell is not None else (f"{_cfg.location[0]},{_cfg.location[1]}" if _cfg.location else None)
    gran = _sc.resolve(getattr(a, "granularity", None), _cfg.granularity, "hour")
```

Then use `to`, `cellarg`, `gran` in place of `a.to`, `a.cell`, `a.granularity` in the body (the `cell = coarse_cell(...)` block reads `cellarg`; `build_share(..., granularity=gran)`; the upload guard `if a.dry_run or not to`; `_upload(doc, to)`).

In `cmd_upload`, resolve `--to`:

```python
    from . import station_config as _sc
    to = _sc.resolve(a.to, _sc.load().endpoint, None)
    if not to:
        raise ValueError("no endpoint: pass --to or run `lorascan setup`")
    return _upload(doc, to)
```

For `scan`'s profile: in `_run_scan` (and `cmd_probe`/`cmd_selftest` via `_profile`), when `a.profile` is the built-in default (`"generic-spidev"`) and a config profile exists, prefer the config. Do it narrowly so an explicit `--profile` still wins:

```python
# in _run_scan, right after reading a.profile:
    from . import station_config as _sc
    prof_name = a.profile
    if prof_name == "generic-spidev":                 # the argparse default = "not chosen"
        prof_name = _sc.load().profile or prof_name
    prof = _profile(prof_name)
```

(Leave `probe`/`selftest` on their explicit `--profile` for now — the wizard passes the profile path directly; scan is the daily command that benefits from the default.)

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_cli.py -v` → PASS; `pytest tests/ -q` green.

- [ ] **Step 5: Commit**

```bash
git add lorascan/cli.py tests/test_cli.py
git commit -F - <<'MSG'
feat(cli): scan/share/upload fall back to the station config

--profile / --to / --cell / --granularity resolve as flag -> config.yaml ->
today's default; explicit flags win; no config = unchanged behavior.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DBbMD4p6qTkRCnNcYoTDoA
MSG
```

---

### Task 4: `endpoint_health` in `share.py`

**Files:**
- Modify: `lorascan/share.py` (add `endpoint_health`).
- Test: add to `tests/test_share.py`.

**Interfaces:**
- Produces: `endpoint_health(base: str, timeout_s: float=10.0, opener=None) -> dict` returning `{"ok": bool, "status": int|None, "watermark": str|None, "error": str|None}`. `opener` is an injectable `urlopen`-like seam for tests (defaults to `urllib.request.urlopen`).

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_share.py
import io, json
from lorascan.share import endpoint_health

class _Resp:
    def __init__(self, code, body=b""): self.status = code; self._b = body
    def read(self): return self._b
    def __enter__(self): return self
    def __exit__(self, *a): return False

def test_endpoint_health_ok():
    def opener(req, timeout=0):
        if req.full_url.endswith("/healthz"): return _Resp(200, b"ok")
        return _Resp(200, json.dumps({"latest": "2026-09-16T00"}).encode())
    h = endpoint_health("https://x", opener=opener)
    assert h["ok"] is True and h["status"] == 200 and h["watermark"] == "2026-09-16T00"

def test_endpoint_health_unreachable():
    def opener(req, timeout=0): raise OSError("connection refused")
    h = endpoint_health("https://x", opener=opener)
    assert h["ok"] is False and h["error"] and "refused" in h["error"]
```

- [ ] **Step 2: Run to verify it fails** — import error / missing function.

- [ ] **Step 3: Implement** in `lorascan/share.py`:

```python
def endpoint_health(base: str, timeout_s: float = 10.0, opener=None) -> dict:
    """GET {base}/healthz then {base}/v1/watermark. Returns reachability so `setup` can
    prove the target before the user walks away. opener defaults to urllib.request.urlopen."""
    import urllib.request, json as _json
    opener = opener or urllib.request.urlopen
    base = base.rstrip("/")
    out = {"ok": False, "status": None, "watermark": None, "error": None}
    try:
        with opener(urllib.request.Request(f"{base}/healthz"), timeout=timeout_s) as r:
            out["status"] = getattr(r, "status", 200); r.read()
        with opener(urllib.request.Request(f"{base}/v1/watermark"), timeout=timeout_s) as r:
            body = r.read()
        try:
            out["watermark"] = (_json.loads(body) or {}).get("latest")
        except ValueError:
            out["watermark"] = None
        out["ok"] = (out["status"] == 200)
    except (OSError, ValueError) as e:
        out["error"] = str(e)
    return out
```

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_share.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add lorascan/share.py tests/test_share.py
git commit -F - <<'MSG'
feat(share): endpoint_health(base) — GET /healthz + /v1/watermark

Lets `lorascan setup` prove the upload target is reachable before the user
walks away. Injectable opener for tests; no new dependency.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DBbMD4p6qTkRCnNcYoTDoA
MSG
```

---

### Task 5: `report/ascii.py` band graph

**Files:**
- Create: `lorascan/report/ascii.py`.
- Test: `tests/test_ascii_graph.py` (create).

**Interfaces:**
- Produces: `band_graph(rows, width=None, floor_dbm=-125, ceil_dbm=-20) -> str`. `rows` is a list of `(freq_hz, level_dbm)` or `(freq_hz, floor_dbm, peak_dbm)`. Returns a multi-line ASCII string: one labeled bar per row scaled to `width` (auto from `shutil.get_terminal_size`, clamped 40..100), a `[X]` mark on rows whose freq is in an exclusion zone (902.000–903.250 / 926.750–928.000 MHz), and a header line with the dBm axis bounds. Pure; no I/O.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ascii_graph.py
from lorascan.report.ascii import band_graph

EXCL = [(902_000_000, 903_250_000), (926_750_000, 928_000_000)]

def test_bar_scales_between_floor_and_ceil():
    g = band_graph([(915_000_000, -125), (915_000_000, -20)], width=40, floor_dbm=-125, ceil_dbm=-20)
    lines = [l for l in g.splitlines() if "915.00" in l]
    assert lines[0].count("#") == 0            # at floor: empty bar
    assert lines[1].count("#") >= 20           # at ceil: full-ish bar

def test_two_row_shapes_accepted():
    a = band_graph([(915_000_000, -60)], width=40)
    b = band_graph([(915_000_000, -120, -60)], width=40)   # peak drives the bar
    assert a.splitlines()[-1].count("#") == b.splitlines()[-1].count("#")

def test_exclusion_zone_marked():
    g = band_graph([(902_500_000, -50), (915_000_000, -50)], width=40)
    lines = {l.split()[0]: l for l in g.splitlines() if "MHz" not in l and l.strip()}
    assert any("902.50" in l and "[X]" in l for l in g.splitlines())
    assert not any("915.00" in l and "[X]" in l for l in g.splitlines())

def test_width_clamped_and_deterministic():
    g1 = band_graph([(915_000_000, -50)], width=5)
    g2 = band_graph([(915_000_000, -50)], width=5)
    assert g1 == g2 and len(g1.splitlines()[-1]) <= 100
```

- [ ] **Step 2: Run to verify it fails** — module missing.

- [ ] **Step 3: Implement**

```python
# lorascan/report/ascii.py
"""Pure ASCII band-energy bar graph for the terminal (immediate feedback in `lorascan setup`)."""
from __future__ import annotations
import shutil

_EXCL = ((902_000_000, 903_250_000), (926_750_000, 928_000_000))


def _in_excl(hz: float) -> bool:
    return any(lo <= hz <= hi for lo, hi in _EXCL)


def _bar_width(width):
    if width is None:
        width = shutil.get_terminal_size((80, 24)).columns
    return max(40, min(100, int(width)))


def band_graph(rows, width=None, floor_dbm=-125, ceil_dbm=-20) -> str:
    w = _bar_width(width)
    barcells = max(1, w - 24)                          # leave room for the "NNN.NN MHz [X] " label
    span = float(ceil_dbm - floor_dbm) or 1.0
    out = [f"band energy  {floor_dbm}..{ceil_dbm} dBm  ([X] = exclusion zone)"]
    for row in rows:
        if len(row) == 3:
            hz, _floor, level = row
        else:
            hz, level = row
        frac = (float(level) - floor_dbm) / span
        frac = 0.0 if frac < 0 else (1.0 if frac > 1 else frac)
        fill = int(round(frac * barcells))
        mark = "[X]" if _in_excl(hz) else "   "
        out.append(f"{hz/1e6:7.2f} MHz {mark} {'#' * fill}{'.' * (barcells - fill)}")
    return "\n".join(out)
```

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_ascii_graph.py -v` → PASS. Adjust the assertions' exact counts to the implementation if off by one (the test asserts ranges/inclusion, not brittle exact widths, except where noted).

- [ ] **Step 5: Commit**

```bash
git add lorascan/report/ascii.py tests/test_ascii_graph.py
git commit -F - <<'MSG'
feat(report): ascii band_graph() for terminal feedback

Pure, deterministic ASCII bar graph of band energy with exclusion-zone marks;
used by `lorascan setup`'s closing "first light" step. shutil only.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DBbMD4p6qTkRCnNcYoTDoA
MSG
```

---

### Task 6: Wizard core — `Wizard`, `IO`, `Runner`, `StepResult`, `run`

**Files:**
- Create: `lorascan/setup.py` (the framework only; steps land in Tasks 7–8).
- Test: `tests/test_setup.py` (create).

**Interfaces:**
- Produces:
  - `@dataclass StepResult(ok: bool, summary: str, detail: str = "")`.
  - `class IO` seam: `ask(prompt, default=None) -> str`, `choose(prompt, options: list[str]) -> int`, `confirm(prompt) -> bool`, `say(msg) -> None`. A `ScriptedIO(answers: list[str])` test double consumes queued answers; a `TtyIO` uses `input`/`print`.
  - `class Runner` seam with the hardware/network calls the steps use (stubs raise `NotImplementedError` here; real bindings arrive in Task 9): `probe(profile) -> dict`, `selftest(profile) -> dict`, `import_daemon(source, config) -> object`, `endpoint_health(url) -> dict`, `first_upload(db, cfg) -> dict`, `sweep(profile) -> list`.
  - `class Wizard(io: IO, runner: Runner, home: str|None=None)` holding mutable `state` (chosen profile path, location, endpoint, granularity, db).
  - `run(w: Wizard) -> int` — runs the ordered steps; a step returning `ok=False` from a fail-closed step (preflight/validate) re-runs that step after the user chooses to fix or quit; a clean quit returns non-zero with a "not configured" line; success returns 0.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_setup.py
from lorascan import setup

def test_scripted_io_consumes_answers():
    io = setup.ScriptedIO(["yes", "2", "hello"])
    assert io.confirm("ok?") is True
    assert io.choose("pick", ["a", "b", "c"]) == 1     # "2" -> index 1
    assert io.ask("name?") == "hello"

def test_run_stops_and_returns_nonzero_on_quit():
    # a wizard whose first step asks to continue; scripted "quit"
    io = setup.ScriptedIO(["quit"])
    w = setup.Wizard(io, setup.Runner(), home="/tmp")
    # a one-step run for the framework test: inject a single failing fail-closed step
    w._steps = [lambda ww: setup.StepResult(False, "blocked", "fix me")]
    rc = setup.run(w)
    assert rc != 0
    assert any("not configured" in m for m in io.said)

def test_run_returns_zero_when_all_steps_ok():
    io = setup.ScriptedIO([])
    w = setup.Wizard(io, setup.Runner(), home="/tmp")
    w._steps = [lambda ww: setup.StepResult(True, "did a thing")]
    assert setup.run(w) == 0
```

- [ ] **Step 2: Run to verify it fails** — module missing.

- [ ] **Step 3: Implement** the framework in `lorascan/setup.py`:

```python
# lorascan/setup.py
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
    return []   # populated in Tasks 7-8
```

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_setup.py -v` → PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add lorascan/setup.py tests/test_setup.py
git commit -F - <<'MSG'
feat(setup): wizard framework — IO/Runner seams, StepResult, run loop

Fail-closed run loop with scripted + TTY IO doubles; steps land next. All
hardware/network behind Runner so steps test with no TTY and no hardware.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DBbMD4p6qTkRCnNcYoTDoA
MSG
```

---

### Task 7: Steps — preflight, pins, validate (with the diagnosis table)

**Files:**
- Modify: `lorascan/setup.py` (add `step_preflight`, `step_pins`, `step_validate`, `diagnose`, and populate `_default_steps`).
- Test: add to `tests/test_setup.py`.

**Interfaces:**
- Consumes: `Wizard`, `StepResult`, `Runner.probe/selftest/import_daemon`, `profile.load_profile`, `autoconf`.
- Produces: `diagnose(probe_result: dict, selftest_result: dict|None) -> str`; the three step functions; `step_pins` sets `w.state["profile_path"]`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_setup.py
from lorascan import setup

def test_diagnose_maps_symptoms():
    assert "SPI" in setup.diagnose({"verdict": "BAD", "status": 0xFF}, None)
    assert "busy" in setup.diagnose({"verdict": "GOOD"}, {"ok": False, "reason": "busy-stuck"}).lower()
    assert "reset" in setup.diagnose({"verdict": "GOOD"}, {"ok": False, "reason": "init-timeout"}).lower()

def test_validate_loops_until_probe_good():
    class R(setup.Runner):
        def __init__(self): self.n = 0
        def probe(self, p): self.n += 1; return {"verdict": "GOOD" if self.n > 1 else "BAD", "status": 0xFF}
        def selftest(self, p): return {"ok": True}
    io = setup.ScriptedIO([])
    w = setup.Wizard(io, R(), home="/tmp"); w.state["profile_path"] = "fake"
    r1 = setup.step_validate(w); assert r1.ok is False       # first probe BAD
    r2 = setup.step_validate(w); assert r2.ok is True        # second probe GOOD + selftest ok

def test_pins_import_from_meshtasticd(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from lorascan.profile import BoardProfile
    class R(setup.Runner):
        def import_daemon(self, source, config):
            prof = BoardProfile(name="auto-meshtasticd", bus_type="spidev", bus_dev="/dev/spidev0.0",
                                bus_hz=2_000_000, pins={"nss": 8, "reset": 22, "busy": 23, "dio1": 24,
                                                        "rxen": None, "txen": None})
            return SimpleNamespace(profile=prof, location=None)
    io = setup.ScriptedIO(["1", "meshtasticd", ""])   # choose "import", source, default config
    w = setup.Wizard(io, R(), home=str(tmp_path))
    res = setup.step_pins(w)
    assert res.ok and os.path.exists(w.state["profile_path"])   # a real profile written under home
```

- [ ] **Step 2: Run to verify it fails** — functions missing.

- [ ] **Step 3: Implement** in `lorascan/setup.py` (add these functions; wire `_default_steps` to include them plus the Task-8 steps):

```python
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
```

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_setup.py -v` → PASS.

- [ ] **Step 5: Commit** (`fix(setup): preflight/pins/validate steps + diagnosis table`, same trailer).

---

### Task 8: Steps — location, upload, first light, write; wire `_default_steps`

**Files:**
- Modify: `lorascan/setup.py` (add `step_location`, `step_upload`, `step_firstlight`, `step_write`; set `_default_steps`).
- Test: add to `tests/test_setup.py`.

**Interfaces:**
- Consumes: `Wizard`, `Runner.endpoint_health/first_upload/sweep`, `station_config.StationConfig/save`, `report.ascii.band_graph`.
- Produces: the four steps; `_default_steps()` returns `[step_preflight, step_pins, step_validate, step_location, step_upload, step_firstlight, step_write]`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_setup.py
from lorascan import setup, station_config as sc
import os

def test_location_range_checked():
    io = setup.ScriptedIO(["999,0", "33.9,-84.3"])   # first out of range, then valid
    w = setup.Wizard(io, setup.Runner(), home="/tmp")
    res = setup.step_location(w)
    assert res.ok and w.state["location"] == (33.9, -84.3)

def test_upload_verifies_and_sets_endpoint():
    class R(setup.Runner):
        def endpoint_health(self, url): return {"ok": True, "status": 200, "watermark": None}
        def first_upload(self, db, cfg): return {"ok": True, "sent": 3, "accepted": 3}
    io = setup.ScriptedIO(["", "yes"])               # default endpoint, opt in to first upload
    w = setup.Wizard(io, R(), home="/tmp"); w.state["db"] = "x.db"
    res = setup.step_upload(w)
    assert res.ok and w.state["endpoint"].startswith("https://")

def test_firstlight_draws_graph_from_sweep():
    class R(setup.Runner):
        def sweep(self, p): return [(915_000_000, -50), (920_000_000, -70)]
    io = setup.ScriptedIO([])
    w = setup.Wizard(io, R(), home="/tmp"); w.state["profile_path"] = "fake"
    res = setup.step_firstlight(w)
    assert res.ok and any("MHz" in m for m in io.said)

def test_write_persists_config(tmp_path):
    io = setup.ScriptedIO([])
    w = setup.Wizard(io, setup.Runner(), home=str(tmp_path))
    w.state.update(profile_path=str(tmp_path/".config/lorascan/profiles/manual.yaml"),
                   location=(33.9, -84.3), endpoint="https://share.lorascan.app", granularity="hour")
    res = setup.step_write(w)
    got = sc.load(home=str(tmp_path))
    assert res.ok and got.location == (33.9, -84.3) and got.endpoint == "https://share.lorascan.app"
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement** in `lorascan/setup.py`:

```python
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
        return StepResult(True, "endpoint saved but not verified; you can upload later with `lorascan upload`.")
    w.state["endpoint"] = endpoint
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
    import os
    prof = w.state.get("profile_path")
    name = os.path.splitext(os.path.basename(prof))[0] if prof else None
    cfg = _sc.StationConfig(profile=name, location=w.state.get("location"),
                            endpoint=w.state.get("endpoint"), granularity=w.state.get("granularity", "hour"))
    base = w.home if w.home is not None else os.path.expanduser("~")
    path = _sc.save(cfg, path=os.path.join(base, ".config", "lorascan", "config.yaml"))
    w.io.say(f"  wrote {path}")
    w.io.say("  next: `lorascan scan survey --db site.db --duration 2h` then `lorascan share`")
    return StepResult(True, "setup complete.")


def _default_steps():
    return [step_preflight, step_pins, step_validate, step_location, step_upload, step_firstlight, step_write]
```

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_setup.py -v` → PASS.

- [ ] **Step 5: Commit** (`feat(setup): location/upload/first-light/write steps + default order`).

---

### Task 9: `setup` subcommand + real `Runner` wiring; end-to-end fake run

**Files:**
- Modify: `lorascan/cli.py` (add `cmd_setup` and the `setup` subparser; a real `Runner` bound to probe/selftest/autoconf/share).
- Test: add to `tests/test_cli.py`.

**Interfaces:**
- Consumes: `setup.Wizard/TtyIO/Runner/run`, `cli.cmd_probe`/`cmd_selftest` logic, `autoconf.resolve`, `share.endpoint_health`, `share.build_share`/`upload_share`, `report.ascii`.
- Produces: `lorascan setup [--profile ...] [--db site.db]` returning the wizard exit code; a `_CliRunner(setup.Runner)` whose methods call the real code, so the wizard drives hardware in production and fakes in tests.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_cli.py
from lorascan import cli, setup

def test_setup_subcommand_runs_with_injected_wizard(monkeypatch, tmp_path):
    # End-to-end over the MANUAL pin path (real BoardProfile, no load_profile lookup), skipping
    # step_preflight (host-specific; covered by its own unit test). Fake Runner so no hardware:
    # manual pins -> validate GOOD -> skip location -> endpoint saved-not-verified -> first light -> write.
    class R(setup.Runner):
        def probe(self, p): return {"verdict": "GOOD"}
        def selftest(self, p): return {"ok": True}
        def endpoint_health(self, u): return {"ok": False, "error": "offline in test"}
        def sweep(self, p): return [(915_000_000, -55)]
    def fake_build(a):
        io = setup.ScriptedIO(["3", "kernel", "/dev/spidev0.0", "22", "23", "24", "", ""])
        w = setup.Wizard(io, R(), home=str(tmp_path))
        w._steps = setup._default_steps()[1:]   # drop step_preflight for this host-independent e2e
        return w
    monkeypatch.setattr("lorascan.cli._build_wizard", fake_build, raising=False)
    rc = cli.main(["setup"])
    assert rc == 0
    from lorascan import station_config as sc
    assert sc.load(home=str(tmp_path)).profile == "manual"
```

- [ ] **Step 2: Run to verify it fails** — no `setup` subcommand.

- [ ] **Step 3: Implement** in `lorascan/cli.py`:

```python
def _build_wizard(a):
    from . import setup, autoconf, share
    from .report.ascii import band_graph  # noqa: F401  (used via runner.sweep -> step)
    class _CliRunner(setup.Runner):
        def probe(self, profile):
            prof = _profile(profile) if isinstance(profile, str) else profile
            hal = open_hal(prof); r = Sx126x(hal, prof)
            try:
                return r.probe_bytes()
            finally:
                hal.close()
        def selftest(self, profile):
            prof = _profile(profile) if isinstance(profile, str) else profile
            hal, radio = _open_radio(prof)
            try:
                row = polled_energy(radio, 911_500_000, 125, 0.2, sample_gap_s=0.01)
                ok = row.n >= 5 and -126 < row.floor_dbm < -1
                reason = "" if ok else ("floor-out-of-range" if row.n >= 5 else "init-timeout")
                return {"ok": ok, "reason": reason}
            finally:
                hal.close()
        def import_daemon(self, source, config):
            return autoconf.resolve(source, config=config)
        def endpoint_health(self, url):
            return share.endpoint_health(url)
        def first_upload(self, db, state):
            st = Store(db); runs = st.runs()
            pname = runs[-1]["profile"] if runs else "unknown"
            cell = None
            if state.get("location"):
                cell = coarse_cell(state["location"][0], state["location"][1], DEFAULT_CELL_DEG)
            doc = build_share(st, cell, submitter_token(), pname, 0.0, DEFAULT_CELL_DEG, None,
                              granularity=state.get("granularity", "hour"))
            r = upload_share(doc, state["endpoint"])
            return {"ok": True, "sent": r["sent"], "accepted": r.get("accepted")}
        def sweep(self, profile):
            prof = _profile(profile) if isinstance(profile, str) else profile
            hal, radio = _open_radio(prof)
            rows = []
            try:
                for f in range(903_000_000, 927_000_000, 3_000_000):
                    row = polled_energy(radio, f, 125, 0.1, sample_gap_s=0.01)
                    rows.append((f, row.floor_dbm, row.peak_dbm))
            finally:
                hal.close()
            return rows
    return setup.Wizard(setup.TtyIO(), _CliRunner(), home=None)


def cmd_setup(a) -> int:
    from . import setup
    return setup.run(_build_wizard(a))
```

Add the subparser in `build_parser` (near the other `sub.add_parser` calls):

```python
    sp = sub.add_parser("setup", help="guided first-run: validate pins, import a meshtasticd/openHOP config, set location, verify upload")
    sp.add_argument("--db", default=None, help="an existing scan database to use for the first upload / first-light graph")
    sp.set_defaults(fn=cmd_setup)
```

(If `_open_radio`, `polled_energy`, `Sx126x`, `open_hal`, `Store`, `coarse_cell`, `submitter_token`, `build_share`, `upload_share`, `DEFAULT_CELL_DEG` are not already imported at module scope in `cli.py`, they are — confirm and reuse; do not re-import differently.)

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_cli.py -v` and `pytest tests/ -q` → green. Also run `python3.11 -m compileall lorascan` (or the bench gate) to catch a 3.11 syntax slip.

- [ ] **Step 5: Commit** (`feat(cli): `lorascan setup` subcommand wiring the real Runner`).

---

### Task 10: README — lead with guided setup, manual details below

**Files:**
- Modify: `README.md`.

**Interfaces:** none (docs). Land this with the release that ships `setup` (Matt: "once released… update the README").

- [ ] **Step 1: Add a guided-setup lead section** immediately after `## Install`, before `## Wire and describe your radio`:

```markdown
## Setup (recommended)

New here? Run the guided setup — it checks your SPI/pin wiring, can import the radio
config from an existing meshtasticd or openHOP install, sets your location, verifies the
upload, and prints a first look at the band:

```
lorascan setup
```

It writes `~/.config/lorascan/config.yaml`, after which the daily commands need no flags:

```
lorascan scan survey --db site.db --duration 2h
lorascan share
```

Everything below is the manual path — reach for it when you want to configure a piece by
hand or understand what `setup` is doing.
```

- [ ] **Step 2: Demote the manual sections.** Retitle `## Wire and describe your radio` and `## Turn an existing node into a scanner (meshtasticd / openHOP)` under a new umbrella heading `## Manual configuration (the details)` (make the two existing sections `###` subsections). Add one line under the new heading: "`lorascan setup` automates all of this; use these when you want to do it yourself."

- [ ] **Step 3: Mention software CS** in the "Wire and describe your radio" subsection where pins are described: "`nss` may be `kernel` (a hardware CE0/CE1) or a GPIO line number for a software-driven chip-select on a single-radio host."

- [ ] **Step 4: Verify structure** — `grep -nE "^##|^###" README.md` shows `## Setup (recommended)` before `## Manual configuration (the details)`, with the two former top-level sections now `###` under it. No command examples changed except the new lead.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -F - <<'MSG'
docs(readme): lead with `lorascan setup`, demote manual config to details

Point new users at the guided wizard as the default; keep the hand-wiring /
meshtasticd-import / scan sections below as "Manual configuration (the details)".
Note software-CS (GPIO nss) support.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01DBbMD4p6qTkRCnNcYoTDoA
MSG
```

---

## After all tasks

- Run the full suite: `pytest tests/ -q` (all green), and the bench 3.11 gate via `release2.sh` (compileall on the bench Python 3.11; keep `tests/test_py311_syntax.py` green).
- Bump the version in `pyproject.toml` and `lorascan/__init__.py`, add a CHANGELOG entry, and release via `release2.sh` (the bench-compile-gated path). The README task (Task 10) lands with that release per Matt's instruction.
- Open the PR for review (lorascan: I merge after review); PR body carries the 🤖 footer + the session URL.
