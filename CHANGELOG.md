# Changelog

## 0.1.5 — 2026-09-14
- Static SVG heat map caps itself at 240 columns by merging adjacent time buckets (mean of measured cells; the axis label says how many were merged), so a multi-day survey report or `serve` page stays a few MB instead of growing without bound (a 19 h run at 60 s buckets was heading for ~10 MB).

## 0.1.4 — 2026-09-14
- Community sharing for thin uplinks (share format `lorascan-share/2`): `share --granularity hour|day` (day adds the 7×24 when-matrix; ≈ 3 KB gzipped per day), `--budget 20k/day` picks the coarsest document that fits, `--to URL` uploads gzip JSON incrementally against the endpoint's watermark (idempotent rows, 5xx/network retries), and `lorascan upload FILE --to URL` sends a file written earlier from any machine. Endpoint protocol in docs/superpowers/specs/2026-09-14-lorascan-share-endpoint.md; share.lorascan.app deployment is a separate task.

## 0.1.3 — 2026-09-14
- `export --csv X.csv` now writes every table: energy to `X.csv`, CAD to `X-cad.csv` (with `hit_rate`), decodes to `X-decode.csv`; `--table energy|cad|decode` writes one table to the named file. 0.1.0–0.1.2 silently exported only energy (Loomwave/lorascan#1, reported by @wehooper4).
- README: export section, FAQ (LoRa vs GFSK listening), and the first CH341/MeshToad validation (Debian 13, Python 3.13) from that report.

## 0.1.2 — 2026-09-14
- `--mqtt mqtt://[user:pass@]host[:port][/prefix]` on every scan plan: publishes each energy / CAD / decode row as it is measured plus a retained status topic, for Grafana and Home Assistant users (optional `paho-mqtt`; a broker that drops mid-run never stops the scan).
- README: offline charts note, MQTT topics, Debian packaging notes (pipx on PEP 668 systems).

## 0.1.1 — 2026-09-14
- Reports and the `serve` live page now carry **static SVG charts** (occupancy heat map, band summary, SF map, hour×weekday matrix) rendered by Python with no JavaScript and no network. They are the charts you see on an offline Pi, in a mail client, or when the plotly CDN is blocked; the interactive plotly.js figures are layered on top when the CDN loads, and the page says in one line which of the two it is showing. Fixes "the charts on the status page appear empty" (the page only had plotly figures, which need cdnjs.cloudflare.com at view time).

## 0.1.0 — 2026-09-14
First release: SX1262 band scanner for 902–928 MHz, receive-only.
- Energy layer: host-polled RSSI and the on-chip Semtech spectral-scan histogram (`--engine scan`, ~48 000 samples per 0.4 s visit).
- LoRa layers: Channel Activity Detection sweeps per spreading factor / bandwidth (`--cad` with a false-alarm reference sweep on the quietest channel, `scan watch`) and passive decode counts against a known-network table (Meshtastic, MeshCore, LoRaWAN US915, Loomwave) — counts only, payloads are never stored.
- Plans: `scan quick` (whole band twice + known channels), `scan survey` (continuous, adaptive revisit), `scan watch` (fixed channel list, all layers), `test` (candidate frequency + settings → ranked report card).
- SQLite store, `report` (occupancy heat map with automatic time buckets, band summary, LoRa presence by SF, hour×weekday matrix, quietest channels, candidate card), `export`, `status`, `serve` (live page for a running survey), `calibrate` (known-level offset into a profile copy), and `share --dry-run` (the opt-in community share file: per-channel aggregates plus a coarse location cell; no upload yet).
- HAL: Linux spidev + libgpiod (v1/v2) / lgpio; CH341 USB-SPI sticks (MeshToad class, experimental, pyusb); board profiles; one-scan-per-radio device lock; fake radio for tests (79 tests, no hardware needed).
Validated on a Raspberry Pi 5 with a Nebra Duo HAT (probe, selftest, quick scan, scan engine, CAD hits and decoded MeshCore/Loomwave frames on live channels); see README "Validated on".
