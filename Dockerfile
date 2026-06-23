# syntax=docker/dockerfile:1
FROM python:3.14-alpine

ARG VORTNOTES_VERSION=1.0.8
LABEL org.opencontainers.image.title="VortNotes" \
      org.opencontainers.image.description="Self-hosted notes, content, and focus apps" \
      org.opencontainers.image.version="${VORTNOTES_VERSION}" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/vorticon/vortnotes"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    NOTES_DATA_DIR=/data \
    VORTNOTES_VERSION=${VORTNOTES_VERSION}

WORKDIR /app

RUN apk add --no-cache ca-certificates \
  && addgroup -g 10001 -S vortnotes \
  && adduser -u 10001 -S -D -H -G vortnotes -s /sbin/nologin vortnotes

COPY requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir --require-hashes -r /app/requirements.lock \
  && pip uninstall -y pip wheel setuptools

COPY --chown=10001:10001 . /app
RUN chmod 0755 /app/scripts/docker-entrypoint.sh \
  && mkdir -p /data/dbs /data/uploads /data/backups /data/config /data/logs \
  && chown -R 10001:10001 /data

VOLUME ["/data"]
USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -m scripts.container_healthcheck || exit 1

ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
