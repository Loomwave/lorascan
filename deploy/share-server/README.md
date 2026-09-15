# Deploying the lorascan share endpoint (share.lorascan.app)

Handover for the cluster operator. The service is `lorascan-share-server` from the lorascan wheel
(reference implementation of `docs/superpowers/specs/2026-09-14-lorascan-share-endpoint.md` in the Loomwave
repo): stdlib Python, one process, SQLite in `/data/share.sqlite`, routes `GET /` (the community map page, inline SVG, no JS/CDN), `GET /v1/map.json`,
`GET /healthz`, `GET /v1/watermark`, `POST /v1/share`, `GET /v1/stats`. It has no TLS of its own: terminate TLS at the ingress.

## Steps
1. Build and push the image from the lorascan repo root (tag = the lorascan version):
   `docker build -f deploy/share-server/Dockerfile -t REGISTRY/lorascan-share-server:0.1.18 . && docker push …` (retag + rollout for each lorascan release that touches the server)
2. Edit `k8s.yaml`: image reference, `ingressClassName`, the cert-manager issuer. Point DNS `share.lorascan.app`
   at the ingress. `kubectl apply -f deploy/share-server/k8s.yaml`.
3. Verify: `curl -s https://share.lorascan.app/healthz` → `ok`; `curl -s https://share.lorascan.app/v1/stats` → JSON counts;
   `curl -s -H 'X-Lorascan-Submitter: 0000000000000000' https://share.lorascan.app/v1/watermark` → `{"latest": {"hour": null, "day": null}}`.
4. Back up `/data/share.sqlite` (WAL mode; copy with `sqlite3 share.sqlite ".backup /tmp/x.sqlite"`).

## Acceptance (from the spec; engineering runs 1–3 from the bench on request)
1. `lorascan share --db x.db --granularity day --to https://share.lorascan.app` prints `uploaded … accepted N`;
   a second run prints `skipped` = all but the last bucket and `accepted` ≤ one bucket's rows.
2. `lorascan upload file.json --to …` from another machine leaves `/v1/stats` row counts unchanged.
3. `GET /v1/watermark` for that submitter returns the last bucket; for a random token, nulls.
4. A 5 MB gzipped body → 413; a `format: lorascan-share/1` body → 400 with a one-line reason.

## Basemap (Carto or other)
The map page draws OpenStreetMap tiles by default. To use Carto (Matt has an API key), create a Secret and the
Deployment picks it up (`k8s.yaml` references it as optional):
```
kubectl -n lorascan create secret generic lorascan-tiles \
  --from-literal=url='https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png?api_key=YOURKEY' \
  --from-literal=attribution='&copy; <a href="https://carto.com/attributions">CARTO</a>, &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>'
```
The URL is embedded in the public page, so restrict the key to the `share.lorascan.app` referrer in Carto's console
before use. Flags `--tiles-url` / `--tiles-attribution` do the same for a systemd install.

## Knobs
`--rate-per-hour` (default 60 POSTs per submitter per hour), `--max-gzip` (4 MB), `--max-inflated` (64 MB).
Documents whose floor is above −70 dBm on more than 90 % of channels are stored with `flagged = 1` and must be
left out of any map. The map renderer (lorascan.app) is a separate task and reads only the SQLite tables.
