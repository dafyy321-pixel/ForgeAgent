FROM ghcr.io/astral-sh/uv:0.9.5 AS uv
FROM python:3.12.10-slim AS runtime
COPY --from=uv /uv /uvx /bin/
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock ./
COPY backend ./backend
RUN uv sync --frozen --no-dev --no-editable && groupadd -g 10001 forge && useradd -u 10001 -g forge forge
COPY migrations ./migrations
COPY alembic.ini ./
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 FORGE_DATA_DIR=/var/lib/forge
RUN mkdir -p /var/lib/forge && chown forge:forge /var/lib/forge
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "forgeagent.api:app", "--host", "0.0.0.0", "--port", "8000"]

FROM runtime AS manager
USER root
RUN apt-get update && apt-get install -y --no-install-recommends docker.io && rm -rf /var/lib/apt/lists/*
USER 10001:10001
CMD ["uvicorn", "forgeagent.sandbox_manager:app", "--host", "0.0.0.0", "--port", "8443", "--ssl-keyfile", "/run/tls/manager.key", "--ssl-certfile", "/run/tls/manager.crt"]

FROM node:22.14.0-alpine AS web-build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build
FROM nginx:1.28-alpine AS web
COPY --from=web-build /web/dist /usr/share/nginx/html
COPY infra/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 443
