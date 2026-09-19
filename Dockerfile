# AquaOps ON — deployment image.
#
# One image, one process group: the modular monolith described in
# docs/architecture.md §3, so an operator with no IT staff can run the whole
# system on modest municipal hardware.
#
# Build and run:
#   docker build -t aquaops-on:u6 .
#   docker run --rm -p 8000:8000 -v aquaops-data:/data aquaops-on:u6
#
# The rules follow the reproducibility guidance in Nüst et al. (2020): pin the
# base image, install from a declared requirement set, copy only what is needed
# to serve, run as a non-root user, and declare the health probe in the image
# rather than in an operator's memory.

# syntax=docker/dockerfile:1

FROM python:3.12-slim AS runtime

# PYTHONDONTWRITEBYTECODE keeps the image free of __pycache__; PYTHONUNBUFFERED
# makes container logs appear in real time, which matters when the only
# observability an operator has is `docker logs`.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# curl is installed only so the HEALTHCHECK below has a client. It is a build
# dependency of the probe, not of the application.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/aquaops

# The runtime set, not the full development set: see requirements-runtime.txt
# for why wntr and pandas are deliberately absent.
COPY requirements-runtime.txt ./
RUN pip install --no-cache-dir -r requirements-runtime.txt

# Only what the application reads at request time. `examples/` and `tests/` are
# not copied: they are development artefacts and they would pull the simulation
# stack back in.
COPY app ./app
COPY modules ./modules
COPY rules ./rules
COPY data/networks ./data/networks

# The SQLite profile lives on a mounted volume so the compliance record survives
# a container replacement. /data is the only writable path the process needs.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin aquaops \
    && mkdir -p /data /tmp/mplcache \
    && chown -R aquaops:aquaops /data /tmp/mplcache

USER aquaops

# Configuration is external to the image, per the twelve-factor config rule:
# nothing below is baked in except a safe default that works for one town.
ENV AQUAOPS_DB_URL=sqlite:////data/aquaops.db \
    AQUAOPS_EVIDENCE_DIR=/data/evidence \
    MPLCONFIGDIR=/tmp/mplcache \
    AQUAOPS_PORT=8000 \
    AQUAOPS_WORKERS=1 \
    AQUAOPS_LOG_LEVEL=info

EXPOSE 8000

# The probe hits the same /health endpoint the integration transcript asserts,
# so "healthy" means the same thing to the orchestrator and to the test suite.
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${AQUAOPS_PORT}/health" || exit 1

# One worker by default. With SQLite, extra workers serialise on the single
# writer and buy nothing; the scale-out path is PostgreSQL plus more workers,
# which is exactly what AQUAOPS_WORKERS exists to make possible.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${AQUAOPS_PORT} --workers ${AQUAOPS_WORKERS} --log-level ${AQUAOPS_LOG_LEVEL} --proxy-headers"]
