param(
    [Parameter(Mandatory = $true)][string]$PythonExe,
    [string]$DataRoot = '',
    [switch]$SkipDownloads
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $DataRoot) { $DataRoot = Join-Path $repoRoot '.data/ai-music' }
$DataRoot = [IO.Path]::GetFullPath($DataRoot)
$sourceCommit = 'ca1e85fe9430179831e6bc6be790c332190a3866'
$engineRoot = Join-Path $DataRoot 'engine'
$toolingRoot = Join-Path $DataRoot 'tooling'
New-Item -ItemType Directory -Path $DataRoot -Force | Out-Null
function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed with exit code ${LASTEXITCODE}: $Executable" }
}
$version = & $PythonExe -c 'import sys; print(".".join(map(str, sys.version_info[:3])))'
if ($LASTEXITCODE -ne 0 -or $version -notmatch '^3\.(11|12)\.') {
    throw 'ACE-Step requires a stable Python 3.11 or 3.12 executable.'
}
if (-not (Test-Path -LiteralPath (Join-Path $toolingRoot 'Scripts/python.exe'))) {
    Invoke-Checked $PythonExe @('-m', 'venv', $toolingRoot)
}
$toolingPython = Join-Path $toolingRoot 'Scripts/python.exe'
$uvExe = Join-Path $toolingRoot 'Scripts/uv.exe'
if (-not (Test-Path -LiteralPath $uvExe)) {
    Invoke-Checked $toolingPython @('-m', 'pip', 'install', 'uv==0.13.0')
}
if (-not (Test-Path -LiteralPath $engineRoot)) {
    Invoke-Checked 'git' @('clone', '--filter=blob:none', '--no-checkout', 'https://github.com/ace-step/ACE-Step-1.5.git', $engineRoot)
    Invoke-Checked 'git' @('-C', $engineRoot, 'checkout', '--detach', $sourceCommit)
}
$actualCommit = & git -C $engineRoot rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $actualCommit -ne $sourceCommit) {
    throw "Existing engine checkout must be at $sourceCommit. It was not reset."
}
$env:UV_CACHE_DIR = Join-Path $DataRoot 'cache/uv'
$env:HF_HOME = Join-Path $DataRoot 'cache/huggingface'
$env:HF_HUB_DISABLE_XET = '1'
Push-Location $engineRoot
$previousProjectEnvironment = [Environment]::GetEnvironmentVariable('UV_PROJECT_ENVIRONMENT', 'Process')
try {
    $env:UV_PROJECT_ENVIRONMENT = Join-Path $engineRoot '.venv'
    # Preserve the upstream lock for all other dependencies, using CPU variants
    # of the same PyTorch release instead of downloading unused CUDA binaries.
    Invoke-Checked $uvExe @('sync', '--frozen', '--python', $PythonExe,
        '--no-install-package', 'torch', '--no-install-package', 'torchvision',
        '--no-install-package', 'torchaudio')
    $enginePython = Join-Path $engineRoot '.venv/Scripts/python.exe'
    $cpuWheelArguments = @('pip', 'install', '--python', $enginePython,
        '--index', 'https://download.pytorch.org/whl/cpu',
        'torch==2.7.1+cpu', 'torchvision==0.22.1+cpu', 'torchaudio==2.7.1+cpu')
    $cpuWheelDirectory = Join-Path $DataRoot 'cpu-wheels'
    if (Test-Path -LiteralPath $cpuWheelDirectory) {
        $cpuWheelArguments += @('--find-links', $cpuWheelDirectory)
    }
    Invoke-Checked $uvExe $cpuWheelArguments
    Invoke-Checked $uvExe @('pip', 'install', '--python', $enginePython, 'psutil==7.2.2')
    $checkpoints = Join-Path $DataRoot 'checkpoints'
    New-Item -ItemType Directory -Path $checkpoints -Force | Out-Null
    $engineCheckpoints = Join-Path $engineRoot 'checkpoints'
    if (-not (Test-Path -LiteralPath $engineCheckpoints)) {
        New-Item -ItemType Junction -Path $engineCheckpoints -Target $checkpoints | Out-Null
    } else {
        $item = Get-Item -LiteralPath $engineCheckpoints
        if ($item.LinkType -ne 'Junction' -or [IO.Path]::GetFullPath([string]$item.Target) -ne $checkpoints) {
            throw 'Engine checkpoints directory must be a junction to the selected data directory. No files were moved.'
        }
    }
    $checkpointManifest = Join-Path $DataRoot 'checkpoint-manifest.json'
    if (-not (Test-Path -LiteralPath $checkpointManifest)) {
        $model = Invoke-RestMethod 'https://huggingface.co/api/models/ACE-Step/Ace-Step1.5?blobs=true'
        $selected = @($model.siblings | Where-Object { $_.rfilename -match '^(vae|Qwen3-Embedding-0\.6B|acestep-v15-turbo)/' } |
            ForEach-Object { @{ path = $_.rfilename; size = $_.size } })
        @{ repo_id = 'ACE-Step/Ace-Step1.5'; revision = $model.sha; files = $selected } |
            ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $checkpointManifest -Encoding utf8
    }
    if (-not $SkipDownloads) {
        $download = @'
import json, sys
from pathlib import Path
from huggingface_hub import snapshot_download
root = Path(sys.argv[1])
manifest = json.loads((root / "checkpoint-manifest.json").read_text(encoding="utf-8-sig"))
print("Downloading pinned turbo, VAE and text encoder components only.", flush=True)
snapshot_download(repo_id=manifest["repo_id"], revision=manifest["revision"],
    allow_patterns=[entry["path"] for entry in manifest["files"]],
    local_dir=root / "checkpoints", max_workers=3)
'@
        Invoke-Checked $enginePython @('-c', $download, $DataRoot)
    }
    $packages = & $uvExe pip freeze --python $enginePython
    if ($LASTEXITCODE -ne 0) { throw 'Could not record runtime package versions.' }
    $modelManifest = Get-Content -LiteralPath $checkpointManifest -Raw | ConvertFrom-Json
    $manifest = @{ source_commit = $sourceCommit; python_version = $version;
        packages = @($packages); checkpoint_revisions = @{ 'ACE-Step/Ace-Step1.5' = $modelManifest.revision };
        device = 'cpu'; model = 'acestep-v15-turbo'; base_url = $null; pid = $null;
        adaptations = @('PyTorch 2.7.1 CPU wheels replace upstream CUDA wheel variants; upstream lock retained for other dependencies.',
            'ai_music_engine.py removes the unused 1.7B planner from vendor readiness checks only when planner initialization is explicitly disabled.',
            'ai_music_engine.py configures the engine temp root before vendor imports cache Python temporary paths.') }
    $manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $DataRoot 'runtime.json') -Encoding utf8
    Invoke-Checked $enginePython @('-c', 'import torch; print("PyTorch:",torch.__version__,"CUDA available:",torch.cuda.is_available())')
    Write-Output "Isolated CPU runtime prepared at $DataRoot"
} finally {
    [Environment]::SetEnvironmentVariable('UV_PROJECT_ENVIRONMENT', $previousProjectEnvironment, 'Process')
    Pop-Location
}
