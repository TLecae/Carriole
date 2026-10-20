# Arrête le moteur d'une installation avant une mise à jour ou une désinstallation (sinon ses fichiers sont verrouillés).
# D'abord proprement (les runs en cours sont interrompus et reprenables), puis en forçant ce qui resterait.
param([Parameter(Mandatory = $true)][string]$Resources)

$etl = Join-Path $Resources "bin\etl.cmd"
if (Test-Path $etl) { try { & $etl stop --timeout 60 | Out-Null } catch { } }

$runtime = Join-Path $Resources "runtime"
Get-Process -ErrorAction SilentlyContinue | Where-Object {
  $_.Path -and $_.Path.StartsWith($runtime, [StringComparison]::OrdinalIgnoreCase)
} | Stop-Process -Force -ErrorAction SilentlyContinue
