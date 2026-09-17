# lorascan

A LoRa-chipset band scanner for the 902–928 MHz band (US ISM / 33 cm). It uses a real SX1262 —
the same silicon a deployment will run on — to measure interference the way that deployment will
experience it, and turns days of measurements into a long-term spectrum heat map, a band summary,
and (later phases) LoRa-specific presence maps, network identification and candidate-channel tests.

Design: `docs/superpowers/specs/2026-09-14-lorascan-design.md`. This directory is
phase 1 (P1): energy layer, quick and survey scans, SQLite store, HTML report, spidev/gpiod radios.

## Install (Debian 12 / Raspberry Pi OS)

```
sudo apt install python3-spidev python3-libgpiod
pip install https://github.com/Loomwave/lorascan/releases/download/v0.1.19/lorascan-0.1.19-py3-none-any.whl
# or from a checkout of https://github.com/Loomwave/lorascan :  pip install .   (developer: pip install -e .)
```
Issues and results: https://github.com/Loomwave/lorascan/issues
No other Python dependencies. Reports are self-contained HTML with static SVG charts that work offline; when the
browser can reach cdnjs.cloudflare.com the same figures become interactive (plotly.js), and the page says which it is showing.
Optional extras: `pip install paho-mqtt` for `--mqtt`.

Debian packaging notes (for a future `apt install lorascan`): pure Python, `pyproject.toml` (setuptools), no compiled
parts, runtime deps `python3-spidev` + `python3-libgpiod` (Recommends), `python3-paho-mqtt` (Suggests); the console
script is `lorascan = lorascan.cli:main`; profiles live inside the package (`lorascan/profiles/*.yaml`) and are
overridable from `/etc/lorascan/profiles/` and `~/.config/lorascan/profiles/`. `pipx install <wheel>` is the
clean per-user install on a Pi whose system Python is externally managed (PEP 668): `sudo apt install pipx`,
then `pipx install --system-site-packages https://github.com/Loomwave/lorascan/releases/download/v0.1.19/lorascan-0.1.19-py3-none-any.whl`
(`--system-site-packages` so the apt-installed spidev/gpiod modules are visible).

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

## Manual configuration (the details)

`lorascan setup` automates all of this; use these when you want to do it yourself.

### Wire and describe your radio

Copy `lorascan/profiles/generic-spidev.yaml` (`python3 -c "import lorascan.profile as p; print(p.PROFILE_DIRS[0])"` prints where the shipped profiles live after `pip install`), set the GPIO line numbers your module uses (BUSY, DIO1, RESET,
optional RXEN/TXEN) and the SPI device. Keys and meaning: see spec §3.1. Pin numbers are gpiochip0 line
offsets (BCM numbers on a Pi). `nss` may be `kernel` (a hardware CE0/CE1) or a GPIO line number for a
software-driven chip-select on a single-radio host.

```
lorascan probe    --profile my-board.yaml     # SPI first light: expects sync word 0x14 0x24 -> GOOD
lorascan selftest --profile my-board.yaml     # init, device errors, two 2-second energy reads
```

#### CH341 USB-SPI sticks (MeshToad V3, PineDio-USB class) — experimental

`pip install pyusb`, then `--profile meshtoad-v3-ch341` (profile `bus: {type: ch341, dev: auto}`; `dev`
may name the stick's USB serial). The backend is a port of the Loomwave Rust CH341 driver (framing,
pin map and the SCK/MOSI-must-be-outputs fix included) but has not yet been run against a stick from
this tool; use `probe` first and report what you see. Add a udev rule for 1a86:5512 or run as root.

### Turn an existing node into a scanner (meshtasticd / openHOP)

