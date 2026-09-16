# Changelog

## 0.1.27 — 2026-09-16
- The auto-ranged band-summary and 500 kHz-ribbon legends show the colour-scale gradient swatch again, laid out to the LEFT of the "busy X% → Y%" text so swatch and label no longer overlap. (0.1.26 dropped the swatch to fix the overlap; this restores it, positioned.) A test now guards the swatch against silent removal.
## 0.1.26 — 2026-09-16
- Fix: the auto-ranged band-summary legend on the community page had a colour swatch overlapping the word "auto-ranged". The legend is now text-only ("busy X% → Y% · colour auto-ranged"), matching the 500 kHz ribbon.
## 0.1.25 — 2026-09-16
- The auto-ranged band summary now carries the same "busy X% → Y%" legend (and a colour swatch) the 500 kHz ribbon prints, so on the community page a relatively-coloured band names the range it mapped to instead of leaving the reader to guess. `band_svg(auto_range=True)` only; the absolute-scale per-station report is unchanged.
## 0.1.24 — 2026-09-16
- Community share page: a "Best 500 kHz slot (coordination grid)" section. When any submitter has scanned at 500 kHz bandwidth, the page ranks and draws the fixed `.250/.750` grid of 52 channels as a static ribbon (recommended channel ringed, exclusion-zone channels struck, hover for busy/floor), driven by the same `recommend_grid_slots` logic; when no one has, it shows how to populate it. Channel colours — and the band-summary strip — now **auto-range** to the data's actual busy spread, so a quiet band no longer renders every bar the same pale colour (`busy_colour` gained optional `vmin`/`vmax`; the per-station report is unchanged). The bulk-export endpoints (`/v1/dump.json`, `/v1/dump.csv`, from 0.1.22) are now documented in the share-server deploy README.
## 0.1.23 — 2026-09-16
- Grid-aligned 500 kHz recommendation: `lorascan report --recommend-grid` ranks the fixed `.250/.750` grid of 52 non-overlapping 500 kHz channels (902.25, 902.75, … 927.75 MHz) — the coordination grid MeshCore-500 deployments share so neighbours interoperate — instead of a free window. Each grid channel is scored worst-case from the true 500 kHz-bandwidth energy that falls in it (reusing the v0.1.21 scoring), with exclusion-zone channels struck. Without the flag the free-grid recommendation is unchanged. New `report/slot_recommend.recommend_grid_slots` / `grid_centers`.
## 0.1.22 — 2026-09-16
- Community share server: bulk data-dump endpoints. `GET /v1/dump.json` returns every unflagged per-(submitter, freq, bandwidth) aggregate row — including `bw_hz`, which the map summary blends away — plus submitter metadata; `GET /v1/dump.csv` is the same rows as CSV. Lets the community and researchers pull the raw fleet aggregates for their own analysis (e.g. bandwidth-specific / 500 kHz slot studies). Flagged (miscalibrated) uploads are excluded, matching the public map.

## 0.1.21 — 2026-09-16
- Best 500 kHz slot recommendation: `lorascan report` now automatically prints and shows a "Recommended 500 kHz slot" — the best-placed 500 kHz window computed from the true 500 kHz-bandwidth energy (not the blended per-channel summary), accounting for CAD/decode presence, a narrowband-carrier floor penalty, and the exclusion zones — whenever the database holds 500 kHz-bandwidth data (`scan … --bw …,500`). The report leads with the pick and lists the top candidate windows, struck where they overlap an exclusion zone; the terminal prints a one-line `[report] best 500 kHz slot: …`. `--recommend-bw <hz>` selects the width (default 500000, `0` disables), so the same ranking serves 250 kHz etc. Adds `Store.channel_summary_by_bw` (per-(freq,bw) unblended summary) and a pure `report/slot_recommend` ranking module. The existing `--slot N` view is unchanged.

## 0.1.20 — 2026-09-16
- Guided setup: `lorascan setup` walks a new user from a bare install to an uploading station — it checks SPI is enabled and that the spidev/gpiod deps and the device permissions are in place, imports the radio pins from a running/installed meshtasticd or openHOP config (or a shipped profile, or manual entry), validates the wiring with probe + selftest and explains any failure in plain language, sets the station location, verifies the community share endpoint and does a first upload, and prints an ASCII band graph so the radio is visibly hearing the band. It writes `~/.config/lorascan/config.yaml`, which `scan`/`share`/`upload` now read by default, so the daily commands need no flags (an explicit flag still wins; with no config file the behaviour is unchanged). Also in this release: a software-driven chip-select — a GPIO `nss` pin — now works on a single-radio host (the wiring meshtasticd and many hand-wired HATs use), where P1 refused it; and the README now leads with the guided setup, with the manual wiring/import steps kept below as details.

