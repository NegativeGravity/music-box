param([Parameter(Mandatory=$true)][string]$PythonExe, [string]$DataRoot = '')
$ErrorActionPreference = 'Stop'
$musicRoot = Split-Path $PSScriptRoot -Parent
if (-not $DataRoot) { $DataRoot = Join-Path $musicRoot '.data\ai-music\yue2' }
& $PythonExe (Join-Path $PSScriptRoot 'setup_yue2.py') --root ([IO.Path]::GetFullPath($DataRoot))
if ($LASTEXITCODE -ne 0) { throw 'YuE2 installation failed; the runtime is not marked ready.' }
