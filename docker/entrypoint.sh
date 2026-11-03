#!/bin/sh
set -e

if [ "$(id -u)" = "0" ]; then
  PUID="${PUID:-1000}"
  PGID="${PGID:-1000}"
  groupmod -o -g "$PGID" app >/dev/null 2>&1 || true
  usermod -o -u "$PUID" -g "$PGID" app >/dev/null 2>&1 || true
  mkdir -p "$CONFIG_DIR" "$DOWNLOADS_DIR"
  # the config folder is small, keep it owned by the app user
  chown -R "$PUID:$PGID" "$CONFIG_DIR"
  # never chown a music library recursively, only an empty fresh mount point
  if [ "$(stat -c %u "$DOWNLOADS_DIR")" = "0" ] && [ -z "$(ls -A "$DOWNLOADS_DIR")" ]; then
    chown "$PUID:$PGID" "$DOWNLOADS_DIR"
  fi
  exec setpriv --reuid="$PUID" --regid="$PGID" --init-groups env HOME="$CONFIG_DIR" "$0" "$@"
fi

exec uvicorn app.main:app \
  --host 0.0.0.0 --port "${PORT:-8080}" \
  --proxy-headers --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-*}" \
  --no-access-log
