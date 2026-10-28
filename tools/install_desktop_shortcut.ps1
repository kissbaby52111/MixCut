param([string]$DesktopPath = '')
$ErrorActionPreference = 'Stop'
$mixRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).ProviderPath
$mixRuntime = Get-Content -Raw -LiteralPath (Join-Path $mixRoot '.runtime\runtime.json') | ConvertFrom-Json
$mixPythonw = Join-Path (Split-Path -Parent ([string]$mixRuntime.python)) 'pythonw.exe'
$mixLauncher = Join-Path $mixRoot 'tools\desktop_launcher.pyw'
$mixIcon = Join-Path $mixRoot 'assets\icons\mixcut.ico'
foreach ($mixRequired in @($mixPythonw, $mixLauncher, $mixIcon)) {
    if (-not (Test-Path -LiteralPath $mixRequired -PathType Leaf)) { throw "Missing launch component: $mixRequired" }
}
if (-not $DesktopPath) { $DesktopPath = [Environment]::GetFolderPath('Desktop') }
if (-not (Test-Path -LiteralPath $DesktopPath -PathType Container)) { throw "Desktop folder not found: $DesktopPath" }
$mixShortcutPath = Join-Path $DesktopPath 'MixCut.lnk'
if (Test-Path -LiteralPath $mixShortcutPath) {
    $mixBackupDir = Join-Path $mixRoot '.runtime\shortcut_backups'
    New-Item -ItemType Directory -Path $mixBackupDir -Force | Out-Null
    Copy-Item -LiteralPath $mixShortcutPath -Destination (Join-Path $mixBackupDir ('MixCut_' + (Get-Date -Format 'yyyyMMdd_HHmmss_fff') + '.lnk'))
}
$mixShell = New-Object -ComObject WScript.Shell
$mixShortcut = $mixShell.CreateShortcut($mixShortcutPath)
$mixShortcut.TargetPath = $mixPythonw
$mixShortcut.Arguments = '"' + $mixLauncher + '"'
$mixShortcut.WorkingDirectory = $mixRoot
$mixShortcut.IconLocation = $mixIcon + ',0'
$mixShortcut.Description = 'MixCut - Local video mixing tool'
$mixShortcut.WindowStyle = 1
$mixShortcut.Save()
$mixCheck = $mixShell.CreateShortcut($mixShortcutPath)
if ($mixCheck.TargetPath -ne $mixPythonw -or $mixCheck.Arguments -ne ('"' + $mixLauncher + '"') -or $mixCheck.IconLocation -ne ($mixIcon + ',0')) {
    throw 'Shortcut verification failed.'
}
Write-Output $mixShortcutPath
