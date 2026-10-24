param([switch]$RemoveLogonTask)
$ErrorActionPreference = 'Stop'
if (-not $RemoveLogonTask) { Write-Output 'No change. Use -RemoveLogonTask to remove only AI_VIDEO_02_Runner logon task.'; return }
Unregister-ScheduledTask -TaskName 'AI_VIDEO_02_Runner' -Confirm:$false
# Does not unregister GitHub, delete runner data, remove credentials or stop production.
