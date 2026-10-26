# Composants tiers

Carriole est distribué sous licence MIT ([LICENSE](LICENSE)). L'installeur Windows et l'AppImage Linux embarquent
les composants ci-dessous, chacun sous sa propre licence (texte complet dans chaque paquet, dossier
`resources/runtime/python` de l'installation). Le code source de Carriole : https://github.com/TLecae/Carriole.

Également livrés : l'interpréteur **Python** (licence PSF), **Electron** et **Chromium** (MIT et licences listées
dans `LICENSES.chromium.html` à côté de l'exécutable), les extensions **spatial** (MIT ; GDAL, GEOS, PROJ) et
**httpfs** (MIT ; OpenSSL, licence Apache 2.0) de DuckDB et les grilles de transformation de l'**IGN** (Licence
Ouverte Etalab 2.0).

## Python

| Composant | Version | Licence |
| --- | --- | --- |
| affine | 3.0.1 | BSD-3-Clause |
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anthropic | 1.13.0 | MIT License |
| anyio | 4.15.1 | MIT |
| attrs | 26.1.0 | MIT |
| certifi | 2026.7.22 | Mozilla Public License 2.0 (MPL 2.0) |
| cffi | 2.1.1 | MIT-0 |
| charset-normalizer | 3.5.2 | MIT |
| click | 8.5.0 | BSD-3-Clause |
| colorama | 0.4.6 | BSD License |
| contourpy | 1.4.0 | BSD-3-Clause |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| docstring-parser | 0.18.0 | MIT License |
| duckdb | 1.5.6 | MIT License |
| et-xmlfile | 2.0.0 | MIT License |
| fastapi | 0.142.2 | MIT |
| h11 | 0.16.0 | MIT License |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpcore2 | 2.13.1 | BSD-3-Clause |
| httptools | 0.8.0 | MIT |
| httpx | 0.28.1 | BSD License |
| httpx2 | 2.13.1 | BSD-3-Clause |
| httpx2-jsfetch | 1.0 | voir PyPI (paquet propre à une autre plateforme) |
| idna | 3.20 | BSD-3-Clause |
| iniconfig | 2.3.1 | MIT |
| jiter | 0.17.0 | MIT |
| jsonschema | 4.26.0 | MIT |
| jsonschema-specifications | 2025.9.1 | MIT |
| markdown-it-py | 4.2.0 | MIT License |
| mdurl | 0.1.2 | MIT License |
| numexpr | 2.14.2 | MIT |
| numpy | 2.5.3 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| openai | 3.29.0 | Apache-2.0 |
| openpyxl | 3.1.5 | MIT License |
| opentelemetry-api | 1.45.1 | Apache-2.0 |
| oracledb | 26.0.1 | UPL-1.0 OR Apache-2.0 |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pluggy | 1.6.0 | MIT License |
| psutil | 7.2.2 | BSD-3-Clause |
| psycopg | 3.3.6 | LGPL-3.0-only — LGPL : bibliothèque non modifiée, source sur https://github.com/psycopg/psycopg |
| psycopg-binary | 3.3.6 | LGPL-3.0-only — LGPL : bibliothèque non modifiée, source sur https://github.com/psycopg/psycopg |
| pyarrow | 25.0.1 | Apache-2.0 |
| pycparser | 3.0 | BSD-3-Clause |
| pydantic | 2.13.5 | MIT |
| pydantic-core | 2.46.5 | MIT |
| pygments | 2.21.0 | BSD-2-Clause |
| pyogrio | 0.13.0 | MIT License |
| pyparsing | 3.3.3 | MIT |
| pyproj | 3.8.0 | MIT |
| pystac | 1.15.2 | Apache Software License |
| pystac-client | 0.9.0 | Apache Software License |
| pystac-core | 1.15.2 | Apache Software License |
| pystac-ext-classification | 2.0.1 | Apache Software License |
| pystac-ext-datacube | 2.2.1 | Apache Software License |
| pystac-ext-eo | 1.1.1 | Apache Software License |
| pystac-ext-file | 2.1.1 | Apache Software License |
| pystac-ext-grid | 1.1.1 | Apache Software License |
| pystac-ext-item-assets | 1.0.1 | Apache Software License |
| pystac-ext-label | 1.0.2 | Apache Software License |
| pystac-ext-mgrs | 1.0.1 | Apache Software License |
| pystac-ext-mlm | 1.4.1 | Apache Software License |
| pystac-ext-pointcloud | 1.0.1 | Apache Software License |
| pystac-ext-projection | 2.0.1 | Apache Software License |
| pystac-ext-raster | 1.1.1 | Apache Software License |
| pystac-ext-render | 2.0.0 | Apache Software License |
| pystac-ext-sar | 1.0.1 | Apache Software License |
| pystac-ext-sat | 1.0.1 | Apache Software License |
| pystac-ext-scientific | 1.0.1 | Apache Software License |
| pystac-ext-storage | 2.0.1 | Apache Software License |
| pystac-ext-table | 1.2.1 | Apache Software License |
| pystac-ext-timestamps | 1.1.1 | Apache Software License |
| pystac-ext-version | 1.2.1 | Apache Software License |
| pystac-ext-view | 1.0.1 | Apache Software License |
| pystac-ext-xarray-assets | 1.0.1 | Apache Software License |
| pytest | 9.1.1 | MIT |
| python-dateutil | 2.9.0.post0 | BSD License, Apache Software License |
| python-dotenv | 1.2.4 | BSD-3-Clause |
| pyyaml | 6.0.3 | MIT License |
| rasterio | 1.5.2 | BSD-3-Clause |
| referencing | 0.37.0 | MIT |
| requests | 2.34.2 | Apache Software License |
| rich | 15.0.0 | MIT License |
| rpds-py | 2026.9.1 | MIT |
| shapely | 2.1.2 | BSD License |
| shellingham | 1.5.4 | ISC License (ISCL) |
| six | 1.17.0 | MIT License |
| sniffio | 1.3.1 | MIT License, Apache Software License |
| sqlalchemy | 2.1.3 | MIT |
| starlette | 1.7.0 | BSD-3-Clause |
| truststore | 0.10.4 | MIT |
| typer | 0.27.3 | MIT |
| typing-extensions | 4.16.0 | PSF-2.0 |
| typing-inspection | 0.4.4 | MIT |
| tzdata | 2026.5 | Apache-2.0 |
| urllib3 | 2.8.0 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |
| uvloop | 0.23.0 | voir PyPI (paquet propre à une autre plateforme) |
| watchfiles | 1.3.0 | MIT License |
| websockets | 17.2 | BSD-3-Clause |

