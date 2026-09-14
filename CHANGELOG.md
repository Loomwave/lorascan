# Changelog

## 0.1.11 — 2026-09-14
- Reference share endpoint `lorascan-share-server` (stdlib + SQLite): `/healthz`, `/v1/watermark`, `/v1/share` (gzip JSON, idempotent upserts keyed per the spec, newest `generated` wins), `/v1/stats`; 4 MB gzip / 64 MB inflated / 60 POST per hour per submitter; miscalibrated documents stored `flagged = 1`. Tests drive it with the real `lorascan share --to` client. Deployment kit in `deploy/share-server/` (Dockerfile, k8s.yaml for share.lorascan.app, systemd unit, handover README).

## 0.1.10 — 2026-09-14
- `lorascan auto --from meshtasticd|openhop [--config] [--dry-run] [--to URL] -- <plan args>` (Loomwave/lorascan#7): resolves the profile from the daemon's own config (meshtasticd config.yaml + config.d board files, spidev or ch341; openHOP ch341 block), stops only the targeted unit, verifies the device is free, runs the plan, always restores and verifies the unit, then shares with the config's location if any. New dependency-free nested-YAML reader for those files.

## 0.1.9 — 2026-09-14
- Bus-level recovery (Loomwave/lorascan#6): the CH341 backend wraps pyusb errors into `HalError`; the scan loop closes and reopens the HAL with 1/2/4/8/16 s backoff (USB device reset from the second attempt), re-initialises the radio, re-uploads the scan patch, and measures the interrupted visit again; gives up cleanly after 5 consecutive failures (`hal_giveup`). Events `hal_error` / `hal_recovered` / `hal_reopen_failed` per run.

## 0.1.8 — 2026-09-14
- `lorascan syncfind --freq --sf --bw [--cr] [--syncs] [--sync-dwell]`: sweeps the 8-bit sync words at one PHY hypothesis and reports the ones that decode (counts, RSSI, SNR) with a ready-made networks.yaml line; rows land in the decode table as `sync-0xNN`. (#5)
- Slot score gains a worst-case-floor term, (floor_worst − best floor in the band) / 10 dB, so a steady carrier cannot rank as a clean window; `floor_penalty` column in the table and the CSV. (#4 field note)
- README: sync-word finder, CAD cross-SF desense caveat from the FORT2 data.

## 0.1.7 — 2026-09-14
- `--bw 62,125,250,500` on quick/survey: every width measured back to back per channel in one run (#2 item 1).
- `--cad-grid 500000` on quick/survey: a CAD sweep over the centre of every window once per grid round at each `--sfs` × `--bws` pair, i.e. dense whole-band LoRa detection independent of sync word (#2 item 2).
- User network table `~/.config/lorascan/networks.yaml` (or `--networks FILE`), one preset per line, merged over the built-in table; a user preset with a built-in name replaces it (#2 item 4).

## 0.1.6 — 2026-09-14
- LoRaWAN US915 decode covers all uplink data rates DR0–DR3 (SF10/9/8/7 @125 on the 64-channel raster) and DR4 (SF8 @500), plus downlinks DR8–DR13 (SF12…SF7 @500, inverted IQ, no PHY CRC: counted on RxDone with a valid header). Presets gain `invert_iq`. (Loomwave/lorascan#4, wehooper4)
- `report --slot 500000` / `export --table slots --slot 500000`: N kHz window view over existing rows — worst floor, peak, busiest channel, highest CAD hit rate and SF, decoded networks per window, best first. (#2 item 3)
- `report --from-share X.json` renders the report from a share document alone (no database), and `report --svg DIR` writes standalone heatmap/band/sfmap/when .svg files. (#3)

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
