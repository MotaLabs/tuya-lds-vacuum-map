FROM ghcr.io/astral-sh/uv:0.11.21 AS uv

FROM python:3.12.15-slim-bookworm

RUN apt-get update \
    && apt-get install --yes --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 1000 --create-home app \
    && mkdir /data \
    && chown app:app /data

COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-cache

ENV PATH="/app/.venv/bin:$PATH" \
    DATA_DIR=/data \
    PYTHONUNBUFFERED=1

USER app
VOLUME ["/data"]
CMD ["tuya-lds-vacuum-map"]