```
sudo lorascan auto --from meshtasticd --dry-run -- survey --db site.db --duration 2h --cad-grid 500000
sudo lorascan auto --from meshtasticd [--config /etc/meshtasticd/config.d/board.yaml] -- survey --db site.db --duration 2h
sudo lorascan auto --from openhop --to https://share.lorascan.app -- survey --db site.db --duration 12h
```
`auto` reads the radio the daemon already uses — meshtasticd's `config.yaml` + the active board in `config.d/`
(spidev → IRQ/Busy/Reset pins, DIO2/DIO3; `ch341` → the known-good MeshToad pin block + VID/PID) or openHOP's
`ch341:` block, or `radio_type: sx1262` + `sx1262:` for SPI radios — writes it as a profile under `~/.config/lorascan/profiles/`, stops **only that unit** (resolved from
`--config` via its ExecStart on multi-instance hosts), verifies nothing else holds the device (`fuser`) and aborts
loudly if something does, runs the plan after `--`, then starts the unit again in a `finally` and reports whether it
came back. `--dry-run` prints all of that and touches nothing. With `--to`, the share is uploaded afterwards with the
location from the daemon's config (openHOP `gps.location`) or `--cell`; the tool never geolocates by IP. The
daemon's TX power is never copied: lorascan only receives.

## Scan

```
lorascan scan quick  --profile my-board.yaml --db lorascan.db            # ~3 min: whole band x2, known channels 3 s each
lorascan scan survey --profile my-board.yaml --db lorascan.db --duration 3d   # continuous, adaptive revisit, Ctrl-C safe
lorascan report --db lorascan.db --out report.html                       # heat map, band summary, when-matrix, quietest table
lorascan export --db lorascan.db --csv rows.csv
```
Options: `--start/--stop/--step` (Hz; default 902.0–928.0 MHz every 200 kHz), `--bw` measurement
bandwidth kHz (125 default), `--dwell` seconds per visit, `--busy-t` dB above the floor that counts
as busy (8), `--revisit` maximum seconds between visits of any channel in survey mode (600).

## LoRa layers: CAD, decode, watch, candidates

