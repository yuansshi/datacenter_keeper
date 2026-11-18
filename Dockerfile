# ---- UI build ---------------------------------------------------------------------------------
FROM node:22-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---- runtime ----------------------------------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY backend/pyproject.toml backend/README.md backend/LICENSE backend/NOTICE ./
COPY backend/licenses ./licenses
COPY backend/labkeeper ./labkeeper
RUN pip install . \
    && useradd --create-home --uid 1000 labkeeper \
    && mkdir -p /data \
    && chown labkeeper:labkeeper /data
COPY --from=ui /ui/dist /app/static
USER labkeeper
ENV LABKEEPER_HOST=0.0.0.0 LABKEEPER_PORT=8000 LABKEEPER_DATA_DIR=/data LABKEEPER_FRONTEND_DIST=/app/static
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
ENTRYPOINT ["labkeeper"]
CMD ["demo", "--data-dir", "/data"]
