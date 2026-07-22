FROM ghcr.io/open-webui/open-webui:main
COPY branding/out/favicon.png                  /app/backend/open_webui/static/favicon.png
COPY branding/out/favicon.svg                  /app/backend/open_webui/static/favicon.svg
COPY branding/out/favicon.ico                  /app/backend/open_webui/static/favicon.ico
COPY branding/out/favicon-96x96.png            /app/backend/open_webui/static/favicon-96x96.png
COPY branding/out/favicon-dark.png             /app/backend/open_webui/static/favicon-dark.png
COPY branding/out/apple-touch-icon.png         /app/backend/open_webui/static/apple-touch-icon.png
COPY branding/out/web-app-manifest-192x192.png /app/backend/open_webui/static/web-app-manifest-192x192.png
COPY branding/out/web-app-manifest-512x512.png /app/backend/open_webui/static/web-app-manifest-512x512.png
COPY branding/out/logo.png                     /app/backend/open_webui/static/logo.png
COPY branding/out/splash.png                   /app/backend/open_webui/static/splash.png
COPY branding/out/splash-dark.png              /app/backend/open_webui/static/splash-dark.png
COPY branding/out/favicon.png                  /app/build/favicon.png
COPY branding/out/favicon.svg                  /app/build/favicon.svg
COPY branding/out/favicon.ico                  /app/build/favicon.ico
COPY branding/out/favicon-96x96.png            /app/build/favicon-96x96.png
COPY branding/out/favicon-dark.png             /app/build/favicon-dark.png
COPY branding/out/apple-touch-icon.png         /app/build/apple-touch-icon.png
COPY branding/out/favicon.png                  /app/build/static/favicon.png
COPY branding/out/favicon.ico                  /app/build/static/favicon.ico
COPY branding/out/favicon-96x96.png            /app/build/static/favicon-96x96.png
COPY branding/out/favicon-dark.png             /app/build/static/favicon-dark.png
COPY branding/out/logo.png                     /app/build/static/logo.png
COPY branding/out/splash.png                   /app/build/static/splash.png
COPY branding/out/splash-dark.png              /app/build/static/splash-dark.png
RUN find /app/build -type f \( -name "*.gz" -o -name "*.br" \) -delete 2>/dev/null || true
RUN sed -i -E "s/^([[:space:]]*)WEBUI_NAME \+= ' \(Open WebUI\)'/\1pass/" /app/backend/open_webui/env.py || true
RUN find /app/build -type f \( -name "*.js" -o -name "*.html" \) -exec sed -i 's/ (Open WebUI)//g; s/(Open WebUI)//g' {} + 2>/dev/null || true
# cache-bust: version all in-app favicon references so cached browsers refetch the DCC "D"
RUN find /app/build -type f \( -name "*.js" -o -name "*.html" \) -exec sed -i 's#/static/favicon\.png#/static/favicon.png?v=dcc2#g; s#/static/favicon-dark\.png#/static/favicon-dark.png?v=dcc2#g; s#/static/favicon\.ico#/static/favicon.ico?v=dcc2#g' {} + 2>/dev/null || true
COPY branding/login_notice.html /tmp/login_notice.html
RUN IDX=$(find /app/build -maxdepth 1 -name "index.html" -print -quit) && \
    if [ -n "$IDX" ]; then cat /tmp/login_notice.html >> "$IDX"; fi; rm -f /tmp/login_notice.html
