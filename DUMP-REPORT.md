# Bulk data-dump endpoints — /v1/dump.json and /v1/dump.csv

Status: DONE

## What was implemented

`lorascan/share_server.py`:
- Endpoint-list comment (top of file) updated with two new lines documenting `/v1/dump.json` and `/v1/dump.csv`.
- `GET /v1/dump.json` — under `with db.lock:`, reads `db.rows("energy")` and `db.rows("submitters")`.
  Energy rows with `flagged == 1` are excluded; the `flagged` key is stripped from the rows that remain
  (all still-present rows are 0, so the value carries no information — it's just not published).
  Submitters are projected to exactly the public fields: `submitter, tool, board, calibration, cell_lat,
  cell_lon, cell_size, uploads, last_seen` (no new PII beyond what `/v1/map.json` already exposes).
  Response: `{"generated": <ISO-8601 UTC, second precision>, "count": <len(energy)>, "energy": [...], "submitters": [...]}`.
- `GET /v1/dump.csv` — same unflagged energy-row set, written via `csv.writer` to an `io.StringIO` (stdlib
  only), fixed column order: `submitter,freq_hz,bw_hz,bucket_s,bucket,hours,floor_p10_med,p90_med,peak_max,busy_mean`.
  Sent via `_send(200, text, "text/plain; charset=utf-8")`.
- No query params, no pagination, no new rate limiting (GET, same posture as `/v1/map.json`).
- No changes to any existing route, `POST /v1/share`, `validate()`, `ingest()`, or the `ShareDB` class.

`tests/test_share_server.py`:
- `_store()` fixture helper gained an optional `bw_hz=125_000` parameter (previously hardcoded), so a test
  can build a doc entirely on a 500 kHz channel width — used to prove `bw_hz` survives into the dump.
- Three new tests:
  - `test_dump_json_has_bw_hz_and_matching_count` — ingest a doc built with `bw_hz=500_000`; `/v1/dump.json`
    returns 200, non-empty `energy`, every row carries `bw_hz` (and at least one is 500000), `count == len(energy)`,
    and the submitter's public fields appear in `submitters`.
  - `test_dump_json_excludes_flagged_uploads` — reuses the existing miscalibrated-upload pattern (all channel
    floors forced above -70 dBm -> `flagged: true` on ingest); asserts none of that submitter's rows appear in
    the dump and no row carries a `flagged` key at all.
  - `test_dump_csv_header_and_row_count` — asserts the exact header line and that the row count matches the
    unflagged row count in the DB.

## Test summary

`pytest tests/test_share_server.py -v`: new tests confirmed red first (404 on both new routes, `HTTPError`),
then green after implementation — 8/8 passed.
`pytest tests/ -q`: 246 passed, 1 skipped (pre-existing skip, unrelated).
`python3 -m compileall lorascan/share_server.py`: compiles clean.

## Concerns

- Target interpreter is documented as Python 3.11 (Debian 12), but this sandbox only has Python 3.12 on
  PATH (no `python3.11` binary available) — compileall/pytest ran under 3.12. The new code uses no f-strings
  at all in the added lines (avoiding the PEP 701 gotcha entirely) and only stdlib (`csv`, `io`, `datetime`),
  so there's no known 3.11-vs-3.12 syntax risk, but this wasn't verified under the actual target interpreter.
- `generated` timestamp in `/v1/dump.json` is wall-clock "now" at request time (server generation time), not
  tied to the newest ingested row — consistent with `/v1/map.json`'s existing behavior but worth noting for
  API consumers expecting a data freshness watermark (they should look at `bucket`/`received_at` per row).
- `flagged` is excluded from the JSON dump per spec; CSV never included it (fixed column list), so both
  formats are consistent on that point.
