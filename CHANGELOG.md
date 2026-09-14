# Changelog

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
