# lorascan community share endpoint (reference implementation) — stdlib only, SQLite on a volume.
# Build from the repo root:  docker build -f deploy/share-server/Dockerfile -t lorascan-share-server:0.1.14 .
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY lorascan ./lorascan
RUN pip install --no-cache-dir . && mkdir -p /data && useradd -r -u 10001 share && chown share /data
USER share
VOLUME ["/data"]
EXPOSE 8081
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8081/healthz', timeout=2).status == 200 else 1)"
ENTRYPOINT ["lorascan-share-server", "--host", "0.0.0.0", "--port", "8081", "--db", "/data/share.sqlite"]
