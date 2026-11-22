# Sandboxed HTML/GSAP motion renderer: renders LLM-written scenes frame-exactly to MP4.
# Runs on the internal (no-egress) network, read-only rootfs, non-root, resource-limited.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers HOME=/tmp

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg curl fonts-inter fonts-jetbrains-mono \
      fonts-noto-color-emoji fonts-noto-core \
 && rm -rf /var/lib/apt/lists/*

RUN pip install playwright==1.65.2 \
 && playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

# Vendored animation libraries — the page can never load anything from the network.
ARG GSAP=3.15.1
RUN mkdir -p /opt/runner/vendor && cd /opt/runner/vendor \
 && for f in gsap DrawSVGPlugin MotionPathPlugin TextPlugin SplitText; do \
      curl -fsSL "https://cdn.jsdelivr.net/npm/gsap@${GSAP}/dist/${f}.min.js" -o "${f}.min.js"; \
    done

COPY runner.py /opt/runner/runner.py
RUN useradd --uid 1001 --create-home render && mkdir -p /out && chown render /out && chmod 777 /out
USER render
WORKDIR /tmp
EXPOSE 8000
CMD ["python", "/opt/runner/runner.py"]
