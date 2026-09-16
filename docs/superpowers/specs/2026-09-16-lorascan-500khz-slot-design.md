# lorascan 500 kHz Slot Recommendation — Design Spec

**Status:** approved design (Matt, 2026-09-16), proceeding to completion (spec review waived — "proceed to completion").
**Issue driver:** the report and slot ranking answer "which narrow (~62.5 kHz) channel is cleanest," but a 500 kHz deployment needs "which 500 kHz slot is best." The scan already captures true 500 kHz-bandwidth energy (`energy.bw_hz`), but the report's per-channel summary blends all bandwidths together, so the 500 kHz picture never surfaces, and the existing `--slot` view only buckets narrow channels and reports the worst sub-slice.

**Goal:** a prominent "best 500 kHz slot" recommendation in the report, driven by the actual 500 kHz-bandwidth energy plus the LoRa-presence (CAD/decode) evidence in each window, ranking a free grid of 500 kHz windows across the band and naming the single best placement.

**Architecture:** a bandwidth-aware channel summary in the store (the root fix), a pure ranking module that scores a free grid of width-W windows from that data + CAD/decode + a narrowband-carrier term + exclusion zones, a prominent auto-appearing report section that leads with the pick, and a one-line terminal recommendation from `lorascan report`. Reuses the existing exclusion logic and the candidate/slot scoring shape.

**Tech stack:** Python, standard library only; the existing `store`, `report/html`, `report/slots`, `exclusions` modules; pytest with fixture SQLite databases.

## Global Constraints

- **Target Python 3.11** (Debian 12 / Pi OS). Must `python3.11 -m compileall` clean; no PEP 701 f-string forms (no backslash in a replacement field, no nested f-string reusing the enclosing quote). `report/html.py` already documents this constraint at `_tr` — keep to it.
- **Standard library only**; no new dependencies.
- **Receive-only** — analysis only; no radio interaction changes.
- **Exclusion zones** (902.000–903.250 / 926.750–928.000 MHz, from `exclusions.DEFAULT_EXCLUSIONS`): a candidate window overlapping a zone is struck (never recommended), shown but marked, matching the existing "list viable first, strike excluded" convention.
- **Backward compatible:** the existing `--slot N` view and every current report section are unchanged. The new section is additive.
- **Reuse the established scoring shape** (candidate card / `slots.slot_view`): score is additive, lower = better, and includes a worst-floor term so a steady carrier cannot rank clean.

## Data model — the bandwidth-aware summary (root fix)

`store.channel_summary` collapses per frequency and stores only the last `bw_hz` seen, blending 62/125/250/500 kHz measurements into one floor/busy per channel — this is why the 500 kHz picture is lost. Add:

- `Store.channel_summary_by_bw(run_id=None, since=None) -> list[dict]` — the same aggregation as `channel_summary` but keyed on `(freq_hz, bw_hz)`, one row per measured bandwidth per frequency. Each row: `{freq_hz, bw_hz, floor_med, p90_med, peak_max, busy_mean, n_rows, n_samples}`. Implementation mirrors `channel_summary` exactly, with the accumulator keyed on `(freq_hz, bw_hz)` instead of `freq_hz`. `channel_summary` itself is left unchanged (other views depend on it).

## The recommendation — `report/slot_recommend.py` (new, pure)

`recommend_slots(by_bw, cad, decodes, width_hz=500_000, exclusions=None) -> dict` where `by_bw` is `channel_summary_by_bw`'s output, `cad` is `cad_summary`'s output (`{freq_hz, sf, bw_hz, hit_rate, n_cad, hits, …}`), `decodes` is `decode_summary`'s output (`{freq_hz, network, preset, n_ok, …}`). Returns `{"width_hz", "recommended": <window|None>, "windows": [<window>…]}`.

