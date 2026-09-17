# lorascan share endpoint v1 (share.lorascan.app) — protocol for the deployer

Status: client implemented in lorascan 0.1.4 (`lorascan share --to`, `lorascan upload`); **reference server implemented
in lorascan 0.1.11** (`lorascan/share_server.py`, console script `lorascan-share-server`, tests driven by the real client)
with a deployment kit in `deploy/share-server/` (Dockerfile, k8s.yaml, systemd unit, handover README); to be deployed by
moe-main at `https://share.lorascan.app`. Design context: `2026-09-14-lorascan-design.md` §4a (what is shared, why
aggregates only, anti-pollution) and its low-bandwidth addendum (Matt approved options 1+2+3 on 2026-09-14 ~14:50Z).

## What the client sends

One JSON document, format `lorascan-share/2`, gzip-compressed, produced by `lorascan share`. Fields:

| field | meaning |
|---|---|
| `format` | `"lorascan-share/2"` (reject anything else with 400) |
| `tool` | `"lorascan 0.1.4"` |
| `generated` | ISO-8601 UTC |
| `submitter` | 32-hex random token made on the client's first share; no account. Also sent as header `X-Lorascan-Submitter` (must match the body) |
| `board` | profile name, e.g. `nebra-duo-hat` |
| `calibration` | `"relative (uncalibrated)"` or `"calibrated (offset +x.x dB applied)"` |
| `cell` | `{"lat","lon","size_deg"}` rounded to the cell, or `null` |
| `granularity` | `"hour"` or `"day"` |
| `energy[]` | per (freq_hz, bw_hz, bucket): `bucket` (`YYYY-MM-DDTHH:00Z` or `YYYY-MM-DD`), `bucket_s` (3600/86400), `n_rows`, `n_samples`, `hours`, `floor_p10_med`, `p90_med`, `peak_max`, `busy_mean` |
| `cad[]` | per (freq_hz, bw_hz, sf): `n_cad`, `hits`, `hit_rate`, `longest_run` (whole-run summary; may be empty) |
| `decode[]` | per (freq_hz, network, preset): `dwell_s`, `n_ok`, `n_crc_err`, `rssi_med` (counts only; may be empty) |
| `when` | day granularity only: 7×24 mean busy fraction (weekday × UTC hour), `null` where unmeasured |

Sizes measured on the bench survey: 130 channels × 1 hour = 31.6 KB JSON / 2.2 KB gzip; per day ≈ 53 KB gzip at
hour granularity, ≈ 3 KB at day granularity.

## Endpoints

### `GET /v1/watermark`
Header `X-Lorascan-Submitter: <token>`. Reply `200 {"latest": {"hour": "<bucket>|null", "day": "<bucket>|null"}}` =
the highest energy bucket stored for that submitter per granularity; unknown submitter → the same shape with nulls
(404 is also accepted by the client and treated as "send everything").

### `POST /v1/share`
Headers `Content-Type: application/json`, `Content-Encoding: gzip`, `X-Lorascan-Submitter`. Body = the document
above, possibly **filtered**: the client sends only energy rows with `bucket >= latest[granularity]` (inclusive, so a
bucket that was still filling at the previous upload is updated). Reply
`200 {"accepted": <energy rows stored or replaced>, "latest": "<highest bucket now stored>"}`.

**Idempotency (required):** upsert energy rows on the key `(submitter, freq_hz, bw_hz, bucket_s, bucket)`, cad on
`(submitter, freq_hz, bw_hz, sf)`, decode on `(submitter, freq_hz, network, preset)`; the newest `generated` wins.
A re-sent document must never double-count. The client retries 5xx and network errors (3 attempts, backoff 2 s × n)
and treats 4xx as final, so validation errors must be 4xx with a short text body.

Limits: body ≤ 4 MB gzipped, ≤ 64 MB inflated; rate ≤ 60 POST / hour / submitter; reject `format` ≠ `lorascan-share/2`,
a body `submitter` ≠ header, `cell.size_deg` < 0.01, or any `energy[].floor_p10_med` > −70 dBm on more than 90 % of
channels (flag, do not merge: §4a anti-pollution). Never store the raw request beyond the parsed rows.

## Storage and the map (later)
Object storage or a small SQLite/Postgres: one row per key above plus `received_at`, `tool`, `board`, `calibration`,
`cell`. The map renderer at `https://lorascan.app` (separate task) reads only these tables; it weights cells by distinct
submitters × hours and draws single-submitter cells hatched.

## Acceptance for the deployment task
1. `lorascan share --db x.db --granularity day --to https://share.lorascan.app` from a Pi prints `uploaded …
   accepted N`; a second run prints `skipped` = all but the last bucket and `accepted` ≤ 1 bucket's rows.
2. `lorascan upload file.json --to …` from a laptop with the same file → same rows, no double count (row count in
   storage unchanged).
3. `GET /v1/watermark` for that submitter returns the last bucket; for a random token returns nulls.
4. A 5 MB gzipped body → 413; a `format: lorascan-share/1` body → 400 with a one-line reason.
