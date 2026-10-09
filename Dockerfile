# Minimal image for the access-desk HTTP service.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY accessdesk ./accessdesk
RUN pip install --no-cache-dir .

# Directory, policy and writable state. Mount your own directory.yaml and policy over these in production.
COPY data/directory.yaml ./data/directory.yaml
COPY policies ./policies
RUN useradd --create-home app && mkdir -p /data && chown -R app /data /app
USER app

ENV ACCESSDESK_HOST=0.0.0.0 \
    ACCESSDESK_PORT=8080 \
    ACCESSDESK_DIRECTORY=/app/data/directory.yaml \
    ACCESSDESK_POLICY=/app/policies/default.yaml \
    ACCESSDESK_AUDIT_LOG=/data/access-audit.jsonl \
    ACCESSDESK_GRANTS=/data/grants.jsonl \
    ACCESSDESK_CACHE_DIR=/data/cache

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/healthz').status==200 else 1)"
CMD ["accessdesk", "serve"]
