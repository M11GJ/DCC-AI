FROM ghcr.io/open-webui/open-webui:v0.11.4
# v0.11.0とv0.11.3のキャッシュ済みHTML向けに旧entry名を互換配信する。
RUN set -eu; \
    entry_dir=/app/build/_app/immutable/entry; \
    asset_dir=/app/build/_app/immutable/assets; \
    start_src="$(find "$entry_dir" -maxdepth 1 -type f -name 'start.*.js' ! -name 'start.BcfU-xkP.js' -print -quit)"; \
    app_src="$(find "$entry_dir" -maxdepth 1 -type f -name 'app.*.js' ! -name 'app.WPjxzi0v.js' -print -quit)"; \
    css_src="$(find "$asset_dir" -maxdepth 1 -type f -name '0.*.css' ! -name '0.D-1LH_Ar.css' -print -quit)"; \
    test -n "$start_src" && test -n "$app_src" && test -n "$css_src"; \
    cp "$start_src" "$entry_dir/start.BcfU-xkP.js"; \
    cp "$start_src" "$entry_dir/start.DVTkig5c.js"; \
    cp "$app_src" "$entry_dir/app.WPjxzi0v.js"; \
    cp "$app_src" "$entry_dir/app.DyaYDsfM.js"; \
    cp "$css_src" "$asset_dir/0.D-1LH_Ar.css"; \
    cp "$css_src" "$asset_dir/0.DgJjT2oH.css"
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
