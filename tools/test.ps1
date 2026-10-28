$ErrorActionPreference = 'Stop'
$mixRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $mixRoot
$mixRuntime = Get-Content -Raw -LiteralPath (Join-Path $mixRoot '.runtime\runtime.json') | ConvertFrom-Json
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH = Join-Path $mixRoot '.runtime\packages'
& $mixRuntime.python -m unittest discover -s tests -v
exit $LASTEXITCODE
