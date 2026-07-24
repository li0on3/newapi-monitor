FROM oven/bun:1.3.14-alpine AS dashboard-build

WORKDIR /build
COPY web/package.json web/bun.lock ./
RUN bun install --frozen-lockfile
COPY web/ ./
RUN bun run build

FROM python:3.13.14-alpine3.23

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup -g 10001 -S monitor \
    && adduser -u 10001 -S -D -H -G monitor monitor \
    && install -d -o monitor -g monitor -m 0700 /data

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=monitor:monitor --chmod=0444 newapi_monitor.py dashboard_auth.py dashboard_data.py dashboard_http.py dashboard_key_usage.py dashboard_newapi_console.py dashboard_settings.py dashboard_setup.py dashboard_sso.py dashboard_app.py ./
COPY --chown=monitor:monitor --chmod=0555 --from=dashboard-build /build/dist /app/static

USER 10001:10001

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=3)"

CMD ["uvicorn", "dashboard_app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log"]
