param([switch]$RequireProduction)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$pythonPath = $env:STUDIO_PYTHON
if (-not $pythonPath) { $pythonPath = Join-Path $repoRoot '.venv-core/Scripts/python.exe' }
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) { throw 'Set STUDIO_PYTHON to the existing studio Python executable' }
if (-not $env:LOCAL_RUN_ROOT) { throw 'Set LOCAL_RUN_ROOT outside sync folders' }
Push-Location $repoRoot
try {
    & $pythonPath -X utf8 scripts/healthcheck_multi_ai_v2.py
    if ($LASTEXITCODE -ne 0) { throw 'Healthcheck failed' }
    if ($RequireProduction) {
        & $pythonPath -m app.production_profile --check-runtime
        if ($LASTEXITCODE -ne 0) { throw 'Production profile/runtime consistency failed' }
        throw 'Production v2 is blocked: Story/Visual/runner/Drive integration is not verified'
    }
} finally { Pop-Location }
