FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /usr/local/bin/uv
ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_HTTP_TIMEOUT=120 UV_CONCURRENT_DOWNLOADS=4 PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
RUN useradd --create-home app && mkdir /models && chown app:app /models
USER app
# The API port is reachable only inside the Compose network, where nginx sets X-Forwarded-For.
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips=*"]

FROM base AS transcription
USER root
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project --extra transcription
USER app

FROM transcription AS test
USER root
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-install-project --extra transcription
COPY tests ./tests
USER app
CMD ["pytest", "-m", "not transcription"]