## 0.1.19 — 2026-09-15
- Hotfix 2: the map page (`lorascan-share-server` GET /, since 0.1.13) nested an f-string that reused its enclosing quote — also Python 3.12-only — so a self-hosted server on Debian 12 raised SyntaxError. Fixed. The suite's static check now walks the 3.12 tokenizer's f-string tokens (catches both shipped forms), and the release script refuses to publish unless the package compiles on the bench's Python 3.11.

## 0.1.18 — 2026-09-15
- Hotfix: 0.1.17's report and map page used a Python 3.12-only f-string form and failed with `SyntaxError` on Python 3.11 (Debian 12). Fixed, plus a test that compiles the package under python3.11 when present and a static check for the 3.12-only pattern. Install 0.1.18 instead of 0.1.17.

## 0.1.17 — 2026-09-15
- Exclusion zones (Loomwave/lorascan#9, pinztrek): 902.000–903.250 and 926.750–928.000 MHz (band edges + 33 cm repeater segments) are measured but never recommended — quietest channels, the slot ranking and the candidate card list viable channels first and strike excluded ones through; the heat map, band summary (static SVG and plotly) and the community map page hatch the zones. `--exclude a-b,c-d` (MHz) overrides, `--no-exclude` disables. `map.json` and the report JSON carry `exclusions`; slot CSV gains an `excluded` column.

## 0.1.16 — 2026-09-14
- Listening time is measured, not guessed: every energy row now stores its dwell (`dwell_s`; existing databases gain the column automatically), and the share document's `hours` sums it. 0.1.4–0.1.15 computed hours as samples × 8.2 µs, which is right for the on-chip scan engine and ~0 for the polled engine — a polled submitter's cell showed 0.0 h on the community map. Rows written before this release fall back to an estimate by engine.

## 0.1.15 — 2026-09-14
- Basemap tile source is configurable: `--tiles-url` / `--tiles-attribution` or `LORASCAN_TILES_URL` / `LORASCAN_TILES_ATTRIBUTION` (default OpenStreetMap); `deploy/share-server/k8s.yaml` reads them from an optional Secret so a Carto key never lands in the manifest. The URL is embedded in the public page — restrict the key to the site's referrer at the provider.

## 0.1.14 — 2026-09-14
- Community map page: OpenStreetMap basemap via Leaflet (cdnjs) as a progressive enhancement — cells drawn as coloured rectangles from `/v1/map.json` with popups (dashed = single submitter), OSM attribution; the inline SVG grid stays and is only hidden once a real tile has rendered with Leaflet's stylesheet applied, so a blocked CDN or tile server leaves the full data visible.

## 0.1.13 — 2026-09-14
- First front end for the community endpoint: `GET /` on `lorascan-share-server` renders the fleet map page (0.1° cells coloured by mean busy fraction, single-submitter cells hatched, each cell linking to OpenStreetMap; band summary across submitters; fleet-wide quietest channels; hour×weekday matrix from hour-granularity uploads; cells and submitters tables) as self-contained HTML with inline SVG — no JavaScript, no CDN. `GET /v1/map.json` returns the aggregates behind it. Flagged (miscalibrated) uploads are counted but never merged.

## 0.1.12 — 2026-09-14
- `auto --from openhop` supports SPI SX1262 radios: `radio_type` decides (sx1262 → spidev profile from the `sx1262:` block; sx1262_ch341 → USB; a config without `radio_type` but with a `ch341:` block still resolves as USB; other types fail loudly); location falls back to `repeater.{latitude,longitude}`. The mini-YAML reader parses real openHOP configs (lists of mappings, indentless sequences, `!!binary |` block scalars, empty flow lists/maps). Contributed by @wehooper4 (PR #8), validated on the FRNebra repeater.
- Fix on top: ordinary indented sequences under a key (`key:` then deeper `- item`) parse again.

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
