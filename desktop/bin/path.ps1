# Ajoute ou retire un dossier du PATH de la MACHINE (appelé par l'installeur et le désinstalleur, qui ont les droits
# d'administrateur). L'ajout est idempotent, le retrait ne touche qu'à ce dossier. Une ancienne installation par
# utilisateur avait posé ce dossier dans le PATH de l'utilisateur : il est aussi retiré de là.
param(
  [Parameter(Mandatory = $true)][ValidateSet("add", "remove")][string]$Action,
  [Parameter(Mandatory = $true)][string]$Dir
)

$target = $Dir.TrimEnd("\")

function Update-Path([string]$scope, [bool]$add) {
  $current = [Environment]::GetEnvironmentVariable("Path", $scope)
  $parts = @($current -split ";" | Where-Object { $_ -and $_.TrimEnd("\") -ne $target })
  if ($add) { $parts += $target }
  # SetEnvironmentVariable prévient aussi les fenêtres ouvertes (WM_SETTINGCHANGE) : un nouveau terminal voit « etl ».
  [Environment]::SetEnvironmentVariable("Path", ($parts -join ";"), $scope)
}

try { Update-Path "Machine" ($Action -eq "add") } catch { }
try { Update-Path "User" $false } catch { }
