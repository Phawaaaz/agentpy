# Agentic Harness API server.
FROM python:3.11-slim

WORKDIR /app

# Deps first for layer caching.
COPY requirements.txt requirements-server.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-server.txt \
    && pip install --no-cache-dir "psycopg[binary]>=3.1"

COPY . .

# Persist sessions/memory/logs/workspaces + the JWT secret on a volume.
VOLUME ["/app/.harness", "/app/workspaces"]

EXPOSE 8000
# HARNESS_* env vars configure everything (see .env.example). At minimum set
# HARNESS_MODEL, HARNESS_API_KEY, HARNESS_JWT_SECRET, and (for multi-user)
# HARNESS_DB_URL to a Postgres URL.
CMD ["uvicorn", "interfaces.server:app", "--host", "0.0.0.0", "--port", "8000"]
