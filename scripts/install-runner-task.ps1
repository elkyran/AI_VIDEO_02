param([Parameter(Mandatory=$true)][string]$RunnerDirectory, [switch]$Install)
$ErrorActionPreference = 'Stop'
$runnerPath = (Resolve-Path -LiteralPath $RunnerDirectory).Path
$runScript = Join-Path $runnerPath 'run.cmd'
if (-not (Test-Path -LiteralPath $runScript -PathType Leaf)) { throw 'Use an existing official GitHub runner directory' }
if (-not $Install) {
    Write-Output 'Prepared only: register the private-repo runner with label ai-video-02 first. Use -Install to create a current-user logon task.'
    return
}
if (Get-ScheduledTask -TaskName 'AI_VIDEO_02_Runner' -ErrorAction SilentlyContinue) { throw 'Existing task preserved; inspect it before updating' }
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $env:ComSpec -Argument ('/d /c ""' + $runScript + '""') -WorkingDirectory $runnerPath
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable
Register-ScheduledTask -TaskName 'AI_VIDEO_02_Runner' -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
