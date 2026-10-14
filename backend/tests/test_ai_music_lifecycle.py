"""Exercise PowerShell lifecycle decisions with fake processes, never model loads."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

REPO = Path(__file__).resolve().parents[2]


def shell(root, code):
    pwsh = shutil.which('pwsh') or str(Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe')
    if not Path(pwsh).is_file():
        pytest.skip('PowerShell runtime unavailable on this platform')
    harness = root / 'harness.ps1'
    harness.write_text(code, encoding='utf-8')
    result = subprocess.run([pwsh, '-NoProfile', '-File', str(harness)], cwd=root, capture_output=True, text=True, timeout=30)
    return result


@pytest.fixture
def runtime(tmp_path):
    scripts = tmp_path / 'scripts'
    scripts.mkdir()
    for name in ['start.ps1', 'stop.ps1', 'start_ai_music.ps1']:
        shutil.copyfile(REPO / 'scripts' / name, scripts / name)
    python = tmp_path / '.venv/Scripts/python.exe'
    python.parent.mkdir(parents=True)
    python.touch()
    data = tmp_path / '.data/ai-music'
    engine_python = data / 'engine/.venv/Scripts/python.exe'
    engine_python.parent.mkdir(parents=True)
    engine_python.touch()
    (data / 'runtime.json').write_text('{"pid":null,"base_url":"http://127.0.0.1:8001"}', encoding='utf-8')
    return tmp_path


MOCK_START = r'''
$global:musicTestStarted = @()
function Get-Command { return @{Source='node.exe'} }
function Get-NetTCPConnection { param($LocalPort,$State,$ErrorAction) return $null }
function Start-Process {
  param($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle,[switch]$PassThru,$RedirectStandardOutput,$RedirectStandardError)
  $global:musicTestStarted += @{file=$FilePath;args=$ArgumentList;hidden=$WindowStyle;temp=$env:MUSICBOX_AI_TEMP_DIR;url=$env:MUSICBOX_AI_ENGINE_URL}
  return [pscustomobject]@{Id=(100+$global:musicTestStarted.Count);StartTime=[datetime]'2026-10-13T10:00:00Z'}
}
'''


def test_default_start_does_not_load_ai(runtime):
    result = shell(runtime, MOCK_START + "\n. ./scripts/start.ps1 -Dev\n$global:musicTestStarted | ConvertTo-Json -Depth 6")
    assert result.returncode == 0, result.stderr
    # Native scripts write operator output as well; locate the trailing JSON.
    calls = json.loads(result.stdout[result.stdout.index('[\n'):])
    assert len(calls) == 4
    assert all(call['hidden'] == 'Hidden' for call in calls)
    assert not any('app.generation_worker' in call['args'] for call in calls)


def test_opt_in_starts_owned_engine_and_worker_with_matching_temp(runtime):
    result = shell(runtime, MOCK_START + "\n. ./scripts/start.ps1 -Dev -WithAiMusic -AiEngine ace-step\n$global:musicTestStarted | ConvertTo-Json -Depth 6")
    assert result.returncode == 0, result.stderr
    calls = json.loads(result.stdout[result.stdout.index('[\n'):])
    assert len(calls) == 6
    worker = next(call for call in calls if 'app.generation_worker' in call['args'])
    assert Path(worker['temp']) == runtime / '.data/ai-music/cache/tmp'
    assert worker['url'] == 'http://127.0.0.1:8001'
    record = json.loads((runtime / '.data/processes.json').read_text(encoding='utf-8-sig'))
    assert record['ai_engine']['pid'] and record['generation_worker']['pid']
    assert record['ai_engine']['started_at']


def test_busy_engine_fails_before_starting_any_app_process(runtime):
    code = MOCK_START + r'''
function Get-NetTCPConnection { param($LocalPort,$State,$ErrorAction) if($LocalPort -eq 8001) { return @{OwningProcess=999} } }
try { . ./scripts/start.ps1 -Dev -WithAiMusic -AiEngine ace-step } catch { Write-Output $_.Exception.Message }
Write-Output "START_COUNT=$($global:musicTestStarted.Count)"
'''
    result = shell(runtime, code)
    assert 'START_COUNT=0' in result.stdout
    assert 'occupied' in result.stdout.lower()


def test_stop_checks_new_ai_process_identity_and_creation_time(runtime):
    state = runtime / '.data/processes.json'
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({'root': str(runtime), 'ai_engine': {'pid': 201, 'started_at': '2026-10-13T10:00:00.0000000Z'}, 'generation_worker': {'pid': 202, 'started_at': '2026-10-13T10:00:00.0000000Z'}}))
    code = r'''
$script:stopped=@()
function Get-CimInstance { param($ClassName,$Filter,$ErrorAction)
 if($Filter -eq 'ProcessId = 201') { return @{ProcessId=201;CommandLine="$PSScriptRoot/scripts/ai_music_engine.py";CreationDate=[datetime]'2026-10-13T11:00:00Z'} }
 if($Filter -eq 'ProcessId = 202') { return @{ProcessId=202;CommandLine="$PSScriptRoot/backend app.generation_worker";CreationDate=[datetime]'2026-10-13T10:00:00Z'} }
 return $null
}
function Stop-Process { param($Id,[switch]$Force,$ErrorAction) $script:stopped += $Id }
. ./scripts/stop.ps1
Write-Output "STOPPED=$($script:stopped -join ',')"
'''
    result = shell(runtime, code)
    assert result.returncode == 0, result.stderr
    assert 'STOPPED=202' in result.stdout
    assert (runtime / '.data/ai-music/runtime.json').exists()


def test_default_ai_start_uses_yue2_worker_without_loading_ace_step(runtime):
    yue = runtime / '.data/ai-music/yue2'
    (yue / 'runtime').mkdir(parents=True)
    (yue / 'runtime/audiocpp_cli.exe').touch()
    (yue / 'runtime.json').write_text('{"verified":true,"engine":"yue2","runtime_release":"v0.9.1","model_revision":"cca74d8980c00f30ab8d52248a367c01ad633ec2"}')
    result = shell(runtime, MOCK_START + "\n. ./scripts/start.ps1 -Dev -WithAiMusic\n$global:musicTestStarted | ConvertTo-Json -Depth 6")
    assert result.returncode == 0, result.stderr
    calls = json.loads(result.stdout[result.stdout.index('[\n'):])
    assert len(calls) == 5
    assert any('app.generation_worker' in call['args'] for call in calls)
    record = json.loads((runtime / '.data/processes.json').read_text(encoding='utf-8-sig'))
    assert 'ai_engine' not in record
    assert record['generation_worker']['pid']
