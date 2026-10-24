param(
    [Parameter(Mandatory=$true)][string]$RunId,
    [ValidateSet('status','advance','pause','stop','manifest')][string]$Action = 'status'
)
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'healthcheck.ps1')
if (-not $env:STUDIO_PYTHON) { throw 'Set STUDIO_PYTHON' }
$repoRoot = Split-Path $PSScriptRoot -Parent
Push-Location $repoRoot
try {
    & $env:STUDIO_PYTHON -X utf8 -m app.multi_ai_v2 $Action --run-id $RunId
    exit $LASTEXITCODE
} finally { Pop-Location }
