# syntax=docker/dockerfile:1
# Targets:
#   app  — api + worker (default)
#   ml   — transcriber worker: app + WhisperX (CPU torch); consumes the "transcribe" queue
FROM python:3.12-slim AS base

ENV PYTHONPATH=/app PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg curl \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 app
# Brand font for burned-in watermark text (Style Profile v2.1)
RUN mkdir -p /usr/share/fonts/brand && curl -fsSL -o /usr/share/fonts/brand/Poppins-SemiBold.ttf \
      "https://github.com/google/fonts/raw/main/ofl/poppins/Poppins-SemiBold.ttf"
WORKDIR /app

# Install dependencies first (cached layer), from pyproject.toml only.
COPY pyproject.toml ./
RUN python - <<'EOF'
import subprocess, sys, tomllib
p = tomllib.load(open("pyproject.toml", "rb"))["project"]
deps = p["dependencies"] + p["optional-dependencies"]["dev"]
subprocess.check_call([sys.executable, "-m", "pip", "install", *deps])
EOF

# ---------------------------------------------------------------- ml (transcriber)
FROM base AS ml
# CPU-only torch first so whisperx doesn't pull multi-GB CUDA wheels.
RUN pip install torch==2.8.0 torchaudio==2.8.0 torchvision==0.23.0 \
      --index-url https://download.pytorch.org/whl/cpu \
 && pip install whisperx==3.8.6
ENV HF_HOME=/models/hf TORCH_HOME=/models/torch
RUN mkdir -p /models && chown app:app /models
COPY . .
RUN mkdir -p /data/storage && chown -R app:app /data /app
USER app
CMD ["celery", "-A", "app.workers.celery_app", "worker", "-Q", "transcribe", "-l", "info", "--concurrency", "1", "-n", "transcriber@%h"]

# ---------------------------------------------------------------- app (default)
FROM base AS app
COPY . .
RUN mkdir -p /data/storage && chown -R app:app /data /app
USER app
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
