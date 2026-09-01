# syntax=docker/dockerfile:1
# Build context is the repository root: the image ships the gateway and the web
# client it serves on the same origin (ADR 0011).
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOMEFLOW_WEB_CLIENT_DIR=/app/web

WORKDIR /app

COPY backend/pyproject.toml backend/README.md ./
COPY backend/src ./src
RUN python -m pip install --no-cache-dir . \
    && useradd --system --uid 10001 --no-create-home homeflow

COPY apps/web ./web

USER 10001
EXPOSE 8000

CMD ["python", "-m", "homeflow"]
