<#
CI locale Windows : mêmes étapes qu'un pipeline. Voir docs/decisions/0020-application-de-bureau.md
Usage :  powershell -ExecutionPolicy Bypass -File scripts\ci.ps1 [-SkipE2E] [-SkipSlow] [-Desktop]
Prérequis : uv, Node.js 22+, Microsoft Edge (livré avec Windows ; ou E2E_CHANNEL=chromium après « npx playwright install chromium »).
PostgreSQL de test (facultatif) : ETL_TEST_PG (tests Python) et E2E_PG_HOST (scénario de connexion de l'interface).
#>
param(
  [switch]$SkipE2E,
  [switch]$Desktop,
  [switch]$SkipSlow
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$python = Join-Path $root ".venv\Scripts\python.exe"

function Step([string]$title) { Write-Host "`n== $title" -ForegroundColor Cyan }
function Check([string]$what) { if ($LASTEXITCODE -ne 0) { throw "$what a échoué (code $LASTEXITCODE)" } }

# Les tests ne touchent ni aux dossiers de l'utilisateur ni à ses variables ETL_* (le conftest isole ETL_HOME).
Get-ChildItem Env:ETL_* | Where-Object { $_.Name -notin @("ETL_TEST_PG") } | ForEach-Object { Remove-Item "Env:$($_.Name)" }

Step "Environnement Python"
uv sync --frozen
Check "uv sync"

Step "Lint (ruff)"
& $python -m ruff check .
Check "ruff check"
& $python -m ruff format --check .
Check "ruff format"

Step "Tests Python (pytest, Windows natif)"
$pytest = @("-m", "pytest", "engine", "api", "store", "worker", "assistant", "-q", "-p", "no:cacheprovider")
if (-not $env:ETL_TEST_PG) { $pytest += "--ignore=engine/tests/test_postgres.py" }
if ($SkipSlow) { $pytest += @("-m", "not slow") }
& $python @pytest
Check "pytest"

Step "Catalogue à jour"
& $python -m etl_engine.cli modifiers doc --out "$env:TEMP\catalogue.md"
Check "etl modifiers doc"
if ((Get-Content docs\catalogue.md -Raw) -ne (Get-Content "$env:TEMP\catalogue.md" -Raw)) { throw "docs/catalogue.md n'est pas à jour : etl modifiers doc" }
& $python -m etl_engine.cli modifiers doc --lang en --out "$env:TEMP\catalogue.en.md"
Check "etl modifiers doc --lang en"
if ((Get-Content docs\en\catalogue.md -Raw) -ne (Get-Content "$env:TEMP\catalogue.en.md" -Raw)) { throw "docs/en/catalogue.md n'est pas à jour : etl modifiers doc --lang en --out docs/en/catalogue.md" }

Step "Traductions (aucun texte sans traduction)"
& $python -m etl_engine.cli i18n check --lang en
Check "etl i18n check"

Step "Frontend (tsc, vitest, build)"
Push-Location frontend
npm ci --no-audit --no-fund
Check "npm ci (frontend)"
npx tsc --noEmit
Check "tsc"
npx vitest run
Check "vitest"
npx vite build
Check "vite build"
Pop-Location

if (-not $SkipE2E) {
  Step "Tests de bout en bout (Playwright) : flux dans la base"
  Push-Location e2e
  npm ci --no-audit --no-fund
  Check "npm ci (e2e)"
  Remove-Item Env:E2E_ROOT -ErrorAction SilentlyContinue
  $env:E2E_MODE = "db"
  npx playwright test
  Check "playwright (db)"

  Step "Tests de bout en bout (Playwright) : flux en fichiers"
  Remove-Item Env:E2E_ROOT -ErrorAction SilentlyContinue
  $env:E2E_MODE = "files"
  npx playwright test
  Check "playwright (files)"
  Remove-Item Env:E2E_MODE, Env:E2E_ROOT -ErrorAction SilentlyContinue
  Pop-Location
}

if ($Desktop) {
  Step "Application Electron (tests de fumée)"
  Push-Location desktop
  npm ci --no-audit --no-fund
  Check "npm ci (desktop)"
  if (-not (Test-Path "node_modules\electron\dist")) { node node_modules\electron\install.js; Check "téléchargement d'Electron" }
  npx tsc
  Check "tsc (desktop)"
  npx playwright test
  Check "playwright (desktop)"
  Pop-Location
}

Write-Host "`nCI OK" -ForegroundColor Green
