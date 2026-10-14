param([string]$DataDir = '')
$ErrorActionPreference = 'Stop'
$musicRoot = Split-Path $PSScriptRoot -Parent
$musicStateFile = if ($DataDir) { Join-Path ([IO.Path]::GetFullPath($DataDir)) 'processes.json' } else { Join-Path $musicRoot '.data\processes.json' }
if (-not (Test-Path -LiteralPath $musicStateFile)) { Write-Host 'No managed Musicbox processes recorded'; return }
$musicState = Get-Content -LiteralPath $musicStateFile -Raw | ConvertFrom-Json
if ($musicState.root -ne $musicRoot) { throw 'Process record belongs to another checkout. No process was stopped.' }
function Stop-MusicboxTree([int]$musicProcessId) {
    # A Windows venv launcher starts a child interpreter; stop the verified tree.
    $musicChildren = Get-CimInstance Win32_Process -Filter "ParentProcessId = $musicProcessId" -ErrorAction SilentlyContinue
    foreach ($musicChild in $musicChildren) { Stop-MusicboxTree $musicChild.ProcessId }
    Stop-Process -Id $musicProcessId -Force -ErrorAction SilentlyContinue
}
foreach ($musicService in @('web','generation_worker','ml_worker','worker','api','ai_engine')) {
    $musicRecord = $musicState.$musicService
    if (-not $musicRecord) { continue }
    $musicPid = if ($musicRecord -is [ValueType]) { $musicRecord } else { $musicRecord.pid }
    $musicProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $musicPid" -ErrorAction SilentlyContinue
    if (-not $musicProcess -or -not $musicProcess.CommandLine) { continue }
    $musicOwned = $musicProcess.CommandLine.Contains($musicRoot)
    if ($musicRecord.started_at) {
        $musicExpected = ([datetime]$musicRecord.started_at).ToUniversalTime()
        if (-not $musicProcess.CreationDate -or [Math]::Abs(($musicProcess.CreationDate.ToUniversalTime() - $musicExpected).TotalSeconds) -gt 1) { continue }
        if ($musicRecord.executable -and $musicRecord.marker) {
            $musicOwned = $musicProcess.ExecutablePath -eq $musicRecord.executable -and $musicProcess.CommandLine.Contains($musicRecord.marker)
        }
    }
    if ($musicOwned) {
        Stop-MusicboxTree $musicPid
    }
}
Remove-Item -LiteralPath $musicStateFile
Write-Host 'Recorded Musicbox processes stopped'
