param([string]$ConfigPath = '')
$ErrorActionPreference = 'Stop'
$mixRoot = Split-Path -Parent $PSScriptRoot
$mixRuntimeFile = Join-Path $mixRoot '.runtime\runtime.json'
if (-not (Test-Path -LiteralPath $mixRuntimeFile)) { throw 'Run setup.bat first.' }
$mixRuntime = Get-Content -LiteralPath $mixRuntimeFile -Raw | ConvertFrom-Json
$mixPython = [string]$mixRuntime.python
$mixPythonWindowed = Join-Path (Split-Path -Parent $mixPython) 'pythonw.exe'
if (Test-Path -LiteralPath $mixPythonWindowed) { $mixPython = $mixPythonWindowed }
$mixScript = Join-Path $mixRoot 'run.py'
if (-not $ConfigPath) {
    $mixActiveFile = Join-Path $mixRoot '.runtime\active_gui_config.json'
    if (Test-Path -LiteralPath $mixActiveFile) {
        $mixActive = Get-Content -Raw -LiteralPath $mixActiveFile | ConvertFrom-Json
        $ConfigPath = [string]$mixActive.config_path
        if (-not $ConfigPath) { throw 'Active project configuration is missing. Check .runtime\active_gui_config.json.' }
    } else {
        $ConfigPath = Join-Path $mixRoot 'config.json'
    }
}
$mixConfigFile = (Resolve-Path -LiteralPath $ConfigPath -ErrorAction Stop).ProviderPath
$mixArguments = @(('"' + $mixScript + '"'), '--config', ('"' + $mixConfigFile + '"'), 'gui')
Start-Process -FilePath $mixPython -ArgumentList $mixArguments -WorkingDirectory $mixRoot -WindowStyle Normal
