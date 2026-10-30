# Contributing to Carriole

Thank you! Issues, ideas and pull requests are welcome, in English or French.

## Report a bug, suggest an idea

Open an [issue](https://github.com/TLecae/Carriole/issues/new/choose). For a security vulnerability, follow
[SECURITY.md](SECURITY.md) (private report).

## Add a modifier

A modifier is one Python file: see [examples/modifiers/surface_calculator.py](examples/modifiers/surface_calculator.py)
and the guide [writing a modifier](docs/ecrire_un_modificateur.md) (French). It can live in your own plugins folder
without changing Carriole; propose it here if it is useful to others.

## Develop

Requirements: [uv](https://docs.astral.sh/uv/), Node.js 22+.

```bash
uv sync                                   # Python environment
cd frontend && npm ci && npx vite build   # user interface
cd ..
uv run etl open                           # local backend + UI in the browser
cd desktop && npm ci && npm start         # or the desktop window (Electron)
```

The first test run downloads DuckDB's spatial extension (internet needed once). Tests never touch your own Carriole
data: each one runs in a temporary `ETL_HOME`.

Before opening a pull request (the CI runs the first three on Linux and Windows):

- `uv run ruff format .` then `uv run ruff check .` (lines up to 120 characters);
- `uv run pytest engine api store worker assistant -m "not slow"`; the PostgreSQL tests run only when `ETL_TEST_PG`
  points to a disposable database;
- in `frontend/`: `npx tsc --noEmit`, `npx vitest run`, `npx vite build`;
- end-to-end tests (Playwright), after `npx vite build`: in `e2e/`, `npm ci` then `npx playwright test`. They use
  Microsoft Edge; elsewhere, `npx playwright install chromium` and set `E2E_CHANNEL=chromium`;
- on Windows, `powershell -ExecutionPolicy Bypass -File scripts\ci.ps1` runs the whole chain;
- after changing a modifier, regenerate the catalogue: `uv run etl modifiers doc` and
  `uv run etl modifiers doc --lang en --out docs/en/catalogue.md`;
- for an architecture choice, add a short decision record in [docs/decisions/](docs/decisions/).

User interface texts are written in French and translated to English (`uv run etl i18n check --lang en` lists missing
translations). Code comments and developer docs are mostly in French: English is fine in your contributions.

## Release (maintainer)

1. Set the version in `desktop/package.json` and update [CHANGELOG.md](CHANGELOG.md).
2. `git tag v<version> && git push origin v<version>`: the *Release* workflow builds the Windows installer and the
   Linux AppImage and creates a draft GitHub release, to review and publish.

By contributing, you agree that your contribution is released under the [MIT license](LICENSE).