**Candidate grid (free, at the scan's resolution).** The candidate centers are exactly the frequencies that have a direct width-W measurement — i.e. every distinct `freq_hz` among `by_bw` rows with `bw_hz == width_hz`. Each defines a window `[center − width/2, center + width/2)`. (If no rows have `bw_hz == width_hz`, `recommended` is `None` and `windows` is empty — the caller shows the "scan with `--bw …,500`" note.)

**Per-window fields and score (lower = better), reusing the slot formula shape:**
- `busy_w` = the width-W row's `busy_mean` at the center (the occupancy a width-W receiver experiences) — the primary term.
- `floor_w`, `peak_w` = the width-W row's `floor_med`, `peak_max` (for display).
- `cad_hit_max`, `cad_sf_max` = the max `hit_rate` (and its SF) over `cad` rows whose `freq_hz` lies in the window.
- `decoded` (name:count string) and `decoded_frames` = sum of `n_ok` over `decode` rows whose `freq_hz` lies in the window.
- `carrier_pen` = `max(0, (worst_narrow_floor − band_best_floor) / 10.0)` where `worst_narrow_floor` is the worst (highest) `floor_med` over the NARROWEST bandwidth present per frequency inside the window (a narrow carrier a width-W RX would still suffer), and `band_best_floor` is the best (lowest) `floor_med` across the whole `by_bw` set. Same 10-dB-per-unit weighting as `slots.slot_view`.
- `excluded` = `exclusions.overlaps(start_hz, end_hz, zones)`.
- `score` = `round(busy_w + cad_hit_max + decoded_frames / 10 + carrier_pen, 4)`.

`windows` is sorted by `(excluded, score)` so viable slots come first, ties by score. `recommended` is the first non-excluded window, or `None` if all candidates are excluded. Each window also carries a plain-language `why` string (e.g. `"busy 2%, floor -119 dBm, no known LoRa, clear of exclusion zones"`; or `"carrier at 921.9 MHz raises the floor"` when `carrier_pen` dominates).

## Presentation

- **`report/html.py`:** a new section **"Recommended 500 kHz slot"** rendered by `render_from_data` whenever `d` carries recommendation data (see data flow). It leads with a one-line highlighted recommendation (center MHz, range, busy %, floor, LoRa presence, exclusion-clear), then a table of the top ~10 candidate windows: rank, centre MHz, range, score, busy %, floor, CAD %, decoded, and a struck row for excluded windows (reusing the exclusion styling already in the report). If there is no width-W data, the section is one note line: *"No 500 kHz-bandwidth data in this database — re-scan including `--bw …,500` to get a 500 kHz slot recommendation."*
- **`build_data` / `build_data_from_share`:** add the recommendation to `d` under `d["slot_recommend"]` by calling `recommend_slots(store.channel_summary_by_bw(...), cads, decodes, width_hz, exclusions)`. For the share path, width-W rows come from the share document's energy rows carrying `bw_hz` (if absent, `recommended` is `None`).
- **`cli.py cmd_report`:** after writing the HTML, print the one-line recommendation to the terminal (e.g. `[report] best 500 kHz slot: 903.30 MHz (903.05–903.55) busy 2% floor -119 dBm — clear`), or a one-line note when there is no width-W data. A new `--recommend-bw` option (default `500000`, `0` disables the section) selects the width; the window width equals the recommend width, so the same machinery serves 250 kHz etc. `--slot N` is untouched.

## Data flow

1. `lorascan report --db slot.db` → `cmd_report` → `render_report` → `build_data`.
2. `build_data` calls `channel_summary_by_bw`, `cad_summary`, `decode_summary`, then `recommend_slots(...)`, storing the result in `d["slot_recommend"]`.
3. `render_from_data` renders the section (auto, no flag) and `cmd_report` prints the one-line pick.
4. The scan side is unchanged — the data is already captured by `scan … --bw 62,125,250,500` (the existing 500 kHz recipe in the README).

## Error handling

- No width-W (`500000`) energy rows → `recommend_slots` returns `recommended: None`, `windows: []`; the section and the terminal line show the "re-scan with `--bw …,500`" note. No crash.
- All candidate windows overlap exclusion zones → `recommended: None`; the table still lists them struck; the note says every 500 kHz window touches an exclusion zone.
- `--recommend-bw 0` → the section and the print are skipped entirely (opt-out).

## Testing (TDD)

- `channel_summary_by_bw`: a fixture DB with the same frequency measured at two bandwidths returns two rows with the correct per-bw floor/busy (and does not blend), while `channel_summary` is unchanged.
- `recommend_slots`: fixture inputs with (a) a planted quiet 500 kHz region → it is `recommended` with the right center and a low score; (b) a narrowband carrier inside an otherwise-quiet window → `carrier_pen` pushes that window down the ranking; (c) CAD hits / decoded frames inside a window raise its score; (d) a window overlapping an exclusion zone → `excluded` True, never `recommended`, listed struck; (e) no width-W rows → `recommended` None, `windows` empty.
- Report: `build_data` populates `d["slot_recommend"]`; `render_from_data` emits the section with the recommendation line when data is present and the note when absent; a golden check that the excluded row is struck.
- CLI: `lorascan report` prints the recommendation line on a fixture DB with 500 kHz data and the note without it; `--recommend-bw 0` suppresses it; `--slot` output is unchanged.
- Whole package `python3.11 -m compileall` clean on the bench; `tests/test_py311_syntax.py` stays green.

## Out of scope (YAGNI)

- Standard US915 fixed 500 kHz channels and a user-supplied center list (Matt chose the free grid; either could be a later `--recommend-centers` option).
- Any change to the scan engine, the store schema (only a new read method), the share protocol, or the existing `--slot`, candidate-card, heat-map, and quietest-channel views.
- Recommending combinations of multiple 500 kHz channels (frequency plans) — this recommends a single best slot.
