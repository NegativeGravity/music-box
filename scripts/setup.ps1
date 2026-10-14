param([string]$PythonPath, [switch]$WithClap, [switch]$NoDemo)
$ErrorActionPreference = 'Stop'
$musicRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $musicRoot
if (-not $PythonPath) {
    $musicBundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $musicBundledPython) { $PythonPath = $musicBundledPython }
    else { $PythonPath = (Get-Command python -ErrorAction Stop).Source }
}
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) { & $PythonPath -m venv .venv; if ($LASTEXITCODE) { throw 'Python environment creation failed' } }
$musicPython = Join-Path $musicRoot '.venv\Scripts\python.exe'
& $musicPython -m pip install -r backend/requirements.txt
if ($LASTEXITCODE) { throw 'API dependencies failed' }
pnpm --dir frontend install --frozen-lockfile
if ($LASTEXITCODE) { throw 'Web dependencies failed' }
$env:PYTHONPATH = Join-Path $musicRoot 'backend'
if (-not $NoDemo) { & $musicPython -m app.demo; if ($LASTEXITCODE) { throw 'Demo preparation failed' } }
pnpm --dir frontend build
if ($LASTEXITCODE) { throw 'Web build failed' }
if ($WithClap) {
    & $musicPython -m pip install -r backend/requirements-ml.txt
    if ($LASTEXITCODE) { throw 'CLAP dependencies failed' }
    & $musicPython scripts/prepare_clap.py
    if ($LASTEXITCODE) { throw 'Pinned CLAP preparation failed' }
}
Write-Host 'Setup complete. Run .\scripts\start.ps1'
