FROM node:22-alpine AS frontend
WORKDIR /src/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 GMV_FRONTEND_DIR=/app/static
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl --fail --silent --show-error https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /usr/share/keyrings/postgresql.asc \
    && echo 'deb [signed-by=/usr/share/keyrings/postgresql.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main' > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends postgresql-client-17 \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv==0.8.22
COPY pyproject.toml uv.lock ./
COPY backend ./backend
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"
COPY alembic.ini ./
COPY migrations ./migrations
COPY --from=frontend /src/frontend/dist ./static
RUN useradd --uid 10001 --create-home vault && mkdir /data && chown vault:vault /data
USER vault
EXPOSE 8080
CMD ["uvicorn", "genmedia.main:app", "--host", "0.0.0.0", "--port", "8080"]
