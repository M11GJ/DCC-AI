FROM ghcr.io/open-webui/open-webui:main
COPY out/ /tmp/brand/
RUN set -e; \
    for d in /app/backend/open_webui/static /app/build /app/build/static; do \
      [ -d "$d" ] || continue; \
      for f in favicon.png favicon.svg favicon.ico favicon-96x96.png favicon-dark.png apple-touch-icon.png web-app-manifest-192x192.png web-app-manifest-512x512.png logo.png splash.png splash-dark.png; do \
        [ -f /tmp/brand/$f ] && cp /tmp/brand/$f "$d/$f" || true; \
      done; \
    done; \
    sed -i -E "s/^([[:space:]]*)WEBUI_NAME \+= ' \(Open WebUI\)'/\1pass/" /app/backend/open_webui/env.py; \
    grep -n "Open WebUI')" /app/backend/open_webui/env.py || echo "suffix line patched"
