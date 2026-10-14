import importlib
import json
from pathlib import Path
import sys

import pytest


def module(name):
    assert importlib.util.find_spec(name) is not None, f'{name} must implement the YuE2 runtime'
    return importlib.import_module(name)


SCORE = '''X:1
T:
M:4/4
L:1/32
Q:1/4=120
V: Vocal clef=treble name="Vocal Melody" snm="Vocal"
V: Ins clef=treble name="Ins Melody" snm="Inst."
K:C
V: Vocal
"C" C8 D8 E8 G8 | "Am" A16 G16 |
V: Ins
C,32 | A,32 |
'''


def test_instrumental_score_moves_original_melody_without_vocal_notes():
    score = module('app.yue2_score')
    result = score.instrumental_score(SCORE)
    before, after = score.parse_score(SCORE), score.parse_score(result)
    assert after.voices['Vocal'].notes == []
    assert after.voices['Ins'].notes == before.voices['Vocal'].notes
    assert after.voices['Vocal'].chords == before.voices['Vocal'].chords
    assert after.bpm == 120


def test_invalid_symbolic_score_fails_instead_of_falling_back_to_vocals():
    with pytest.raises(ValueError):
        module('app.yue2_score').instrumental_score('not a musical score')


def test_command_keeps_prompt_as_data_and_bounds_semantic_duration(tmp_path):
    runtime = module('app.yue2_runtime').Yue2Runtime(tmp_path)
    runtime.cli = tmp_path / 'runtime/audiocpp_cli.exe'
    cmd = runtime.command({'caption': 'piano; --backend cuda', 'mode': 'vocal',
                           'lyrics': '[Verse]\nHello', 'language': 'en',
                           'seed': 42, 'duration_seconds': 30}, tmp_path / 'output')
    assert cmd[0] == str(runtime.cli)
    assert cmd[cmd.index('--backend') + 1] == 'cpu'
    assert 'style=English, piano; --backend cuda' in cmd
    assert 'semantic_max_tokens=750' in cmd
    assert cmd[cmd.index('--lyrics') + 1] == '[Verse]\nHello'


def test_unprepared_runtime_is_unavailable_and_corrupt_asset_is_not_ready(tmp_path):
    runtime = module('app.yue2_runtime').Yue2Runtime(tmp_path)
    assert runtime.health()['models_initialized'] is False
    binary = tmp_path / 'runtime/audiocpp_cli.exe'
    binary.parent.mkdir()
    binary.write_bytes(b'fixture')
    (tmp_path / 'runtime.json').write_text(json.dumps({'verified': True,
        'files': [{'path': 'runtime/audiocpp_cli.exe', 'size': 99}]}))
    assert runtime.health()['models_initialized'] is False


def test_checksum_rejection_preserves_existing_file(tmp_path):
    installer = module('scripts.setup_yue2')
    asset = tmp_path / 'weights.gguf'
    asset.write_bytes(b'bad bytes')
    with pytest.raises(ValueError, match='checksum'):
        installer.verify_file(asset, size=9, sha256='0' * 64)
    assert asset.read_bytes() == b'bad bytes'


def test_audio_validation_uses_observed_duration_and_rejects_silence(tmp_path):
    import wave
    import numpy as np
    inspect = module('app.yue2_runtime').inspect_audio
    path = tmp_path / 'audio.wav'
    def write(signal):
        with wave.open(str(path), 'wb') as wav:
            wav.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
            wav.writeframes(np.repeat(signal[:, None], 2, axis=1).astype('<i2').tobytes())
    write(np.sin(np.arange(96000) * 2 * np.pi * 220 / 48000) * 8000)
    assert inspect(path, 30)['duration_seconds'] == pytest.approx(2, abs=.01)
    write(np.zeros(96000))
    with pytest.raises(ValueError, match='silent'):
        inspect(path, 30)


def test_native_runner_records_failure_without_claiming_audio(tmp_path, monkeypatch):
    import psutil
    from types import SimpleNamespace
    monkeypatch.setattr(psutil, 'virtual_memory', lambda: SimpleNamespace(available=24 * 1024**3))
    runner = module('app.yue2_runner')
    runtime = module('app.yue2_runtime').Yue2Runtime(tmp_path)
    monkeypatch.setattr(runtime, 'command', lambda *a, **k: [sys.executable, '-c', 'raise SystemExit(7)'])
    config = tmp_path / 'request.json'
    config.write_text(json.dumps({'attempt_id': 'fixture', 'request': {'mode': 'vocal', 'duration_seconds': 30},
                                 'runtime_root': str(tmp_path), 'timeout_seconds': 10}))
    (tmp_path / 'start.ready').write_text(json.dumps({'attempt_id': 'fixture'}))
    runner.run_candidate(config, runtime=runtime)
    result = json.loads((tmp_path / 'runner-result.json').read_text())
    assert result['status'] == 'failed'
    assert result['error_code'] == 'RuntimeError'
    assert not (tmp_path / 'audio.wav').exists()


def test_cancelled_runner_does_not_start_native_inference(tmp_path, monkeypatch):
    runner = module('app.yue2_runner')
    runtime = module('app.yue2_runtime').Yue2Runtime(tmp_path)
    monkeypatch.setattr(runtime, 'command', lambda *a, **k: pytest.fail('Cancelled request started inference'))
    config = tmp_path / 'request.json'
    config.write_text(json.dumps({'attempt_id': 'fixture', 'request': {'mode': 'vocal', 'duration_seconds': 30},
                                 'runtime_root': str(tmp_path)}))
    (tmp_path / 'start.ready').write_text(json.dumps({'attempt_id': 'fixture'}))
    (tmp_path / 'cancel.requested').touch()
    runner.run_candidate(config, runtime=runtime)
    assert json.loads((tmp_path / 'runner-result.json').read_text())['status'] == 'cancelled'


def test_verified_runtime_detects_same_size_asset_changes(tmp_path):
    runtime = module('app.yue2_runtime').Yue2Runtime(tmp_path)
    names = ['runtime/audiocpp_cli.exe', 'models/yue2-3b-q8_0.gguf', 'models/yue2-vae-f16.gguf',
             'models/sidecars/yue2-model-config.json', 'models/sidecars/yue2-generation-config.json',
             'models/sidecars/yue2-vae-config.json', 'models/sidecars/yue2-qwen.tiktoken']
    entries = []
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'fixture')
        entries.append({'path': name, 'size': 7, 'mtime_ns': path.stat().st_mtime_ns})
    (tmp_path / 'runtime.json').write_text(json.dumps({'verified': True, 'runtime_release': 'v0.9.1',
        'runtime_variant': 'cpu-portable',
        'model_revision': 'cca74d8980c00f30ab8d52248a367c01ad633ec2', 'files': entries}))
    assert runtime.health()['models_initialized'] is True
    asset = tmp_path / names[1]
    asset.write_bytes(b'changed')
    import os
    os.utime(asset, ns=(asset.stat().st_atime_ns, entries[1]['mtime_ns'] + 1_000_000_000))
    assert runtime.health()['models_initialized'] is False
