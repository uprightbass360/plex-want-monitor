FROM python:3.12-slim

RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin app \
    && mkdir /data && chown app:app /data

WORKDIR /app
COPY pyproject.toml ./
COPY app/ app/
RUN pip install --no-cache-dir .

ENV WM_DATA_DIR=/data WM_PORT=8787
VOLUME /data
EXPOSE 8787
USER app

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/healthz', timeout=3).status == 200 else 1)"]

CMD ["python", "-m", "app"]
