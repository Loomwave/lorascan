# lorascan Guided Setup — Design Spec

**Status:** approved design (Matt, 2026-09-16), pending spec review.
**Issue driver:** users report lorascan is hard to configure, leading to misconfigurations and data that never reaches the community share. Requested: a guided, step-by-step setup that validates pin settings, imports existing radio configs (meshtasticd / openHOP), and sets the station location. A specific defect was called out: lorascan cannot use a **software-driven CS pin on a single-radio host** — the SPI HAL raises `HalError("software chip-select … is not supported in P1")` whenever `pins.nss` is a GPIO line, which is exactly the wiring meshtasticd and many hand-wired HATs use. This spec fixes that, because a wizard that imports such a config and then dead-ends at radio open would not help the users who most need it.

**Goal:** a `lorascan setup` interactive CLI wizard that takes a non-expert from a bare install to a validated, uploading station, and a persistent station config so the daily commands work without remembered flags. It closes with an ASCII graph of the band the radio is hearing, so a first-run user gets immediate, tangible proof it works.

**Architecture:** two pieces. (1) A **software-CS fix** in the SPI HAL so a GPIO chip-select works on a single-radio host. (2) One new orchestration module (`lorascan/setup.py`) plus a `setup` subcommand, driving the existing building blocks — `autoconf` (import from meshtasticd/openHOP), `profile` (board profiles), `probe`/`selftest` (pin validation), `share` (build + upload) — and closing with an ASCII band graph. One new persistent file, the *station config* `~/.config/lorascan/config.yaml`. The HAL fix is the only change to radio-adjacent code; no change to the SX126x driver, the scan engine, or the share protocol.

