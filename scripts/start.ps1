param([switch]$Dev, [switch]$WithAiMusic, [ValidateSet('yue2','ace-step')][string]$AiEngine = 'yue2', [string]$AiDataRoot = '', [string]$PythonExe = '',
      [string]$DataDir = '', [int]$WebPort = 3000, [int]$ApiPort = 8000, [int]$AiPort = 8001)
$ErrorActionPreference = 'Stop'
$musicRoot = Split-Path $PSScriptRoot -Parent
$musicPython = if ($PythonExe) { [IO.Path]::GetFullPath($PythonExe) } else { Join-Path $musicRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $musicPython)) { throw 'Run scripts\setup.ps1 first' }
$musicNode = (Get-Command node -ErrorAction Stop).Source
$musicState = if ($DataDir) { [IO.Path]::GetFullPath($DataDir) } else { Join-Path $musicRoot '.data' }
foreach ($musicPort in @($WebPort,$ApiPort,$AiPort)) { if ($musicPort -lt 1 -or $musicPort -gt 65535) { throw 'Ports must be between 1 and 65535.' } }
if ($WebPort -eq $ApiPort -or ($WithAiMusic -and ($AiPort -eq $WebPort -or $AiPort -eq $ApiPort))) { throw 'Service ports must be distinct.' }
New-Item -ItemType Directory -Path $musicState -Force | Out-Null
$env:PYTHONPATH = Join-Path $musicRoot 'backend'
$env:MUSICBOX_DATA_DIR = $musicState
$env:MUSICBOX_AI_ENGINE = $AiEngine
$env:NEXT_TELEMETRY_DISABLED = '1'
$env:API_ORIGIN = "http://127.0.0.1:$ApiPort"
$env:MUSICBOX_ORIGINS = "http://localhost:$WebPort,http://127.0.0.1:$WebPort,http://testserver"
$musicPorts = @($WebPort,$ApiPort)
if ($WithAiMusic -and $AiEngine -eq 'ace-step') { $musicPorts += $AiPort }
foreach ($musicPort in $musicPorts) {
    if (Get-NetTCPConnection -LocalPort $musicPort -State Listen -ErrorAction SilentlyContinue) { throw "Port $musicPort is occupied. Existing app may already be running." }
}
$musicRecords = @{root=$musicRoot}
function Register-MusicboxProcess($Name, $Process, $Executable, $Marker) {
    $musicRecords[$Name] = @{pid=$Process.Id;started_at=$Process.StartTime.ToUniversalTime().ToString('o');executable=$Executable;marker=$Marker}
    $musicRecords | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $musicState 'processes.json') -Encoding UTF8
}
try {
    if ($WithAiMusic) {
        if (-not $AiDataRoot) { $AiDataRoot = Join-Path $musicRoot '.data\ai-music' }
        $AiDataRoot = [IO.Path]::GetFullPath($AiDataRoot)
        $env:MUSICBOX_YUE2_ROOT = Join-Path $AiDataRoot 'yue2'
        $env:MUSICBOX_AI_TEMP_DIR = Join-Path $AiDataRoot 'cache/tmp'
        if ($AiEngine -eq 'yue2') {
            $musicManifestPath = Join-Path $env:MUSICBOX_YUE2_ROOT 'runtime.json'
            if (-not (Test-Path -LiteralPath $musicManifestPath) -or -not (Test-Path -LiteralPath (Join-Path $env:MUSICBOX_YUE2_ROOT 'runtime/audiocpp_cli.exe'))) { throw 'Run scripts/setup_yue2.ps1 before enabling YuE2.' }
            $musicManifest = Get-Content -LiteralPath $musicManifestPath -Raw | ConvertFrom-Json
            if (-not $musicManifest.verified -or $musicManifest.engine -ne 'yue2' -or $musicManifest.runtime_release -ne 'v0.9.1' -or $musicManifest.model_revision -ne 'cca74d8980c00f30ab8d52248a367c01ad633ec2') { throw 'YuE2 runtime manifest does not match the verified installation.' }
        } else {
            $env:MUSICBOX_AI_ENGINE_URL = "http://127.0.0.1:$AiPort"
            $musicEngine = & (Join-Path $PSScriptRoot 'start_ai_music.ps1') -Port $AiPort -DataRoot $AiDataRoot | ConvertFrom-Json
            $musicRecords.ai_engine = @{pid=$musicEngine.pid;started_at=$musicEngine.started_at;executable=$musicEngine.executable;marker=(Join-Path $PSScriptRoot 'ai_music_engine.py')}
            $musicRecords | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $musicState 'processes.json') -Encoding UTF8
        }
    }
    $musicApi = Start-Process -FilePath $musicPython -ArgumentList @('-m','waitress',"--listen=127.0.0.1:$ApiPort",'config.wsgi:application') -WorkingDirectory (Join-Path $musicRoot 'backend') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $musicState 'api.log') -RedirectStandardError (Join-Path $musicState 'api-error.log')
    Register-MusicboxProcess 'api' $musicApi $musicPython 'config.wsgi:application'
    $musicWorker = Start-Process -FilePath $musicPython -ArgumentList @('-m','app.worker','--stage','decode') -WorkingDirectory $musicRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $musicState 'worker.log') -RedirectStandardError (Join-Path $musicState 'worker-error.log')
    Register-MusicboxProcess 'worker' $musicWorker $musicPython 'app.worker'
    $musicMlWorker = Start-Process -FilePath $musicPython -ArgumentList @('-m','app.worker','--stage','clap') -WorkingDirectory $musicRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $musicState 'ml-worker.log') -RedirectStandardError (Join-Path $musicState 'ml-worker-error.log')
    Register-MusicboxProcess 'ml_worker' $musicMlWorker $musicPython 'app.worker'
    if ($WithAiMusic) {
        $musicGeneration = Start-Process -FilePath $musicPython -ArgumentList @('-m','app.generation_worker') -WorkingDirectory $musicRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $musicState 'generation-worker.log') -RedirectStandardError (Join-Path $musicState 'generation-worker-error.log')
        Register-MusicboxProcess 'generation_worker' $musicGeneration $musicPython 'app.generation_worker'
    }
    $musicMode = if ($Dev) { 'dev' } else { 'start' }
    $musicNext = Join-Path $musicRoot 'frontend\node_modules\next\dist\bin\next'
    $musicWeb = Start-Process -FilePath $musicNode -ArgumentList @("`"$musicNext`"",$musicMode,'--hostname','127.0.0.1','--port',[string]$WebPort) -WorkingDirectory (Join-Path $musicRoot 'frontend') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $musicState 'web.log') -RedirectStandardError (Join-Path $musicState 'web-error.log')
    Register-MusicboxProcess 'web' $musicWeb $musicNode $musicNext
} catch {
    if ($musicRecords.Count -gt 1) { & (Join-Path $PSScriptRoot 'stop.ps1') -DataDir $musicState }
    throw
}
Write-Host "Musicbox is starting at http://127.0.0.1:$WebPort. Logs: $musicState"
