# One image for every Python service of the platform; each Deployment runs its own command.
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY common common
COPY control-plane control-plane
COPY operator operator
COPY model-gateway model-gateway
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.13-slim
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN useradd --uid 10001 --user-group --no-create-home --home-dir /tmp fleet
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY bundles /app/bundles
COPY seed /app/seed
USER 10001:10001
