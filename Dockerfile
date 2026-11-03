# syntax=docker/dockerfile:1

# ---- frontend (SvelteKit static build)
FROM --platform=$BUILDPLATFORM node:24-alpine AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- deno: the JavaScript runtime yt-dlp needs for YouTube
FROM denoland/deno:bin-2.9.7 AS deno

# ---- runtime
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CONFIG_DIR=/config \
    DOWNLOADS_DIR=/downloads \
    STATIC_DIR=/app/static \
    BUNDLED_YTDLP=/opt/yt-dlp/yt-dlp \
    DENO_DIR=/config/cache/deno \
    XDG_CACHE_HOME=/config/cache \
    PUID=1000 \
    PGID=1000 \
    PORT=8080

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates tzdata curl procps \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd -g 1000 app && useradd -u 1000 -g app -d /config -M -s /usr/sbin/nologin app

COPY --from=deno /deno /usr/local/bin/deno

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Fallback copy of yt-dlp. On the first start it is copied to /config/bin and from then on updated in the
# background, so a running container gets fixes for YouTube changes without a new image.
ADD https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp /opt/yt-dlp/yt-dlp
RUN chmod 755 /opt/yt-dlp/yt-dlp && /opt/yt-dlp/yt-dlp --version

COPY backend/app ./app
COPY --from=frontend /src/build ./static
COPY docker/entrypoint.sh /entrypoint.sh
COPY docker/amplicon /usr/local/bin/amplicon
RUN chmod 755 /entrypoint.sh /usr/local/bin/amplicon

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/api/health" >/dev/null || exit 1
ENTRYPOINT ["/entrypoint.sh"]
