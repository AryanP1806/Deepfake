# DFIR in one container: builds the web UI, installs the backend, downloads the detection models on first start.
#   CPU (works everywhere):  docker compose up --build
#   NVIDIA GPU:              docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build

# ---- 1. build the web UI -------------------------------------------------------------------------------------
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- 2. the app ----------------------------------------------------------------------------------------------
FROM python:3.11-slim-bookworm
ARG TORCH_INDEX=cpu
ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv UV_HTTP_TIMEOUT=600 UV_LINK_MODE=copy \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PATH="/opt/venv/bin:$PATH" \
    DFIR_DATA_DIR=/app/data DFIR_MODELS_DIR=/app/models DFIR_CHROMIUM_NO_SANDBOX=1

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 libsndfile1 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv

# yt-dlp needs a JavaScript runtime to read YouTube; reuse the Node binary from the UI build stage
COPY --from=web /usr/local/bin/node /usr/local/bin/node

WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock backend/.python-version ./
# CPU PyTorch by default (the lock file pins the CUDA 12.6 build used on the dev machine)
RUN sed -i "s#whl/cu126#whl/${TORCH_INDEX}#" pyproject.toml \
    && uv sync --no-install-project --no-dev \
       --extra capture --extra provenance --extra ocr --extra ml --extra asr --extra detectors \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* /root/.cache

COPY backend/dfir ./dfir
COPY backend/third_party ./third_party
COPY scripts /app/scripts
COPY models.lock.json /app/models.lock.json
COPY --from=web /web/dist /app/frontend/dist
COPY docker/entrypoint.sh /entrypoint.sh
# guard against Windows line endings (CRLF) breaking the script inside Linux
RUN tr -d '\r' < /entrypoint.sh > /tmp/e.sh && mv /tmp/e.sh /entrypoint.sh

RUN useradd --create-home --uid 1000 dfir && mkdir -p /app/data /app/models \
    && chown -R dfir:dfir /app && chmod +x /entrypoint.sh
USER dfir
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=600s --retries=5 \
    CMD curl -fs http://localhost:8000/api/system > /dev/null || exit 1
ENTRYPOINT ["/entrypoint.sh"]
