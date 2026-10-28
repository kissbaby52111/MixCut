param([switch]$WithCollector)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONIOENCODING = 'utf-8'
$mixRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $mixRoot
$mixPython = $null
$mixRuntimePath = Join-Path $mixRoot '.runtime\runtime.json'
if (Test-Path -LiteralPath $mixRuntimePath) {
    $mixRuntime = Get-Content -Raw -LiteralPath $mixRuntimePath | ConvertFrom-Json
    if ($mixRuntime.python -and (Test-Path -LiteralPath $mixRuntime.python)) { $mixPython = $mixRuntime.python }
}
if (-not $mixPython) {
    $mixBundled = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $mixBundled) { $mixPython = $mixBundled }
}
if (-not $mixPython) {
    $mixCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($mixCommand) { $mixPython = $mixCommand.Source }
}
if (-not $mixPython) { throw 'Python 3.11+ was not found. Install Python and run setup.bat again.' }
if (-not (Test-Path -LiteralPath (Join-Path $mixRoot 'vendor\pyJianYingDraft\pyJianYingDraft\__init__.py'))) {
    & $mixPython tools/bootstrap.py --sources-only
    if ($LASTEXITCODE -ne 0) { throw 'Source download failed. Existing archives were kept.' }
}
$mixRuntimeArgs = @('tools/fetch_runtime.py')
if ($WithCollector) { $mixRuntimeArgs += '--with-collector' }
& $mixPython @mixRuntimeArgs
if ($LASTEXITCODE -ne 0) { throw 'Runtime download failed. Completed parts were kept; run setup again.' }
& $mixPython -m pip install --disable-pip-version-check --target .runtime/packages --index-url https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
& $mixPython -m pip install --disable-pip-version-check --no-deps --target .runtime/packages --index-url https://pypi.tuna.tsinghua.edu.cn/simple -r requirements-draft.txt
if ($LASTEXITCODE -ne 0) { Write-Warning 'Optional draft dependencies failed; direct MP4 rendering is still available.' }
foreach ($mixSeed in @(@('config.example.json', 'config.json'), @('.env.example', '.env'))) {
    $mixTarget = Join-Path $mixRoot $mixSeed[1]
    if (-not (Test-Path -LiteralPath $mixTarget)) {
        Copy-Item -LiteralPath (Join-Path $mixRoot $mixSeed[0]) -Destination $mixTarget
    }
}
& $mixPython run.py doctor
exit $LASTEXITCODE
