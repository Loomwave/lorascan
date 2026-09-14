# lorascan

A LoRa-chipset band scanner for the 902–928 MHz band (US ISM / 33 cm). It uses a real SX1262 —
the same silicon a deployment will run on — to measure interference the way that deployment will
experience it, and turns days of measurements into a long-term spectrum heat map, a band summary,
and (later phases) LoRa-specific presence maps, network identification and candidate-channel tests.

Design: `docs/superpowers/specs/2026-09-14-lorascan-design.md` (the Loomwave repo). This directory is
phase 1 (P1): energy layer, quick and survey scans, SQLite store, HTML report, spidev/gpiod radios.

## Install (Debian 12 / Raspberry Pi OS)

```
sudo apt install python3-spidev python3-libgpiod
pip install .            # or: pip install -e .   (developer)
```
No other Python dependencies. Reports load plotly.js from cdnjs when opened in a browser.

## Wire and describe your radio

Copy `profiles/generic-spidev.yaml`, set the GPIO line numbers your module uses (BUSY, DIO1, RESET,
optional RXEN/TXEN) and the SPI device. Keys and meaning: see spec §3.1. Pin numbers are gpiochip0 line
offsets (BCM numbers on a Pi). Chip-select stays with the kernel (`nss: kernel`, use a CE pin).

```
lorascan probe    --profile my-board.yaml     # SPI first light: expects sync word 0x14 0x24 -> GOOD
lorascan selftest --profile my-board.yaml     # init, device errors, two 2-second energy reads
```

### CH341 USB-SPI sticks (MeshToad V3, PineDio-USB class) — experimental

`pip install pyusb`, then `--profile meshtoad-v3-ch341` (profile `bus: {type: ch341, dev: auto}`; `dev`
may name the stick's USB serial). The backend is a port of the Loomwave Rust CH341 driver (framing,
pin map and the SCK/MOSI-must-be-outputs fix included) but has not yet been run against a stick from
this tool; use `probe` first and report what you see. Add a udev rule for 1a86:5512 or run as root.

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

## Licence

Apache-2.0 (the Loomwave repository licence). The Semtech SX126x scan patch (P1 task 10) is
redistributed under Semtech's BSD-3 notice as shipped in RadioLib.