## Interface (navigateur)

| Composant | Version | Licence |
| --- | --- | --- |
| @codemirror/autocomplete | 6.20.3 | MIT |
| @codemirror/commands | 6.11.1 | MIT |
| @codemirror/lang-python | 6.2.1 | MIT |
| @codemirror/lang-sql | 6.10.0 | MIT |
| @codemirror/language | 6.13.1 | MIT |
| @codemirror/lint | 6.9.7 | MIT |
| @codemirror/search | 6.7.2 | MIT |
| @codemirror/state | 6.7.6 | MIT |
| @codemirror/streamparser | 6.0.0 | MIT |
| @codemirror/view | 6.43.14 | MIT |
| @lezer/common | 1.5.3 | MIT |
| @lezer/highlight | 1.2.5 | MIT |
| @lezer/lr | 1.4.11 | MIT |
| @lezer/python | 1.1.19 | MIT |
| @mapbox/jsonlint-lines-primitives | 2.0.3 | MIT |
| @mapbox/point-geometry | 1.1.0 | ISC |
| @mapbox/tiny-sdf | 2.2.0 | BSD-2-Clause |
| @mapbox/unitbezier | 1.0.0 | BSD-2-Clause |
| @mapbox/vector-tile | 3.0.0 | BSD-3-Clause |
| @maplibre/geojson-vt | 6.1.2 | ISC |
| @maplibre/maplibre-gl-style-spec | 26.4.4 | ISC |
| @maplibre/mlt | 1.3.0 | (MIT OR Apache-2.0) |
| @maplibre/vt-pbf | 4.3.2 | MIT |
| @marijn/find-cluster-break | 1.0.4 | MIT |
| @tanstack/react-virtual | 3.14.13 | MIT |
| @tanstack/virtual-core | 3.17.11 | MIT |
| @types/d3-color | 3.1.3 | MIT |
| @types/d3-drag | 3.0.7 | MIT |
| @types/d3-interpolate | 3.0.4 | MIT |
| @types/d3-selection | 3.0.12 | MIT |
| @types/d3-transition | 3.0.9 | MIT |
| @types/d3-zoom | 3.0.9 | MIT |
| @types/geojson | 7946.0.16 | MIT |
| @typescript/typescript-aix-ppc64 | 7.0.2 | Apache-2.0 |
| @typescript/typescript-darwin-arm64 | 7.0.2 | Apache-2.0 |
| @typescript/typescript-darwin-x64 | 7.0.2 | Apache-2.0 |
| @typescript/typescript-freebsd-arm64 | 7.0.2 | Apache-2.0 |
| @typescript/typescript-freebsd-x64 | 7.0.2 | Apache-2.0 |
| @typescript/typescript-linux-arm | 7.0.2 | Apache-2.0 |
| @typescript/typescript-linux-arm64 | 7.0.2 | Apache-2.0 |
| @typescript/typescript-linux-loong64 | 7.0.2 | Apache-2.0 |
| @typescript/typescript-linux-mips64el | 7.0.2 | Apache-2.0 |
| @typescript/typescript-linux-ppc64 | 7.0.2 | Apache-2.0 |
| @typescript/typescript-linux-riscv64 | 7.0.2 | Apache-2.0 |
| @typescript/typescript-linux-s390x | 7.0.2 | Apache-2.0 |
| @typescript/typescript-linux-x64 | 7.0.2 | Apache-2.0 |
| @typescript/typescript-netbsd-arm64 | 7.0.2 | Apache-2.0 |
| @typescript/typescript-netbsd-x64 | 7.0.2 | Apache-2.0 |
| @typescript/typescript-openbsd-arm64 | 7.0.2 | Apache-2.0 |
| @typescript/typescript-openbsd-x64 | 7.0.2 | Apache-2.0 |
| @typescript/typescript-sunos-x64 | 7.0.2 | Apache-2.0 |
| @typescript/typescript-win32-arm64 | 7.0.2 | Apache-2.0 |
| @typescript/typescript-win32-x64 | 7.0.2 | Apache-2.0 |
| @xyflow/react | 12.12.0 | MIT |
| @xyflow/system | 0.0.83 | MIT |
| bidi-js | 1.1.0 | MIT |
| classcat | 5.0.5 | MIT |
| codemirror | 6.0.2 | MIT |
| crelt | 1.0.7 | MIT |
| d3-color | 3.1.0 | ISC |
| d3-dispatch | 3.0.1 | ISC |
| d3-drag | 3.0.0 | ISC |
| d3-ease | 3.0.1 | BSD-3-Clause |
| d3-interpolate | 3.0.1 | ISC |
| d3-selection | 3.0.0 | ISC |
| d3-timer | 3.0.1 | ISC |
| d3-transition | 3.0.1 | ISC |
| d3-zoom | 3.0.0 | ISC |
| earcut | 3.2.4 | ISC |
| gl-matrix | 3.4.4 | MIT |
| i18next | 26.4.3 | MIT |
| json-stringify-pretty-compact | 4.0.0 | MIT |
| kdbush | 4.1.0 | ISC |
| maplibre-gl | 6.13.0 | BSD-3-Clause |
| minimist | 1.2.8 | MIT |
| murmurhash-js | 1.0.0 | MIT |
| pbf | 5.1.2 | BSD-3-Clause |
| potpack | 2.1.0 | ISC |
| protocol-buffers-schema | 3.6.1 | MIT |
| quickselect | 3.0.0 | ISC |
| react | 19.3.0 | MIT |
| react-dom | 19.3.0 | MIT |
| require-from-string | 2.0.2 | MIT |
| resolve-protobuf-schema | 2.1.0 | MIT |
| scheduler | 0.28.0 | MIT |
| style-mod | 4.1.4 | MIT |
| tinyqueue | 3.0.0 | ISC |
| use-sync-external-store | 1.7.0 | MIT |
| w3c-keyname | 2.2.8 | MIT |
| zustand | 5.0.15 | MIT |
