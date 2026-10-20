<#
.SYNOPSIS
Diagnose the local MVP; install/download only with explicit switches.
.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup.ps1
.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup.ps1 -Install -DownloadVoice -Voice ruslan -DownloadShotcut
#>
[CmdletBinding()]
param(
    [switch]$Install,
    [switch]$DownloadVoice,
    [ValidateSet('ruslan', 'denis')][string]$Voice = 'ruslan',
    [switch]$DownloadShotcut,
    [switch]$DownloadAsr,
    [switch]$WriteRuntime,
    [string]$Workspace = 'workspace',
    [string]$Python
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$corePython = Join-Path $repoRoot '.venv-core\Scripts\python.exe'
$ttsPython = Join-Path $repoRoot '.venv-tts\Scripts\python.exe'
$downloadRoot = Join-Path $repoRoot 'downloads'
$voiceRevision = 'c10ece1aade47bb51c153c893d14e5bf8e5b7117'
$asrRevision = '08e178d48790749d25932bbc082711ddcfdfbc4f'
$shotcutVersion = '26.9.27'
$downloadRecords = [Collections.Generic.List[object]]::new()

function Resolve-RepositoryPath([string]$Path) {
    $resolved = [IO.Path]::GetFullPath((Join-Path $repoRoot $Path))
    $prefix = $repoRoot.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $resolved.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Path must stay inside repository: $Path"
    }
    return $resolved
}

function Invoke-Checked([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $Executable" }
}

function Find-Python {
    if ($Python) {
        if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw '-Python must name an existing python.exe.' }
        return [IO.Path]::GetFullPath($Python)
    }
    foreach ($existing in @($corePython, $ttsPython)) {
        if (Test-Path -LiteralPath $existing -PathType Leaf) { return $existing }
    }
    foreach ($commandName in @('py', 'python', 'python3')) {
        $command = Get-Command $commandName -ErrorAction SilentlyContinue
        if ($null -eq $command -or $command.Source -like '*\WindowsApps\*') { continue }
        $prefixArguments = @()
        if ($commandName -eq 'py') { $prefixArguments = @('-3') }
        $candidate = & $command.Source @prefixArguments -c 'import sys; print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $candidate -and (Test-Path -LiteralPath ([string]$candidate) -PathType Leaf)) {
            return [string]$candidate
        }
    }
    return $null
}

function Save-VerifiedDownload([string]$Url, [string]$Destination, [string]$Sha256) {
    if ($Url -notmatch '^https://(github\.com/mltframework/shotcut/|huggingface\.co/rhasspy/piper-voices/)') {
        throw "Download URL is outside the fixed official sources: $Url"
    }
    if (Test-Path -LiteralPath $Destination -PathType Leaf) {
        $actual = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $Sha256) { throw "Existing file has an unexpected SHA256; not overwritten: $Destination" }
    } else {
        New-Item -ItemType Directory -Path (Split-Path -Parent $Destination) -Force | Out-Null
        $temporary = $Destination + '.download'
        Write-Host "Downloading explicitly requested file: $Url"
        Invoke-WebRequest -Uri $Url -OutFile $temporary -UseBasicParsing
        $actual = (Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $Sha256) { throw "SHA256 mismatch; incomplete file retained for inspection: $temporary" }
        Move-Item -LiteralPath $temporary -Destination $Destination
    }
    [IO.File]::WriteAllText($Destination + '.sha256', "$Sha256  $([IO.Path]::GetFileName($Destination))`n", [Text.UTF8Encoding]::new($false))
    $downloadRecords.Add([ordered]@{ url = $Url; path = $Destination.Substring($repoRoot.Length + 1); sha256 = $Sha256; verified = $true })
}