```
lorascan scan quick  --profile P --db lorascan.db --cad                 # + CAD sweeps (SF7/9/11 x BW125/250) on hot and known channels
lorascan scan watch  --profile P --db lorascan.db --freqs 906.875,910.525,911.5 --sfs 7,9,11 --bws 125,250 --dwell 2 --decode-dwell 10
lorascan test        --profile P --db lorascan.db --candidates 905.0/9/125,921.0/11/250/8 --dwell 60
```
Channel Activity Detection is the LoRa-specific detector: it correlates against LoRa chirps at one
spreading factor and bandwidth, so a sweep over SFs maps which LoRa families occupy a channel (the
report's "LoRa presence by spreading factor"). The decode layer tunes to each known network's exact
PHY on that frequency (Meshtastic presets and frequency slots, LoRaWAN US915 channels, MeshCore,
Loomwave; `lorascan/networks.py`) and counts CRC-valid frames with their RSSI and SNR — payload bytes
are drained and discarded, never stored. `test` ranks candidates by busy fraction + CAD hit rate +
decoded frames per minute, all measured passively at exactly the candidate's settings.

## Running a long survey on a headless Pi

```
sudo systemd-run --unit lorascan-survey -p WorkingDirectory=$PWD \
  python3 -m lorascan scan survey --profile my-board.yaml --engine scan --db survey.db --duration 3d --cad
lorascan status --db survey.db                 # runs, row counts, age of the last row
lorascan serve  --db survey.db --port 8080     # live report at http://<pi>:8080/ (re-rendered every 60 s)
lorascan share  --db survey.db --cell 34.12,-84.38 --dry-run   # the opt-in community share file, to read before any upload
```

### Sharing on a thin or absent uplink

The share file is aggregates only and is sent gzipped: 130 channels at hour granularity is about 53 KB per day,
`--granularity day` (per-channel day rows plus a 7×24 hour-of-day × weekday matrix) about 3 KB per day. Uploads are
incremental and idempotent: `share --to URL` first asks the endpoint which bucket it already holds for your submitter
token and sends only that bucket and later ones, so a dropped link costs one small retry, never a re-send of the
run. `--budget 20k/day` picks the coarsest document that fits (hour → day → day without the CAD/decode tables).
A site with no uplink at all writes the file and uploads it later from any machine: `lorascan upload FILE --to URL`
(the submitter token is inside the file). The community endpoint (share.lorascan.app) is being deployed; until it is
live, `share` without `--to` just writes the file. Protocol: docs/superpowers/specs/2026-09-14-lorascan-share-endpoint.md
Add `--mqtt mqtt://[user:pass@]broker[:1883][/prefix]` to any scan to publish every row to a broker as it is measured
(needs `paho-mqtt`): `<prefix>/energy/<MHz>` per visit (floor/P50/P90/peak dBm, busy fraction, engine, n; no histogram),
`<prefix>/cad/<MHz>/sf<SF>` per CAD sweep, `<prefix>/decode/<MHz>/<network>` per decode dwell (counts only),
`<prefix>/status` retained. Prefix defaults to `lorascan/<hostname>`. A broker that is down at start fails the command
before the radio is opened; a broker that drops mid-run is counted (`mqtt_errors` event) and never stops the scan.
`--cad` adds Channel Activity Detection sweeps on the busy and known channels once per grid round, plus a
200-CAD reference sweep on the quietest channel so the report can state the false-alarm rate. Only one
lorascan may hold a radio at a time (a lock on the SPI device); a second one exits with a message.

## Export

```
lorascan export --db survey.db --csv survey.csv            # survey.csv (energy), survey-cad.csv, survey-decode.csv
lorascan export --db survey.db --csv cad.csv --table cad   # exactly one table to the named file
lorascan export --db survey.db --csv slots.csv --table slots --slot 500000   # the N kHz window view, best first
```
CAD rows carry `hit_rate` (hits / n_cad); decode rows are counts and medians only, never payloads.
(0.1.0–0.1.2 exported only the energy table: Loomwave/lorascan#1.)

## Picking a LoRa-clean 500 kHz slot (multi-bandwidth, dense CAD, your own networks)

```
lorascan scan survey --profile my-board.yaml --db slot.db --bw 62,125,250,500 --cad-grid 500000 --sfs 7,9,11 --bws 125,250,500 --duration 12h
lorascan report --db slot.db --out slot.html --slot 500000
```
`--bw` takes a list: every width is measured back to back on each channel, so one database holds the energy
floor at 62/125/250/500 kHz. `--cad-grid 500000` runs a Channel Activity Detection sweep over the centre of
every 500 kHz window once per grid round at each `--sfs` × `--bws` pair, which surfaces every LoRa family
in the band whatever its sync word. `--slot 500000` then ranks the windows.

The report now **automatically** prints and shows a "Recommended 500 kHz slot" — the best-placed 500 kHz window computed from the true 500 kHz-bandwidth energy, with CAD/decode presence and exclusion zones accounted for — whenever the database has 500 kHz-bandwidth data. `--recommend-bw <hz>` selects the width (default 500000, `0` disables).

For a deployment that must **coordinate on a shared channel** (e.g. MeshCore 500), add `--recommend-grid`: the pick is then aligned to the fixed `.250/.750` grid of 52 non-overlapping 500 kHz channels (902.25, 902.75, … 927.75 MHz) instead of a free window, so neighbours land on the same channel. Channels overlapping an exclusion zone (including the 902.25/902.75 band-edge pair) are struck, so the recommendation is the cleanest *usable* grid channel.

```
lorascan report --db slot.db --out slot.html   # prints: [report] best 500 kHz slot: 903.30 MHz (903.05-903.55) ...
```

Your own networks: put one preset per line in `~/.config/lorascan/networks.yaml` (or pass `--networks FILE`):
```
# network/preset: {sync, sf, bw (kHz), cr, preamble, crc, iq, freqs (MHz, space separated)}
fort2/main: {sync: 0x3C, sf: 8, bw: 250, cr: 6, preamble: 12, freqs: 905.0 906.5}
meshcore/us-narrow: {sync: 0x12, sf: 7, bw: 62, preamble: 32, freqs: 910.525 912.0}   # overrides the built-in preset
```
User networks are appended to the built-in table; a user preset with a built-in network/preset name replaces it.
`scan watch` and `test` decode against the merged table.

### Naming an undocumented LoRa network (sync-word finder)

```
lorascan syncfind --profile my-board.yaml --db find.db --freq 915.0 --sf 9 --bw 125 --cr 5 --sync-dwell 1
lorascan syncfind ... --syncs 0x00-0x7F          # half the space; 0x12,0x34,0x2B = a short list
```
CAD says "LoRa here" but decode names nothing when the sync word is not in the table. `syncfind` runs one
decode dwell per 8-bit sync word at the PHY you name (256 × `--sync-dwell`, so 4 min at 1 s) and prints the
sync words that yield CRC-valid frames, with counts, RSSI and SNR, plus a ready-made `networks.yaml` line.
Header/CRC errors at one sync with no CRC-valid frames usually mean the right sync with the wrong CR or SF.
Point it at a CAD-hot (frequency, SF, BW); rows land in the decode table as `sync-0xNN`.

**CAD cross-SF desense (field note, #5):** a strong nearby transmitter (a 1 W MeshCore SF11/250 node in the
same window) lights CAD at neighbouring SF/BW pairs too — in one tower dataset SF9/125 never fired without
SF11/250 in the same 3 s window. Before hunting a sync word, check the SF map for a much stronger cell at
another SF in the same window and the band summary for a peak near the top of the scale.

## Running your own share endpoint

The community endpoint is a small stdlib server in this package: `lorascan-share-server --db /data/share.sqlite --port 8081`
(routes `/` = the community map page, `/v1/map.json`, `/healthz`, `/v1/watermark`, `/v1/share`, `/v1/stats`; idempotent upserts; 4 MB gzip / 64 MB inflated / 60 POST per
hour per submitter; miscalibrated documents stored flagged). `deploy/share-server/` has a Dockerfile, a Kubernetes manifest,
a systemd unit and the handover README; the protocol is documented in
`docs/superpowers/specs/2026-09-14-lorascan-share-endpoint.md`. A regional group can run one for itself; the public one is
share.lorascan.app.

## Exclusion zones (what is measured but never recommended)

Band edges and the 33 cm amateur repeater segments are not options for a new deployment, so by default
`902.000–903.250` and `926.750–928.000` MHz are **excluded from recommendations**: the quietest-channels table,
the slot ranking and the candidate card list viable channels first and strike the excluded ones through, and the
heat map and band summary hatch the zones (in the HTML report and on the community map page). Every row is still
collected, stored, exported and shared. Change the zones with `--exclude 902.0-903.25,926.75-928.0` (MHz pairs, on
`report` and `export`) or switch them off with `--no-exclude`.

## Slot view, standalone SVGs, rendering from a share file

```
lorascan report --db survey.db --out r.html --slot 500000       # adds a '500 kHz slots, best first' table (worst case per window)
lorascan report --db survey.db --out r.html --svg figs/          # also writes figs/heatmap.svg band.svg sfmap.svg when.svg
lorascan report --from-share site.json --out site.html --svg figs/   # no database needed: render what a site shared
```
The slot table scores each window by its busiest channel's busy fraction + its highest CAD hit rate + decoded
frames / 10 + (worst floor − best floor in the band) / 10 dB (lower is better): a steady carrier 10 dB above the
band's best floor costs as much as 100 % busy, so it cannot rank clean. That is the question "which 500 kHz
window is least hit" in one table; `floor_worst`, `floor_best`, `peak_max` are there to cross-check.
`--from-share` renders the heat map at the share's granularity (hour or day), so a central host can draw a
site's figures from the 3–53 KB/day it uploads instead of its database.

## Flaky buses (USB sticks on towers)

A transient bus error (pyusb `USBTimeoutError` / `USBError`, a spidev EIO) no longer ends a run: the scan
closes the HAL, waits 1/2/4/8/16 s, resets the USB device on the second try, reopens and re-initialises the
radio (re-uploading the scan patch if in use) and measures the interrupted visit again. After 5 consecutive
failures it records `hal_giveup` and stops cleanly. `lorascan status` shows `hal_error` / `hal_recovered` /
`hal_reopen_failed` counts per run.

## Calibration

`lorascan calibrate --profile my-board.yaml --level -60 --freq 915.0` reads a known input level (a signal
generator, or a reference transmitter whose level at your antenna port you have measured) and writes
`rssi_offset_db` into `my-board-calibrated.yaml`. Reports and share files then print absolute dBm;
without it they say "relative (uncalibrated)" (spec §8).

## What the numbers mean

Per channel visit the tool stores every RSSI sample's 33-level histogram (4 dB levels, the Semtech
scan-patch layout) and the derived floor (P10), median, P90, peak and busy fraction. Levels are
**relative** unless the profile carries a measured `rssi_offset_db`; comparisons between channels and
over time are the product, absolute dBm is not (spec §8). Unsettled reads (≥ −1 dBm, ≤ −126 dBm)
are discarded and counted.

## Receive-only

P1 never transmits: the driver has no transmit call and the PA is parked at −9 dBm. Later phases add
an opt-in two-radio link test behind `--tx-ok` with power and duty-cycle caps.

## Engines

`--engine poll` (default) polls `GetRssiInst` from the host: ~1.4 kHz over spidev, far less over USB
bridges. `--engine scan` uploads Semtech's spectral-scan RAM patch (as redistributed by RadioLib) and
lets the chip build a 33-level histogram itself at ~120 k samples/s; one 0.4 s visit yields ~48 000
samples. The scan engine runs the GFSK receiver at the nearest bandwidth at or above `--bw` (datasheet
Table 13-45). Two things learned on hardware and encoded in the code: the modem must be in GFSK mode
with Semtech's `util_spectral_scan` parameters, and the scan status register keeps the previous
COMPLETED flag until the new scan is running, so the tool waits the nominal scan time before polling
and rejects any histogram whose sample count is not the requested one. If the engine fails twice the
scan falls back to polling and says so.

## FAQ

**Can it listen with LoRa instead of GFSK?** It does by default. `--engine poll` initialises the SX1262 in LoRa
mode and reads RSSI in LoRa RX; only `--engine scan` switches to GFSK, because Semtech's spectral-scan patch
only runs under the GFSK receiver (the histogram is wideband energy through the RX filter, so the dBm mean the
same thing either way). Energy in any modem cannot tell LoRa from noise: to *detect LoRa* add `--cad` (Channel
Activity Detection with the real LoRa demodulator per SF/BW) or use `scan watch` / `test`, which also passively
decode against the known-network presets. The report's "LoRa presence by spreading factor" panel comes from those.

## Validated on

- 2026-09-14, Loomwave bench Raspberry Pi 5 (Debian 12, Python 3.11, python3-spidev 3.5,
  python3-libgpiod 1.6) with a Nebra Duo HAT (E22P-915M30S SX1262 on spidev0.0, BUSY 23, DIO1 24,
  RESET 22): `probe` GOOD (0x14 0x24), `selftest` PASS, quick scan 274 rows in 2.6 min (polled),
  scan engine complete histograms of 48 780 samples per 0.4 s visit. Against `infrad bandscan 4` on
  the same HAT the same hour, floors agreed to +0.5 dB mean (−7…+8 dB spread over 14 channels); the
  reference disagreed with itself by up to 9 dB between two back-to-back runs on that bench, so a
  quiet-site or long-dwell comparison is still owed before absolute agreement is claimed.
- Same bench, 13:22–13:25Z: `scan watch` on 906.875 / 910.525 / 911.5 / 913.125 MHz — CAD hits at
  SF9 and SF11 on the busy channels (e.g. 910.525 SF11/BW250 11 of 50, 906.875 SF9/BW125 8 of 50,
  SF7 0 of 50 everywhere), one CRC-valid MeshCore us-narrow frame decoded on 910.525 (−33 dBm,
  SNR 12), seven Loomwave fleet frames on 911.5 (SNR 11).

- 2026-09-14, reported by @wehooper4 (issue #1): v0.1.0 on Debian 13 (Trixie), Python 3.13, a MeshToad CH341
  USB-SPI stick with profile `meshtoad-v3-ch341` — `probe`, `selftest` and `scan` all GOOD, and a `watch` plan
  produced 14 energy / 42 CAD / 15 decode rows. First real-hardware validation of the CH341 HAL.

## Licence

Apache-2.0 (the Loomwave repository licence). The Semtech SX126x scan patch (P1 task 10) is
redistributed under Semtech's BSD-3 notice as shipped in RadioLib.
