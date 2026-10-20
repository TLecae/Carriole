#!/usr/bin/env bash
# Construit l'application de bureau pour Linux (AppImage) : Python embarqué + dépendances, interface, Electron.
# Pendant Linux de scripts/build_desktop.ps1.
# Usage :  scripts/build_desktop.sh [--skip-runtime] [--dir-only]
#   --skip-runtime  réutilise desktop/build/runtime déjà préparé
#   --dir-only      produit seulement le dossier de l'application (desktop/release/linux-unpacked)
# Prérequis : uv, Node.js 22+, accès à internet (Python, extension DuckDB, grilles PROJ, Electron) — au build seulement.
# Résultat : desktop/release/Carriole-<version>-x86_64.AppImage
set -euo pipefail

skip_runtime=0
dir_only=0
for arg in "$@"; do
  case "$arg" in
    --skip-runtime) skip_runtime=1 ;;
    --dir-only) dir_only=1 ;;
    *) echo "option inconnue : $arg" >&2; exit 2 ;;
  esac
done

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
desktop="$root/desktop"
runtime="$desktop/build/runtime"
python="$runtime/python/bin/python3"
step() { printf '\n== %s\n' "$1"; }

if [ "$skip_runtime" -eq 0 ]; then
  step "Python embarqué"
  # Python géré par uv (python-build-standalone) : relocalisable, contrairement au Python du système.
  export UV_PYTHON_PREFERENCE=only-managed
  uv sync --frozen
  base="$(.venv/bin/python -c 'import sys; print(sys.base_prefix)')"
  rm -rf "$runtime"
  mkdir -p "$runtime/python"
  cp -a "$base/." "$runtime/python/"
  # sans ce qui ne sert pas (tests, tkinter, documentation)
  find "$runtime/python" -depth -type d \( -name test -o -name tests -o -name idlelib -o -name turtledemo \
    -o -name tkinter -o -name __pycache__ \) -exec rm -rf {} +
  rm -rf "$runtime/python/share" "$runtime/python/lib/"tcl* "$runtime/python/lib/"tk* "$runtime/python/lib/"itcl*
  site="$("$python" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"

  step "Dépendances (versions du verrou uv.lock)"
  requirements="$(mktemp)"
  uv export --frozen --no-dev --no-hashes --no-header --no-emit-workspace --format requirements-txt -o "$requirements" >/dev/null
  uv pip install --python "$python" --target "$site" --no-deps -r "$requirements"
  uv pip install --python "$python" --target "$site" --no-deps ./engine ./store ./api ./worker ./assistant
  rm -f "$requirements"

  step "Extension DuckDB spatial et grilles PROJ"
  "$python" "$desktop/build/prepare_runtime.py" "$runtime"
fi

step "Interface"
(cd frontend && npm ci --no-audit --no-fund && npx vite build)

step "Application Electron"
version="$(node -p "require('./desktop/package.json').version")"
printf '{"version": "%s"}\n' "$version" > "$desktop/build/install.json"
cd "$desktop"
npm ci --no-audit --no-fund
npx tsc
if [ "$dir_only" -eq 1 ]; then npx electron-builder --linux --dir; else npx electron-builder --linux --publish never; fi

step "Résultat"
ls -lh "$desktop/release/"*.AppImage 2>/dev/null || ls -d "$desktop/release/linux-unpacked"
