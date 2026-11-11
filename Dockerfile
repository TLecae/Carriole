# Carriole en mode serveur (docs/serveur.md, ADR 0052) : plusieurs utilisateurs dans leur navigateur.
#   docker build -t carriole .
#   docker run -p 8080:8080 -e ETL_ADMIN_PASSWORD=… carriole      (exemple complet : docker/compose.yaml)

# ---------------------------------------------------------------------------------------------- interface
FROM node:22-bookworm-slim AS interface
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npx vite build

# ------------------------------------------------------------------------------------------------ serveur
FROM python:3.12-slim-bookworm
LABEL org.opencontainers.image.title="Carriole" \
      org.opencontainers.image.description="ETL for geographic data, server mode (several users in their browser)" \
      org.opencontainers.image.source="https://github.com/TLecae/Carriole" \
      org.opencontainers.image.licenses="MIT"
# bubblewrap : bac à sable du code Python des flux (ADR 0053) ; tini : transmet l'arrêt au serveur et à ses workers ;
# libexpat1 : demandée par GDAL (rasterio, pyogrio), absente de l'image « slim »
RUN apt-get update \
    && apt-get install -y --no-install-recommends bubblewrap ca-certificates libexpat1 tini \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.10.4 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/carriole/venv
WORKDIR /opt/carriole/src
COPY pyproject.toml uv.lock README.md ./
COPY engine engine
COPY store store
COPY api api
COPY worker worker
COPY assistant assistant
# versions du verrou uv.lock, paquets de Carriole installés (pas en mode éditable : les sources ne servent plus)
RUN uv sync --frozen --no-dev --no-editable && rm -rf /root/.cache

# extensions DuckDB (spatiale, https) et grilles PROJ téléchargées ici : le serveur n'en a pas besoin à l'exécution
COPY desktop/build/prepare_runtime.py /tmp/prepare_runtime.py
RUN /opt/carriole/venv/bin/python /tmp/prepare_runtime.py /opt/carriole && rm /tmp/prepare_runtime.py
COPY --from=interface /src/frontend/dist /opt/carriole/frontend
# contrôle : toutes les bibliothèques natives se chargent (une dépendance système manquante échoue ici, pas au démarrage)
RUN /opt/carriole/venv/bin/python -c "import duckdb, pyarrow, pyogrio, pyproj, rasterio, shapely; \
import etl_api.main, etl_worker.main; from etl_engine.registry import Registry; Registry.discover(None)" \
    && test -f /opt/carriole/frontend/index.html

ENV PATH=/opt/carriole/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    ETL_HOME=/var/lib/carriole \
    ETL_FRONTEND_DIR=/opt/carriole/frontend \
    ETL_DUCKDB_EXTENSIONS=/opt/carriole/duckdb_ext \
    PROJ_USER_WRITABLE_DIRECTORY=/opt/carriole/proj \
    ETL_HOST=0.0.0.0 \
    ETL_PORT=8080

RUN useradd --system --uid 10001 --user-group --create-home --home-dir /var/lib/carriole carriole
USER carriole
WORKDIR /var/lib/carriole
VOLUME ["/var/lib/carriole"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=4)"
ENTRYPOINT ["tini", "--", "etl", "serve", "--server"]