**Build order:** the software-CS fix ships first (the wizard's validate step depends on it for GPIO-CS users), then the station config + command wiring, then the wizard steps, then the closing ASCII graph.

**Tech stack:** Python, standard library only (no new third-party deps); the existing `profile.parse_mini_yaml` / `yamlmini` mini-YAML reader for config I/O; `argparse` CLI; the `fake` HAL and injected seams for tests.

## Global Constraints

- **Target interpreter is Python 3.11** (Debian 12 / Raspberry Pi OS = every tester). The feature MUST `python3.11 -m compileall` clean on the bench before release; no PEP 701 f-string forms (backslash in a replacement field; a nested f-string reusing the enclosing quote). Dev boxes run 3.12 and will not catch these.
- **Receive-only.** lorascan never transmits. The wizard never keys the radio; `probe`/`selftest` are receive/register-only, unchanged.
- **Software CS is single-radio-host scope only.** The fix targets one SX1262 on one SPI host whose NSS is a GPIO line. Multi-radio bus arbitration / shared-bus CS sequencing is out of scope. The GPIO CS is active-low, idle high, asserted around each `xfer` transaction; the kernel CE is suppressed best-effort (`SPI_NO_CS`) and its absence is tolerated (the BCM SPI driver historically ignores `SPI_NO_CS`, which is harmless here because the module's real select is the GPIO).
- **Zero new dependencies.** Standard library plus the package's own modules only.
- **Config directory convention (existing):** `~/.config/lorascan/` already holds `token`, `profiles/`, and `networks.yaml`; `networks.yaml` also falls back to `/etc/lorascan/networks.yaml`. The station config follows the same two-path convention: `~/.config/lorascan/config.yaml` then `/etc/lorascan/config.yaml`.
- **Fail-closed at every step:** a check that does not pass stops forward progress and offers a fix. The wizard never advances past a silent failure into a state that collects or uploads nothing.
- **Non-invasive to the host:** detect-and-instruct only. The wizard never edits boot config, never runs `sudo`, never restarts a system service on its own (importing a daemon's *config file* is a read; the existing `auto` command's stop/restore of a daemon is out of this feature's scope).
- **Backward compatible:** with no `config.yaml` present, `scan` / `share` / `upload` behave exactly as today. Precedence is always explicit flag → `config.yaml` value → current built-in default.
- **Share protocol unchanged:** `lorascan-share/2`; endpoints `GET /healthz`, `GET /v1/watermark`, `POST /v1/share` as in `2026-09-14-lorascan-share-endpoint.md`.

## Station config schema

`~/.config/lorascan/config.yaml` (mini-YAML, flow maps as in the shipped profiles):

```yaml
# written by `lorascan setup`; hand-editable
profile: my-nebra              # a name under ~/.config/lorascan/profiles/ or a path
location: {lat: 33.8931, lon: -84.2534}   # omitted entirely if the user declines a location
share:   {endpoint: "https://share.lorascan.app", granularity: hour}
```

- Every key is optional; a missing key means "fall back to the built-in default".
- `location` absent ⇒ no location shared (same as today's "no `--cell`").
- Loader `station_config.load()` returns a small dataclass with `profile: str|None`, `location: tuple[float,float]|None`, `endpoint: str|None`, `granularity: str|None`; it reads `~/.config/lorascan/config.yaml` then `/etc/lorascan/config.yaml`, returns an all-None config if neither exists, and never raises on a missing file (a malformed file raises a clear error, since a half-written config that silently reverts to defaults is exactly the silent-misconfig this feature exists to kill).

## Components

### `lorascan/hal/spidev_gpiod.py` (modified) — software chip-select
- **Does:** support `pins.nss` as a GPIO line number (software CS) in addition to `"kernel"`/`None` (kernel CE), on a single-radio host.
- **Change:** in `__init__`, replace the `HalError("software chip-select … not supported")` raise with: request the `nss` GPIO as an output initialized HIGH (deasserted), and set `self.spi.no_cs = True` best-effort (wrapped so a driver that rejects it does not fail the open). In `xfer`, when a software CS is configured, drive the GPIO LOW before `spi.xfer2` and HIGH after (a single CS-framed transaction per call, matching how kernel CE frames each `xfer2`); with kernel CE, `xfer` is unchanged.
- **Interface:** unchanged public surface (`xfer`, `busy`, `dio1`, `set_reset`, `set_rxen`, `sleep`, `close`); a private `_assert_cs`/`_deassert_cs` pair, no-ops under kernel CE.
- **Depends on:** `spidev`, the GPIO backend already abstracted here (`_GpioV1`/`_GpioV2`/`_GpioLg`), which already does output request + set.
- **Note:** `probe`/`selftest`/`scan` need no change — they call `hal.xfer`; the framing is entirely inside the HAL. The `fake` HAL is unaffected (it has no CS).

### `lorascan/station_config.py` (new)
- **Does:** load/save the station config; resolve a value with the fixed precedence.
- **Interface:**
  - `@dataclass StationConfig(profile, location, endpoint, granularity)`.
  - `load(home=None) -> StationConfig` — reads the two-path convention; all-None if absent.
  - `save(cfg, path=None) -> str` — writes `~/.config/lorascan/config.yaml`, returns the path.
  - `resolve(flag_value, cfg_value, default)` — the precedence helper the commands call.
- **Depends on:** `profile.parse_mini_yaml`, `os`.

### `lorascan/setup.py` (new)
- **Does:** the wizard — a sequence of steps, each returning a `StepResult(ok, summary, detail)` and looping on its own fix until the user passes or quits. Pure orchestration: all console I/O and all hardware/network actions go through injected seams.
- **Interface (each step is a function taking the shared `Wizard` context):**
  - `Wizard(io, runner, home)` — `io` is an `IO` seam (`ask(prompt, default)`, `choose(prompt, options)`, `confirm(prompt)`, `say(msg)`); `runner` wraps the hardware/network calls (`probe(profile) -> ProbeResult`, `selftest(profile) -> SelftestResult`, `import_daemon(source, config) -> Resolved`, `endpoint_health(url) -> HealthResult`, `first_upload(db, cfg) -> UploadResult`) so tests inject fakes.
  - `step_preflight(w)`, `step_pins(w)`, `step_validate(w)`, `step_location(w)`, `step_upload(w)`, `step_firstlight(w)`, `step_write(w)` — return `StepResult`.
  - `run(w) -> int` — drives the steps in order, honoring the fail-closed loop and a clean quit; returns a process exit code.
- **Depends on:** `station_config`, `autoconf`, `profile`, `report.ascii`, and (through `runner`) `cli`'s probe/selftest and `share`.
- **Diagnosis table (step_validate):** a small, tested map from a probe/selftest symptom to a plain-language cause + which pin to re-check — e.g. `status==0xFF` → "SPI/CS wiring or the board has no power (if your NSS is a GPIO, confirm the pin number)"; selftest `BUSY` never clears → "the `busy` pin is wrong"; init timeout → "check `reset` and `dio1`"; energy floor out of range → "antenna/feedline or `rxen`". Because software CS is now supported, a GPIO `nss` is a normal case here, not a hard stop.
- **step_firstlight:** the immediate-gratification close-out. Prefer a saved scan DB if one exists (peak dBm per band slice from the run just uploaded); otherwise run a short receive-only energy sweep across 902–928 MHz (a fixed set of channel centers, brief dwell each) through the runner. Render the result with `report.ascii.band_graph` and print it. Receive-only, skippable, and never fatal — a failure here still lets `step_write` complete (the station is configured regardless).

### `lorascan/report/ascii.py` (new)
- **Does:** render band energy as a terminal ASCII bar graph — pure, deterministic, no I/O.
- **Interface:** `band_graph(rows, width=None, floor_dbm=-125, ceil_dbm=-20) -> str` where `rows` is a list of `(freq_hz, level_dbm)` (or `(freq_hz, floor_dbm, peak_dbm)`); returns a multi-line string of labeled horizontal bars scaled to `width` (auto from `shutil.get_terminal_size`, clamped), with the two exclusion zones (902.000–903.250 / 926.750–928.000 MHz) marked and a dBm axis legend.
- **Depends on:** `shutil` only. Reused by nothing else yet, but deliberately standalone so `report` could later offer an ASCII view.

### `lorascan/cli.py` (modified)
- Add the `setup` subparser → `cmd_setup`, which constructs a real `Wizard` (a TTY `IO` and a `runner` bound to the real `probe`/`selftest`/`autoconf`/`share`) and calls `run`.
- Change three commands to read the station config when a flag is absent, via `station_config.resolve`, leaving explicit flags and today's defaults intact:
  - `scan …`: `--profile` default → `cfg.profile` → today's behavior.
  - `share`: `--to` → `cfg.endpoint`; `--cell` → `cfg.location` (formatted `lat,lon`); `--granularity` → `cfg.granularity`.
  - `upload`: `--to` → `cfg.endpoint`.
- These are default-resolution changes only; no command's flags or output format change.

### `lorascan/share.py` (small addition)
- `endpoint_health(base, timeout_s=10) -> dict` — `GET {base}/healthz` (and `/v1/watermark` for a fuller check), returning reachability + any server version/watermark, so the wizard can prove the target before the user walks away. Reuses the existing request/retry style; adds no new dependency.

## Data flow

1. `lorascan setup` → `cmd_setup` builds a real `Wizard` → `run`.
2. **Preflight:** read `/dev/spidev*`, `/boot/firmware/config.txt` (or `/boot/config.txt`), attempt `import spidev`/`gpiod`, check group membership/permissions. Report each with its fix; the user confirms they have addressed blockers or quits.
3. **Pins:** the user chooses import / shipped-profile / manual. Import calls `autoconf.resolve(source, config)`; a shipped profile is `load_profile(name)`; manual builds a `BoardProfile`. The chosen profile is written under `~/.config/lorascan/profiles/`.
4. **Validate:** `runner.probe(profile)` then `runner.selftest(profile)`. On failure, print the diagnosis and return to step 3.
5. **Location:** default from `autoconf`'s daemon location if present, else prompt; range-check (`-90..90`, `-180..180`).
6. **Upload:** `runner.endpoint_health(endpoint)`; if a scan DB exists and the user opts in, `runner.first_upload(db, cfg)` = build_share + upload_share, and confirm accepted (HTTP 200, watermark advanced). No DB ⇒ verify reachability and print the post-first-survey command.
7. **First light:** draw the ASCII band graph from the scan DB if present, else a short receive-only sweep; print it. Non-fatal.
8. **Write:** `station_config.save(cfg)`; print the summary and the exact next command (or offer to launch a survey).

## Error handling

- Every hardware/network call is wrapped; a failure becomes a `StepResult(ok=False, …)` with the real error text, never a traceback.
- The wizard is fail-closed: a failing preflight/validate step will not advance; the user fixes and retries or quits with a non-zero exit code and a one-line "not configured" summary.
- Upload/endpoint failures in step 6 report the actual error and still leave a written share file (matching today's store-and-forward), so the user can `lorascan upload FILE` later.
- A malformed `config.yaml` raises a clear error at load; an absent one is silent (returns all-None).

## Testing (TDD)

- **Software CS (`spidev_gpiod`):** with a fake `spidev` + fake GPIO backend, assert that a GPIO `nss` is requested as output idle-high, that each `xfer` drives it low-then-high around exactly one `xfer2`, that `no_cs=True` is attempted and a driver that rejects it does not fail the open, and that a `"kernel"`/`None` nss leaves `xfer` and CS untouched. The old "not supported" `HalError` is gone (asserted absent).
- **ASCII graph (`report.ascii.band_graph`):** deterministic golden-string tests — bar scaling at the floor/ceil bounds, width clamping, exclusion-zone marks at the right frequencies, and the two row shapes (`(f, level)` and `(f, floor, peak)`).
- `station_config`: precedence, two-path fallback, absent = all-None, malformed = raises, round-trip save/load.
- `setup` steps: each step and its fix-loop with an injected `IO` (scripted answers) and a fake `runner` — preflight blockers, all three pin sources (including a meshtasticd import whose CS is a GPIO), a validate failure → fix → pass, location range-check, upload with/without a DB, first-light with a DB and via a sweep, and the clean-quit path. The diagnosis table is asserted symptom-by-symptom.
- End-to-end: the `fake` profile drives `run` through a scripted happy path and a repair path with no TTY and no hardware, ending in a printed ASCII graph.
- CLI default-resolution: `scan`/`share`/`upload` pick up `config.yaml` when a flag is absent, are overridden by an explicit flag, and are unchanged when no config exists.
- The whole package `python3.11 -m compileall` clean on the bench (gate in `release2.sh`), and the PEP-701 tokenizer guard in `tests/test_py311_syntax.py` stays green.

## Out of scope (YAGNI)

- Editing boot config or running `sudo`/`raspi-config` for the user (detect-and-instruct only).
- A web-based setup flow (may reuse this step logic in a later phase; not now).
- Any change to the share protocol, the scan engine, or the SX126x driver. (The HAL software-CS fix is the one radio-adjacent change and is in scope.)
- Multi-radio / shared-bus CS arbitration — software CS is fixed for a single-radio host only.
- Managing the daemon lifecycle (stop/restore) — that stays in the existing `auto` command; `setup` only *reads* a daemon config to import pins.
