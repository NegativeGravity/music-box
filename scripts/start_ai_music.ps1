param([int]$Port = 8001, [string]$DataRoot = '', [switch]$NoInit)
$ErrorActionPreference = 'Stop'
if ($Port -lt 1 -or $Port -gt 65535) { throw 'Port must be between 1 and 65535.' }
$listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
if ($listener) { throw "Port $Port is already occupied. No existing process was stopped." }
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $DataRoot) { $DataRoot = Join-Path $repoRoot '.data/ai-music' }
$DataRoot = [IO.Path]::GetFullPath($DataRoot)
$engineRoot = Join-Path $DataRoot 'engine'
$pythonExe = Join-Path $engineRoot '.venv/Scripts/python.exe'
$runnerScript = Join-Path $PSScriptRoot 'ai_music_engine.py'
$manifestPath = Join-Path $DataRoot 'runtime.json'
if (-not (Test-Path -LiteralPath $pythonExe) -or -not (Test-Path -LiteralPath $manifestPath)) {
    throw 'Run setup_ai_music.ps1 before starting the engine.'
}
$settings = @{
    ACESTEP_DEVICE = 'cpu'; ACESTEP_USE_FLASH_ATTENTION = 'false';
    ACESTEP_COMPILE_MODEL = 'false'; ACESTEP_INIT_LLM = 'false';
    ACESTEP_LM_BACKEND = 'pt'; ACESTEP_OFFLOAD_TO_CPU = 'false';
    ACESTEP_QUEUE_WORKERS = '1'; ACESTEP_API_HOST = '127.0.0.1';
    ACESTEP_API_PORT = [string]$Port; ACESTEP_CHECKPOINTS_DIR = (Join-Path $DataRoot 'checkpoints');
    ACESTEP_TMPDIR = (Join-Path $DataRoot 'cache/tmp');
    HF_HOME = (Join-Path $DataRoot 'cache/huggingface'); HF_HUB_DISABLE_XET = '1';
    HF_HUB_OFFLINE = '1'; TRANSFORMERS_OFFLINE = '1';
    OMP_NUM_THREADS = '4'; MKL_NUM_THREADS = '4'; PYTHONUNBUFFERED = '1';
    ACESTEP_NO_INIT = $(if ($NoInit) { 'true' } else { 'false' })
}
$previous = @{}
$logDir = Join-Path $DataRoot 'logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$stdout = Join-Path $logDir "engine-$stamp.stdout.log"
$stderr = Join-Path $logDir "engine-$stamp.stderr.log"
try {
    foreach ($name in $settings.Keys) {
        $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
        [Environment]::SetEnvironmentVariable($name, $settings[$name], 'Process')
    }
    $process = Start-Process -FilePath $pythonExe -ArgumentList @(('"{0}"' -f $runnerScript), '--engine-root', ('"{0}"' -f $engineRoot), '--host', '127.0.0.1', '--port', [string]$Port) `
        -WorkingDirectory $engineRoot -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $manifest.pid = $process.Id
    $startedAt = $process.StartTime.ToUniversalTime().ToString('o')
    $manifest | Add-Member -NotePropertyName started_at -NotePropertyValue $startedAt -Force
    $manifest.base_url = "http://127.0.0.1:$Port"
    $manifest | Add-Member -NotePropertyName stdout_log -NotePropertyValue $stdout -Force
    $manifest | Add-Member -NotePropertyName stderr_log -NotePropertyValue $stderr -Force
    $manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $manifestPath -Encoding utf8
    @{ pid = $process.Id; started_at = $startedAt; executable = $pythonExe; base_url = $manifest.base_url; stdout_log = $stdout; stderr_log = $stderr } | ConvertTo-Json
} finally {
    foreach ($name in $previous.Keys) { [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process') }
}
