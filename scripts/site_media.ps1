<#
Vidéo et captures du site et du README : un flux construit, lancé puis inspecté dans la vraie interface (scénario
e2e\media\demo.media.ts), en anglais et en français.
Usage :  powershell -ExecutionPolicy Bypass -File scripts\site_media.ps1 [-Lang en,fr]
Données : les communes de la Haute-Garonne, avec leur population (API Géo d'Etalab, Licence Ouverte), servies sous
D:\donnees le temps de l'enregistrement (lecteur virtuel `subst`, retiré à la fin) : les chemins montrés restent neutres.
La lettre D: doit être libre.
Prérequis : uv, Node.js, Microsoft Edge, accès à internet (données, fond de carte, ffmpeg d'imageio-ffmpeg dans un
environnement uv temporaire). L'interface doit être construite (frontend\dist).
Résultat : site\media\demo-<langue>.mp4, site\img\demo-<langue>.webp (image d'attente de la vidéo),
site\img\{editeur,inspecteur,requete}-<langue>.webp.
#>
param(
  [string[]]$Lang = @("en", "fr")
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$python = Join-Path $root ".venv\Scripts\python.exe"
$work = Join-Path $env:TEMP "carriole-media"

function Step([string]$title) { Write-Host "`n== $title" -ForegroundColor Cyan }
function Check([string]$what) { if ($LASTEXITCODE -ne 0) { throw "$what a échoué (code $LASTEXITCODE)" } }

if (Test-Path "D:\") { throw "le lecteur D: existe : il doit être libre pour servir les données sous D:\donnees" }
if (-not (Test-Path (Join-Path $root "frontend\dist\index.html"))) { throw "interface non construite : cd frontend; npx vite build" }
if (Test-Path $work) { Remove-Item $work -Recurse -Force }
New-Item -ItemType Directory -Path $work | Out-Null

Step "Données"
& $python (Join-Path $PSScriptRoot "site_media_data.py") (Join-Path $work "demo\donnees\communes_31.parquet")
Check "données"

Step "Enregistrement"
subst D: (Join-Path $work "demo")
Check "subst"
try {
  Push-Location (Join-Path $root "e2e")
  foreach ($l in $Lang) {
    $env:MEDIA_LANG = $l
    $env:MEDIA_OUT = Join-Path $work "out-$l"
    npx playwright test -c media.config.ts
    Check "scénario ($l)"
  }
} finally {
  Pop-Location
  subst D: /D
  Remove-Item Env:\MEDIA_LANG, Env:\MEDIA_OUT -ErrorAction SilentlyContinue
}

Step "Conversion"
$ffmpeg = (uv run --no-project --with imageio-ffmpeg python -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())" | Select-Object -Last 1)
Check "ffmpeg"
New-Item -ItemType Directory -Force -Path (Join-Path $root "site\media") | Out-Null
foreach ($l in $Lang) {
  $out = Join-Path $work "out-$l"
  $video = Get-ChildItem (Join-Path $out "raw") -Recurse -Filter "video.webm" | Select-Object -First 1
  if (-not $video) { throw "vidéo introuvable ($l)" }
  # H.264, lue par tous les navigateurs ; démarrage avant la fin du téléchargement ; sans son
  & $ffmpeg -y -v error -i $video.FullName -c:v libx264 -preset slow -crf 30 -pix_fmt yuv420p -movflags +faststart -an (Join-Path $root "site\media\demo-$l.mp4")
  Check "vidéo ($l)"
  # image d'attente : le flux construit, avant son lancement (les captures montrent déjà l'inspecteur)
  & $ffmpeg -y -v error -ss 28 -i $video.FullName -frames:v 1 -c:v libwebp -quality 80 (Join-Path $root "site\img\demo-$l.webp")
  Check "image d'attente ($l)"
  foreach ($name in "editeur", "inspecteur", "requete") {
    & $ffmpeg -y -v error -i (Join-Path $out "$name-$l.png") -c:v libwebp -quality 82 (Join-Path $root "site\img\$name-$l.webp")
    Check "$name ($l)"
  }
}
Get-ChildItem (Join-Path $root "site\media"), (Join-Path $root "site\img") | Select-Object Name, @{n = "Ko"; e = { [math]::Round($_.Length / 1KB) } } | Format-Table -AutoSize
