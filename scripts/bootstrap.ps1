# Read-only bootstrap. Installation remains an explicit action in docs/INSTALL.md.
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'healthcheck.ps1')
exit $LASTEXITCODE
