<#
Construit l'installeur de l'application de bureau : Python embarqué + dépendances, interface, Electron, NSIS.
Usage :  powershell -ExecutionPolicy Bypass -File scripts\build_desktop.ps1 [-SkipRuntime] [-DirOnly]
  -SkipRuntime  réutilise desktop\build\runtime déjà préparé
  -DirOnly      produit seulement le dossier de l'application (desktop\release\win-unpacked), sans installeur
Prérequis : uv, Node.js 22+, accès à internet (extension DuckDB, grilles PROJ, Electron, NSIS) — une seule fois, au build.
Résultat : desktop\release\Carriole-Setup-<version>.exe : assistant d'installation pour tous les utilisateurs, qui demande
les droits d'administrateur au lancement (fenêtre habituelle de Windows).
#>
param(
  [switch]$SkipRuntime,
  [switch]$DirOnly
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$desktop = Join-Path $root "desktop"
$runtime = Join-Path $desktop "build\runtime"
$python = Join-Path $runtime "python\python.exe"
$sitePackages = Join-Path $runtime "python\Lib\site-packages"

function Step([string]$title) { Write-Host "`n== $title" -ForegroundColor Cyan }
function Check([string]$what) { if ($LASTEXITCODE -ne 0) { throw "$what a échoué (code $LASTEXITCODE)" } }

if (-not $SkipRuntime) {
  Step "Python embarqué"
  uv sync --frozen
  Check "uv sync"
  $base = & (Join-Path $root ".venv\Scripts\python.exe") -c "import sys; print(sys.base_prefix)"
  if (Test-Path $runtime) { Remove-Item $runtime -Recurse -Force }
  New-Item -ItemType Directory -Path (Join-Path $runtime "python") | Out-Null
  # Le Python géré par uv est relocalisable : on le copie, sans ce qui ne sert pas (tests, tkinter, documentation).
  robocopy $base (Join-Path $runtime "python") /E /NFL /NDL /NJH /NJS /NP /XD test tests idlelib turtledemo tcl Doc __pycache__ /XF *.pyc | Out-Null
  if ($LASTEXITCODE -ge 8) { throw "robocopy a échoué (code $LASTEXITCODE)" }
  Remove-Item (Join-Path $runtime "python\Lib\tkinter") -Recurse -Force -ErrorAction SilentlyContinue

  Step "Dépendances (versions du verrou uv.lock)"
  $requirements = Join-Path $desktop "build\requirements.txt"
  uv export --frozen --no-dev --no-hashes --no-header --no-emit-workspace --format requirements-txt -o $requirements | Out-Null
  Check "uv export"
  uv pip install --python $python --target $sitePackages --no-deps -r $requirements
  Check "installation des dépendances"
  uv pip install --python $python --target $sitePackages --no-deps .\engine .\store .\api .\worker .\assistant
  Check "installation des paquets de l'atelier"

  Step "Extension DuckDB spatial et grilles PROJ"
  & $python (Join-Path $desktop "build\prepare_runtime.py") $runtime
  Check "préparation du runtime"
}

Step "Interface"
Push-Location frontend
npm ci --no-audit --no-fund
Check "npm ci (frontend)"
npx vite build
Check "vite build"
Pop-Location

Step "Application Electron"
$version = (Get-Content (Join-Path $desktop "package.json") -Raw | ConvertFrom-Json).version
Set-Content -Path (Join-Path $desktop "build\install.json") -Value "{`"version`": `"$version`"}" -Encoding ascii
Push-Location $desktop
npm ci --no-audit --no-fund
Check "npm ci (desktop)"
if (-not (Test-Path "node_modules\electron\dist")) { node node_modules\electron\install.js; Check "téléchargement d'Electron" }
npx tsc
Check "tsc"
if ($DirOnly) { npx electron-builder --dir } else { npx electron-builder --publish never }
Check "electron-builder"
Pop-Location

Step "Résultat"
Get-ChildItem (Join-Path $desktop "release") -Filter *.exe | ForEach-Object { "{0}  {1:N0} Mo" -f $_.FullName, ($_.Length / 1MB) }
