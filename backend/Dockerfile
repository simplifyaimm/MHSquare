# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONPATH=/app PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg curl \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 app
WORKDIR /app

# Install dependencies first (cached layer), from pyproject.toml only.
COPY pyproject.toml ./
RUN python - <<'EOF'
import subprocess, sys, tomllib
p = tomllib.load(open("pyproject.toml", "rb"))["project"]
deps = p["dependencies"] + p["optional-dependencies"]["dev"]
subprocess.check_call([sys.executable, "-m", "pip", "install", *deps])
EOF

COPY . .
RUN mkdir -p /data/storage && chown -R app:app /data /app
USER app

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