$selectedPython = Find-Python
$pythonVersion = $null
if ($selectedPython) {
    $versionText = & $selectedPython -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])'
    if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect Python.' }
    $pythonVersion = [version]([string]$versionText)
}
$mutating = $Install -or $DownloadVoice -or $DownloadShotcut -or $DownloadAsr -or $WriteRuntime
if ($mutating -and [Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'This setup script targets Windows.' }
if ($Install -or $DownloadAsr) {
    if (-not $selectedPython -or $pythonVersion -lt [version]'3.11') { throw 'Install Python 3.11+ first, or supply -Python PATH. Python 3.11 is the tested version.' }
}

Push-Location -LiteralPath $repoRoot
try {
    if ($Install) {
        foreach ($environmentName in @('.venv-core', '.venv-tts')) {
            $environmentPython = Join-Path $repoRoot "$environmentName\Scripts\python.exe"
            if (-not (Test-Path -LiteralPath $environmentPython -PathType Leaf)) {
                Invoke-Checked $selectedPython @('-m', 'venv', (Join-Path $repoRoot $environmentName))
            }
        }
        Write-Host 'Installing declared Python dependencies from PyPI. This is an explicit -Install operation.'
        Invoke-Checked $corePython @('-m', 'pip', '--isolated', 'install', '--index-url', 'https://pypi.org/simple', '-e', '.[mcp]')
        Invoke-Checked $ttsPython @('-m', 'pip', '--isolated', 'install', '--index-url', 'https://pypi.org/simple', '-r', (Join-Path $repoRoot 'requirements-tts.txt'))
        New-Item -ItemType Directory -Path $downloadRoot -Force | Out-Null
        $coreFreeze = & $corePython -m pip freeze
        if ($LASTEXITCODE -ne 0) { throw 'Unable to record core dependencies.' }
        $ttsFreeze = & $ttsPython -m pip freeze
        if ($LASTEXITCODE -ne 0) { throw 'Unable to record TTS dependencies.' }
        [IO.File]::WriteAllLines((Join-Path $downloadRoot 'core-freeze.txt'), [string[]]$coreFreeze, [Text.UTF8Encoding]::new($false))
        [IO.File]::WriteAllLines((Join-Path $downloadRoot 'tts-freeze.txt'), [string[]]$ttsFreeze, [Text.UTF8Encoding]::new($false))
    }

    if ($DownloadVoice) {
        $hashes = @{
            denis = @('15fab56e11a097858ee115545d0f697fc2a316c41a291a5362349fb870411b0a', '831c860dac0b5073eaa81610a0a638ec23d90a6cf8e5f871b4485c2cec3767c8', '8b5d685dd80f8ad3f8dbbe1c56b16bb0809f00c144af3642dbcf3b707eb89c12')
            ruslan = @('72a5f88e0b20928064eb45d88e1daa21f8af62d18613580d32cbb4aed48dcf7f', '706a4fb17bc304abd07809b552deae615e64dcbffbfbd09854ba37ca59e88117', '28f5c0381c1234eb17bfd77e77f893b008948c29bc351fb811abd2d6a69e7662')
        }
        $voiceName = "ru_RU-$Voice-medium"
        $voiceBase = "https://huggingface.co/rhasspy/piper-voices/resolve/$voiceRevision/ru/ru_RU/$Voice/medium"
        $modelDirectory = Resolve-RepositoryPath 'models\piper'
        Save-VerifiedDownload "$voiceBase/$voiceName.onnx" (Join-Path $modelDirectory "$voiceName.onnx") $hashes[$Voice][0]
        Save-VerifiedDownload "$voiceBase/$voiceName.onnx.json" (Join-Path $modelDirectory "$voiceName.onnx.json") $hashes[$Voice][1]
        Save-VerifiedDownload "$voiceBase/MODEL_CARD" (Join-Path $modelDirectory "$voiceName.MODEL_CARD") $hashes[$Voice][2]
    }

    if ($DownloadShotcut) {
        $zipName = "shotcut-win64-$shotcutVersion.zip"
        $zipPath = Resolve-RepositoryPath "tools\$zipName"
        Save-VerifiedDownload "https://github.com/mltframework/shotcut/releases/download/v$shotcutVersion/$zipName" $zipPath '1f8c50ce7b125a2f0dcb60777b358e86baf03b8515982612b6adae10a83d93a5'
        $shotcutDirectory = Resolve-RepositoryPath 'tools\Shotcut'
        if (-not (Test-Path -LiteralPath $shotcutDirectory)) {
            Expand-Archive -LiteralPath $zipPath -DestinationPath (Resolve-RepositoryPath 'tools')
        } else {
            Write-Host 'tools/Shotcut already exists; it has not been overwritten.'
        }
    }

    if ($DownloadAsr) {
        if (-not (Test-Path -LiteralPath $ttsPython -PathType Leaf)) { throw 'Create .venv-tts with -Install before -DownloadAsr.' }
        Write-Host 'Downloading the explicitly requested public Systran faster-whisper-medium model (approximately 1.5 GB).'
        $asrDirectory = Resolve-RepositoryPath 'models\asr\medium'
        $downloadCode = @'
import hashlib, json, os, pathlib, sys
os.environ.pop('HF_HUB_OFFLINE', None)
os.environ['HF_HUB_DISABLE_IMPLICIT_TOKEN'] = '1'
from huggingface_hub import snapshot_download
target = pathlib.Path(sys.argv[1])
snapshot_download('Systran/faster-whisper-medium', revision=sys.argv[2], local_dir=str(target), allow_patterns=['config.json', 'model.bin', 'preprocessor_config.json', 'tokenizer.json', 'vocabulary.json'], token=False)
records=[]
for path in sorted(target.glob('*')):
    if path.is_file() and path.name != 'download_checksums.json':
        digest=hashlib.sha256()
        with path.open('rb') as stream:
            for part in iter(lambda: stream.read(1024*1024), b''): digest.update(part)
        records.append({'file':path.name,'sha256':digest.hexdigest()})
(target/'download_checksums.json').write_text(json.dumps({'repository':'https://huggingface.co/Systran/faster-whisper-medium','revision':sys.argv[2],'hashes':'recorded_after_download','files':records},indent=2),encoding='utf-8')
'@
        Invoke-Checked $ttsPython @('-c', $downloadCode, $asrDirectory, $asrRevision)
    }

    if ($downloadRecords.Count -gt 0) {
        New-Item -ItemType Directory -Path $downloadRoot -Force | Out-Null
        $recordPath = Join-Path $downloadRoot ('verified-downloads-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss') + '.json')
        [IO.File]::WriteAllText($recordPath, ($downloadRecords.ToArray() | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
    }

    if ($WriteRuntime) {
        $workspaceDirectory = Resolve-RepositoryPath $Workspace
        $runtimePath = Join-Path $workspaceDirectory 'runtime.json'
        if (Test-Path -LiteralPath $runtimePath) { throw 'runtime.json exists; review and edit it manually. Setup will not replace it.' }
        $runtime = [ordered]@{
            tts_python = $ttsPython
            tts_model = Join-Path $repoRoot "models\piper\ru_RU-$Voice-medium.onnx"
            tts_length_scale = 1.08
            ffmpeg_path = Join-Path $repoRoot 'tools\Shotcut\ffmpeg.exe'
            ffprobe_path = Join-Path $repoRoot 'tools\Shotcut\ffprobe.exe'
            melt_path = Join-Path $repoRoot 'tools\Shotcut\melt.exe'
        }
        foreach ($key in @('tts_python','tts_model','ffmpeg_path','ffprobe_path','melt_path')) {
            if (-not (Test-Path -LiteralPath $runtime[$key] -PathType Leaf)) { throw "Missing runtime file $key. Complete installation/downloads before -WriteRuntime." }
        }
        $localAsr = Join-Path $repoRoot 'models\asr\medium'
        if (Test-Path -LiteralPath (Join-Path $localAsr 'model.bin') -PathType Leaf) {
            $runtime.asr_python = $ttsPython
            $runtime.asr_model = $localAsr
        }
        New-Item -ItemType Directory -Path $workspaceDirectory -Force | Out-Null
        [IO.File]::WriteAllText($runtimePath, ($runtime | ConvertTo-Json -Depth 4), [Text.UTF8Encoding]::new($false))
        Write-Host "Wrote machine-local runtime configuration: $runtimePath"
    }

    $diagnostic = [ordered]@{
        mode = $(if ($mutating) { 'explicit_setup_then_diagnose' } else { 'diagnose_only_no_downloads' })
        repository = $repoRoot
        python = $selectedPython
        python_version = $(if ($pythonVersion) { $pythonVersion.ToString() } else { $null })
        python_compatible = [bool]($pythonVersion -and $pythonVersion -ge [version]'3.11')
        core_environment = Test-Path -LiteralPath $corePython -PathType Leaf
        tts_environment = Test-Path -LiteralPath $ttsPython -PathType Leaf
        voice_model = Test-Path -LiteralPath (Join-Path $repoRoot "models\piper\ru_RU-$Voice-medium.onnx") -PathType Leaf
        shotcut_melt = Test-Path -LiteralPath (Join-Path $repoRoot 'tools\Shotcut\melt.exe') -PathType Leaf
        ffmpeg = Test-Path -LiteralPath (Join-Path $repoRoot 'tools\Shotcut\ffmpeg.exe') -PathType Leaf
        ffprobe = Test-Path -LiteralPath (Join-Path $repoRoot 'tools\Shotcut\ffprobe.exe') -PathType Leaf
        portable_asr_model = Test-Path -LiteralPath (Join-Path $repoRoot 'models\asr\medium\model.bin') -PathType Leaf
        clean_computer_validation = 'not_tested'
        paid_api_required = $false
    }
    $diagnostic | ConvertTo-Json -Depth 4
} finally {
    Pop-Location
}
