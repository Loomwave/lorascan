# lorascan — a LoRa-chipset band scanner for the 902–928 MHz band — Design

**Status:** approved in effect by Matt 2026-09-14 ("focus only on the scanner app"); P1 and P2 built and bench-validated the same day (see `lorascan/README.md`, card 847). Defaults in §0 were taken as decided. Community sharing (§4a) and lorascan.app were added at Matt's request during the build.
**Owner of the design:** Matt. **Author of specifics:** loomwave-engineering.
**Companion (after approval):** an implementation plan in `docs/superpowers/plans/`, and a new public repo `Loomwave/lorascan` (proposed) so the wider mesh community can use it without access to this private repo.

---

## 0. The request, verbatim, and the decisions it forces

> SPI or CH341-connected radio on Debian. Adjustable GPIO mappings. Scan the entire 915 MHz ISM / 33 cm ham band for interference before a new LoRa deployment; because LoRa interferes strongly with LoRa, use an actual LoRa chipset. Quick scan (< 1 h) or continuous long-term scanning to find sporadic sources. Output: a long-term heat map of the spectrum showing interference and clean areas. Plus an option to test candidate frequencies and LoRa settings against the environment.

| # | Question | Decision (default unless Matt says otherwise) |
|---|---|---|
| 1 | Where does it live | A new public repo `Loomwave/lorascan`, Apache-2.0 (this repo's licence). Until it exists this spec lives here. |
| 2 | Language | **Python 3.11+** package, `pip install lorascan` on Debian 12/13 and Raspberry Pi OS. Contributors can read and extend it; no cross-compiling. The Rust `loomwave-sx126x` crate in this repo (spidev + CH341 backends, proven on the fleet) is the *reference* for the register-level behaviour and could be extracted later if speed ever matters; it is not the plan. |
| 3 | Radios | SX1262 first (SX1261/SX1268 fall out for free: same command set). LR11xx and SX127x are explicitly **out of scope for v1**; the backend interface leaves room. |
| 4 | Buses | (a) Linux `spidev` + `libgpiod` for HATs and wired modules; (b) **CH341 USB-SPI** bridge (MeshToad / PineDio-USB class, VID:PID 1a86:5512) entirely in userspace via libusb — no kernel module; (c) **serial-attached probe**: any RadioLib-capable board (Heltec, RAK, T-Beam…) running a small probe sketch that streams the same measurements over USB serial. (c) is what gets the most people scanning and is P3. |
| 5 | What "interference" means | Three layers, all recorded, each with its own heat map: **energy** (modulation-agnostic RSSI statistics per channel), **LoRa presence** (Channel Activity Detection per spreading factor — the LoRa-specific detector the request asks for), and **identification** (passive decode attempts against a table of known networks: Meshtastic, MeshCore, LoRaWAN, Loomwave). |
| 6 | Transmit | **Receive-only by default.** The candidate-test mode has an optional two-radio active link test that transmits; it needs an explicit `--tx-ok` flag, a power cap, and a duty-cycle cap, and it never transmits on a channel the passive pass measured as occupied by a decoded network unless told to. |

---

## 1. What the SX1262 can actually measure (the research the request asked for)

Sources: SX1261/2 datasheet Rev 2.2 (Dec 2024), Semtech AN1200.85 (Introduction to Channel Activity Detection), Semtech `sx1302_hal` `util_spectral_scan` + `loragw_sx1261.c`, RadioLib's `SX126x_Spectrum_Scan` example and `SpectrumScan.py`, and this project's own `infrad bandscan` (14-channel energy sweep, tower-validated 2026-08-01) and the KD4HME site survey it produced.

### 1.1 Instantaneous RSSI (`GetRssiInst`, opcode 0x15)
- Returns one byte while in RX; **signal power = −RssiInst/2 dBm** (datasheet §13.5.4, Table 13-81). Valid only in RX mode after the front end settles; the settling delay depends on bandwidth (Table 13-82 gives the GFSK table; LoRa BW 125/250/500 kHz settle in the low-ms range). Unsettled reads decode to ≈ −127.5 dBm or ≥ 0 dBm and must be discarded — this project's firmware already does that (`client/src/main.cpp`, bandscan sampler).
- Each read is one SPI transaction. Practical sample rates: ~1–1.5 kHz over `spidev` (this project's infrad samples every 700 µs), but only ~100–300 Hz over a CH341 because every transaction is several 1 ms-scale USB URBs. **Host-polled RSSI cannot see LoRa symbol structure and, over USB, cannot even see short packets reliably.** It is the fallback sampler, not the primary one.
- Absolute accuracy is a few dB and shifts with front-end gain (Rx boosted gain register 0x08AC = 0x96 vs power-saving 0x94, datasheet §9.6) and with any external LNA/FEM. The tool treats RSSI as **relative** unless a per-board `rssi_offset_db` is calibrated.

### 1.2 On-chip spectral scan (the Semtech scan patch)
- Semtech ships a binary patch for the SX126x (from `sx1302_hal`, BSD-licensed, redistributed by RadioLib as `SX126x_patch_scan.h`) that turns the chip into an **RSSI histogram engine**: after `SetRx(infinite)`, opcode 0x9B `SetSpectralScanParams(nb_scan, interval)` samples RSSI `nb_scan` times at a fixed interval (RadioLib default 8.2 µs) with the averaging window in register 0x089B, and leaves a **33-level histogram** in registers at 0x0401 (16-bit counts); status in 0x07CD (0x0F ongoing, 0xFF completed, 0xF0 aborted).
- Levels are **4 dB apart**: level i = −4·i + rssi_offset dBm for i = 0…31, level 32 = everything below. RadioLib's default offset is −11 dBm, so the bins span −11 … −135 dBm. Semtech's gateway tool applies a per-board calibrated offset.
- This gives ~120 000 RSSI samples per second **inside the chip**, independent of SPI/USB latency, which is exactly what a CH341-attached radio needs. One 2 000-sample scan takes ≈ 16 ms plus settle; a 130-channel sweep of the whole band at 125 kHz steps completes in well under a minute.
- Bench-learned (2026-09-14, Nebra HAT, now encoded in the tool): the patch samples RSSI through the **GFSK receiver**, so the modem must be configured the way Semtech's `util_spectral_scan` does (packet type GFSK, bitrate 0x001400, no shaping, RX bandwidth from Table 13-45, fdev 0x02E90F, buffer base 0x80, register 0x08AC = 0xCB, RSSI window 0x089B = 0x14) — in LoRa mode every sample lands in the underflow level; the scan status register keeps the previous COMPLETED flag until the new scan is running, so the host must wait the nominal scan time before polling and reject a histogram whose sample count is not the requested one; and the RAM patch is wiped by any chip reset (the CAD/decode layers' re-init), so it is re-uploaded before each scan when a reset has happened.
- Caveats, stated up front: the patch is undocumented by Semtech for the SX1262 (it was written for the SX1261 on Corecell gateways), RadioLib flags it "experimental, may have undocumented side effects", the patch must be re-uploaded after every reset/sleep, and it must be **verified on each supported board** before it is trusted. The tool therefore keeps the host-polled sampler as a fallback and reports which engine produced each row.

### 1.3 Channel Activity Detection (CAD) — the LoRa-specific detector
- `SetCadParams` (0x88, datasheet §13.4.7): `cadSymbolNum` ∈ {1, 2, 4, 8, 16} symbols, `cadDetPeak`, `cadDetMin`, `cadExitMode` (CAD_ONLY returns to standby; CAD_RX stays in RX for `cadTimeout × 15.625 µs` if activity is seen). A CAD lasts `N × Tsym + 32/BW` (AN1200.85), where Tsym = 2^SF / BW; e.g. SF11/BW250 with 2 symbols ≈ 16.5 ms, SF7/BW125 ≈ 2.3 ms.
- CAD correlates against LoRa chirps at the **configured SF and BW only**; it is blind to other SFs (the datasheet and the literature both say cross-SF signals look like noise to it). That is the property the request needs: sweeping CAD across SF7…SF12 at the bandwidths in use (62.5, 125, 250, 500 kHz) yields a **LoRa-presence map per (channel, SF, BW)** that energy detection cannot give, and it separates "LoRa is here" from "something is here".
- Detection thresholds (`cadDetPeak`/`cadDetMin`) are SF/BW dependent and the datasheet defers to **AN1200.48** for the recommended table; RadioLib's defaults come from that table and are the starting values. CAD can miss weak signals below the noise floor and can false-trigger; the tool records raw hit counts and run lengths, never a single verdict, and calibrates the false-alarm rate on a channel the energy layer shows as quiet.
- CAD also catches **payload chirps of an ongoing transmission**, not only preambles (AN1200.85), so it is a duty-cycle estimator for LoRa traffic, not just a preamble counter.

### 1.4 Passive decoding — who lives here
- Set the radio to a known network's exact PHY and count CRC-valid frames per channel: Meshtastic (sync word 0x2B; presets: ShortTurbo BW500/SF7/CR4-5, ShortFast BW250/SF7, ShortSlow BW250/SF8, MediumFast BW250/SF9, MediumSlow BW250/SF10, LongTurbo BW500/SF11/CR4-8, LongFast BW250/SF11/CR4-5, LongModerate BW125/SF11/CR4-8; US default LongFast on slot 20 = 906.875 MHz; slot k of a preset = 902.0 + bw/2 + k·bw MHz, 104 slots at 250 kHz); MeshCore (US recommended 910.525 MHz, SF7, BW 62.5 kHz, CR 4/5; older deployments SF11/BW250; RadioLib default sync 0x12 unless the firmware sets one — to be confirmed against MeshCore source before shipping the table); LoRaWAN US915 (64 uplinks 902.3 + 0.2·k MHz at BW125, 8 uplinks 903.0 + 1.6·k MHz at BW500, 8 downlinks 923.3 + 0.6·k MHz at BW500, sync 0x34, 8-symbol preamble); Loomwave (911.5 MHz fleet; bench cells on 905.0; private sync). The table is a **user-editable YAML** so a community can add its own network.
- Payloads are **never stored** — only counts, RSSI, SNR, frequency error and lengths. This is a spectrum tool, not a sniffer.

### 1.5 Numbers that bound what a heat map can claim
- Receiver sensitivity with boosted gain (datasheet Table 3-x): BW125 SF7 −124 dBm, SF12 −137; BW250 SF7 −121, SF12 −134; BW500 SF7 −117, SF12 −129. An energy scan at BW125 sees anything a LoRa link would see at that bandwidth; narrower measurement BW raises the floor resolution but lengthens the sweep.
- LoRa co-channel rejection is only 5 dB at SF7 and 19 dB at SF12; adjacent-channel rejection at ±1.5·BW is 60–72 dB. So a strong neighbour one channel over is not a problem, a same-channel LoRa signal a few dB down is — which is why the **LoRa-presence layer matters more than raw energy** for choosing a LoRa channel.
- Frequency resolution: the PLL step is 32 MHz / 2^25 ≈ 0.95 Hz; image calibration must be run for the 902–928 band (`CalibrateImage` 0x98, band bytes 0xE1/0xE9) after every cold start.

---

## 2. Approaches considered

**A. Energy-only sweep, RadioLib-style (what infrad's `bandscan` already is).** Cheap, proven on the tower. It cannot say whether energy is LoRa, cannot see which SF families are in use, and under-reads busy channels (the KD4HME survey showed undecoded LoRa traffic pinning the "floor"). Rejected as the whole product; kept as layer 1.

**B. Three-layer scanner (energy histogram + CAD sweep + passive decode) with a plan engine and a SQLite time series — RECOMMENDED.** Answers all four asks (quick, continuous, heat map, candidate test) with one data model. Each layer is independently testable against a fake radio. The extra cost is scheduling: a full three-layer pass over 130 channels × 6 SFs takes minutes, so the plan engine must budget dwell adaptively.

**C. SDR-based analyser (RTL-SDR/HackRF) with a LoRa demodulator.** Best raw spectrum picture, but it is not what was asked for (the request wants the real chipset's view, which is what a deployment will experience), it needs a second device class, and open LoRa demodulators are fragile at SF11/12. Rejected; the report can import an SDR CSV later if anyone wants an overlay.

---

## 3. Architecture

```
 lorascan CLI  ──►  Plan engine  ──►  Measurement layers  ──►  Store (SQLite)  ──►  Reports
   scan quick          (dwell,          energy | cad | decode                          heat maps, band
   scan survey          revisit,        ──────────────────                             summary, candidate
   scan watch           budget)          Radio driver (SX126x)                         report card,
   test <freq> …                         ──────────────────                             CSV/Parquet/JSON,
   report …                              HAL: spidev+gpiod | ch341 (libusb) | serial probe   MQTT (optional)
```

Package layout (`lorascan/`):
- `hal/`: `spidev_gpiod.py`, `ch341.py`, `serial_probe.py`, `fake.py` (records/replays SPI transcripts for tests). One interface: `xfer(bytes) -> bytes`, `busy() -> bool`, `dio1() -> bool`, `reset()`, `set_rxen(bool)`, `set_txen(bool)`, `sleep(s)`.
- `radio/sx126x.py`: the command layer (init with TCXO/DIO2 options, `set_frequency`, `set_lora`, `rx_continuous`, `rssi_inst`, `spectral_scan(...)`, `cad(...)`, `receive(timeout)`, `transmit(...)`, `calibrate_image`). Mirrors the Rust crate's sequencing, which is bench-proven.
- `measure/`: `energy.py` (histogram or polled), `cad.py`, `decode.py`; each returns typed rows.
- `plan/`: `quick.py`, `survey.py`, `watch.py`, `candidate.py`; a plan is a generator of (channel, layer, params, dwell) steps with a budget.
- `store/`: SQLite schema + writers; exporters.
- `report/`: `heatmap.py`, `band.py`, `candidate.py`, `html.py` (self-contained plotly.js page), `png.py` (matplotlib).
- `boards/*.yaml`: hardware profiles.
- `networks/*.yaml`: known-network PHY tables.

### 3.1 Hardware profiles (adjustable GPIO mappings)
```yaml
name: waveshare-sx1262-hat        # also: nebra-e22p-hat, meshtoad-v3-ch341, generic-spidev
bus: {type: spidev, dev: /dev/spidev0.0, hz: 2000000}
pins: {nss: kernel, reset: 18, busy: 20, dio1: 16, rxen: null, txen: null}   # gpiod chip/line numbers
radio: {tcxo_v: 1.8, dio2_rf_switch: true, rx_boosted: true, max_tx_dbm: 10}
cal:   {rssi_offset_db: 0.0, scan_offset_dbm: -11}                          # measured per board, see §8
```
A CH341 profile replaces `bus` with `{type: ch341, vid: 0x1a86, pid: 0x5512}` and names CH341 pins (MeshToad: CS 0, RXen 1, Reset 2, Busy 4, IRQ 6 — the map this project already validated in `infra/crates/sx126x/src/ch341.rs`). `lorascan probe` prints what it found on the bus and runs a self-test (chip version read, TCXO error clear, image calibration, one RSSI read).

### 3.2 Measurement layers — what each row records
- **energy** row: `ts, freq_hz, bw_hz, engine (scan|poll), n, hist[33] (counts), floor_dbm (P10), p50, p90, peak, busy_frac (fraction above floor + T dB, T default 8)`. The histogram is stored so any later threshold can be recomputed.
- **cad** row: `ts, freq_hz, bw_hz, sf, symbols, n_cad, hits, longest_run, det_peak, det_min`. Hit rate and run length together separate real traffic from false alarms.
- **decode** row: `ts, freq_hz, network, preset, n_ok, n_crc_err, rssi_med, snr_med, ferr_hz_med, len_med`.
- All rows carry `run_id`, `board`, `antenna` (free text), `lat/lon` (optional), and `engine`.

### 3.3 Plans
- **quick** (< 1 h, default ~15 min): energy sweep of the whole 902–928 MHz band on a 200 kHz grid at BW125 (130 channels, 2 000-sample histograms, two passes); CAD sweep of {SF7, SF9, SF11} × {BW125, BW250} on every channel whose busy fraction or peak exceeded threshold, plus on the known-network channels; decode pass on the known-network channels (30 s each). Ends with the band summary and a ranked list of the quietest channels at each bandwidth.
- **survey** (continuous): round-robin over the grid with adaptive dwell — channels with recent activity are revisited more often (bounded so no channel is starved beyond a maximum revisit interval, default 10 min); hourly CAD sweeps; decode passes on a schedule; time buckets of 1 min stored, rolled up to hourly/daily aggregates. Runs for days; the store is append-only and safe to read while running. `--duration` or SIGTERM stops it cleanly.
- **watch**: a fixed list of frequencies (e.g. the 14 known-user channels, or a proposed 4-channel plan) at high time resolution, all three layers. This is the tool for "is 921 MHz really quiet at 3 a.m. on Saturdays".
- **test** (candidates): for each candidate (freq, SF, BW, CR): a long passive dwell at exactly those settings (energy at that BW, CAD at that SF/BW, decode at that PHY), producing a **report card**: floor, occupancy, LoRa-presence rate, decoded foreign frames, expected sensitivity at that SF/BW, and a rank among the candidates. Optional **active link test** with a second `lorascan` on another host: numbered probe frames at the candidate settings, measuring PER / RSSI / SNR both directions under the live band; opt-in (`--tx-ok`), capped power (`max_tx_dbm` from the profile), duty-cycle limited (default ≤ 1 %), refuses channels the passive pass decoded as another network's unless `--force`.

### 3.4 Store and export
- SQLite (`lorascan.db`), WAL mode: tables `runs`, `energy`, `cad`, `decode`, `events` (threshold crossings with start/end), `rollup_hour`, `rollup_day`. Rows are never updated.
- `lorascan export --csv|--parquet|--json` per table and per time range; `lorascan report` reads the DB only. Optional MQTT publisher (`--mqtt`) emits per-channel summaries for Grafana/Home Assistant users.

### 3.5 Reports and visuals (the deliverable the request is about)
1. **Long-term spectrum heat map**: x = time (minute/hour/day buckets), y = frequency (the grid), colour = occupancy (busy fraction) with a second map for level (P90 dBm); LoRa-presence overlay as hatching; known-network channels as labelled rules on the y axis; ham 33 cm sub-band segments as a side band. Interactive (plotly, zoom/hover) and static PNG.
2. **Band summary** (the KD4HME chart, generalised): per channel floor→peak bar, busy %, CAD hit rate per SF, decoded networks — sorted by frequency, with a "quietest N channels at BW X" callout.
3. **When does it happen**: hour-of-day × day-of-week occupancy matrix per channel or per band segment — the view that finds sporadic sources.
4. **SF map**: frequency × SF heat map of CAD hit rate — shows which LoRa families occupy which channels.
5. **Candidate report card**: one table + one radar/rank figure per test run, with the caveats (measurement duration, antenna, board offset) printed on the card.
6. A **live page** (`lorascan serve`, optional, P3) for continuous mode: the same figures re-rendered from the DB every minute.

### 3.6 Error handling
- Radio faults (BUSY stuck, IRQ timeout, XOSC error, patch not accepted): the driver retries once with a full re-init; a second failure marks the step `failed` in the store and the plan skips ahead — a survey never dies because of one bad transaction. Device errors are read (`GetDeviceErrors`) and logged with the step.
- USB detach (CH341 unplugged, serial probe reset): the HAL raises `Detached`; the run pauses, re-probes every 10 s, resumes with a new segment; the gap is recorded as an event so the heat map shows "no data" rather than "quiet".
- Time: rows use UTC from the host clock; a run records the clock source; the report warns when NTP is unsynchronised (`timedatectl`), because "3 a.m." claims are the point of the tool.
- Every threshold (busy T dB, CAD params, revisit bounds) is a CLI/profile value with a stated default; nothing is silently hard-coded.

### 3.7 Testing
- Unit: HAL fake with recorded SPI transcripts (from the bench SX1262 and the MeshToad) so the command layer, the histogram maths (bin → dBm, percentiles, busy fraction), the plan budgeting and the report rendering run in CI with no hardware.
- Property tests on the plan engine (every channel revisited within its bound; budgets never exceeded).
- Hardware smoke (bench, PM-sequenced): the Nebra HAT (pins already known) and the MeshToad CH341 stick; the acceptance run reproduces the 2026-08-01 KD4HME sweep on the same 14 channels and must agree with infrad's `bandscan` on floor within ±3 dB and on the busy ranking.
- A `lorascan selftest` for users: reads chip version, calibrates, runs one scan on a quiet channel and one on the busiest known channel, and prints pass/fail with the numbers.

---

## 4. Phasing (each phase ships something usable)
- **P1 — scan**: spidev HAL, SX1262 driver, energy layer (scan patch + polled fallback), quick + survey plans, SQLite, heat map + band summary (HTML + PNG). Validated on the bench HAT against infrad's bandscan.
- **P2 — LoRa**: CAD layer with the AN1200.48 table, decode layer with the network YAML, watch plan, passive candidate test + report card, SF map, hour×day matrix, the share-file format + `share --dry-run` (§4a).
- **P3 — reach**: CH341 HAL, serial-probe HAL + the probe sketch, active two-radio link test, MQTT, `serve`, Debian packaging, the community upload endpoint + overall map (§4a).

## 4a. Community sharing (Matt, 2026-09-14 12:5xZ): an optional upload so an overall map can be built
- **Opt-in per run** (`lorascan share --db … --to https://…`, or `--share` on a scan); nothing leaves the host otherwise. The report page shows what would be shared before the first upload.
- **What is shared:** per-channel aggregates only — for each (frequency, bandwidth, hour bucket): floor P10, P90, busy fraction, CAD hit rate per SF, decoded-network counts — plus the board profile name, the calibration state, and a **coarse location cell** (default: a 0.1° × 0.1° grid cell, ~10 km; the user may choose a finer cell or none). Never payloads, never raw sample streams, never a precise position. The share file is a JSON document the user can read and edit before sending.
- **Where it goes:** a community repository at **lorascan.app** (domain registered by Matt 2026-09-14): uploads to `https://share.lorascan.app/v1/share` (a small HTTPS endpoint backed by object storage), the overall map at `https://lorascan.app`; deployment of the web side is a moe-main task once the format has real files behind it. The map publishes: cells coloured by measured occupancy per channel family (Meshtastic slots, LoRaWAN, MeshCore, other), time-of-day profiles per cell, and per-cell "quietest channels" tables. A submitter id is a random token generated on first share (no account), revocable by deleting the token; a contributor may also sign shares with an optional key so a region's operators can filter to trusted sources.
- **Anti-pollution:** the aggregate weights each cell by number of distinct submitters and by measurement hours; single-submitter cells are drawn hatched; obviously miscalibrated uploads (floor above −70 dBm on every channel, or offsets outside ±20 dB) are flagged rather than merged.
- **Phasing:** the share-file format and `lorascan share --dry-run` land in P2; the endpoint and the map renderer (lorascan.app) are a separate small project (P3+), specified in their own doc once the format has real files behind it, and deployed by moe-main.
- **Low-bandwidth uplinks (tester concern via Matt, 2026-09-14 14:4xZ — some sites sit behind metered 2G, satellite, or no link at all).** Measured on the bench survey db: one hour of 130 channel×hour aggregates is 31.6 KB JSON / 2.2 KB gzipped, so hour granularity is ~53 KB gzipped per day and day granularity (130 rows + the 7×24 when-matrix) ~3 KB per day. The endpoint design therefore carries: (1) `Content-Encoding: gzip` on the POST and a `--granularity hour|day` knob on `share` (day is enough for the overall map); (2) **incremental, idempotent uploads** — rows keyed on (submitter, freq, bw, bucket), the server answers with its highest bucket per submitter, the client sends only later buckets, so a dropped link costs one small retry; (3) an optional `--budget <bytes>/day` that picks the coarsest granularity that fits and drops CAD/decode tables if it must; (4) **store-and-forward**: `share` writes the file, `lorascan upload <file>` works from any machine, the submitter token travels inside the file, so a no-uplink site hands the file to a laptop/phone/USB stick and it uploads later. Deferred to P4: a mesh-carried daily summary (~4 B/channel ≈ 520 B, three Loomwave frames, relayed by a gateway node with internet) and MQTT as a transport (same aggregates, only helps where a broker is already reachable).

## 5. Out of scope for v1 (named so nobody re-derives it)
SX127x / LR11xx backends; SDR import; automatic channel-plan generation (the tool ranks, a human chooses); any decryption or payload storage; Windows/macOS.

## 6. Open questions for Matt (defaults in §0 apply until answered)
1. Public repo name and owner (`Loomwave/lorascan`?) and whether the spec should move there now or after P1.
2. Python (default) versus Rust-with-the-existing-crate.
3. Which boards you can put on the bench for validation: an SX1262 HAT on a Pi, a MeshToad/CH341 stick, and a second radio for the active test.
4. Whether the 33 cm ham sub-band overlay should follow the ARRL band plan by default (it is only a label layer).

## 7. Security, privacy, regulatory (short)
Receive-only unless `--tx-ok`; transmissions obey the profile's power cap and duty-cycle cap and are logged; no payloads are stored; the report never geolocates third parties (counts and RSSI only). The tool does not decide legality of any emission — the operator does.

## 8. Calibration note (how a heat map's dBm is made honest)
`lorascan calibrate` with a signal generator or a known transmitter at a measured level sets `rssi_offset_db` and `scan_offset_dbm` in the board profile; without it the report labels every level "relative (uncalibrated)". Two boards' heat maps are comparable only when both are calibrated or when the report is read in relative terms (busy fraction, ranks) — which is the primary product anyway.
