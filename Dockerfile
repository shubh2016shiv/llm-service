FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"

RUN python -m venv "${VIRTUAL_ENV}"


FROM ghcr.io/astral-sh/uv:0.7.3 AS uv-binary


FROM base AS builder

WORKDIR /build

RUN apt-get update \
    && apt-get install --yes --no-install-recommends build-essential gcc \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv-binary /uv /uvx /bin/
COPY pyproject.toml uv.lock ./

RUN uv sync \
    --frozen \
    --no-dev \
    --no-install-project \
    --python "${VIRTUAL_ENV}/bin/python"


FROM base AS runtime

WORKDIR /app

RUN groupadd --system appuser \
    && useradd --system --gid appuser --create-home --home-dir /home/appuser appuser

COPY --from=builder /opt/venv /opt/venv
COPY app ./app
COPY config ./config

ENV APP_ENVIRONMENT=production \
    CONFIG_DIR=config \
    SERVICE_NAME=llm-provider-service \
    UVICORN_HOST=0.0.0.0 \
    UVICORN_PORT=8000

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
