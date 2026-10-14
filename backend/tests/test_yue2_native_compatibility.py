"""Regression coverage for the observed Windows native crash and file lock."""
import ctypes
import json
import os
from pathlib import Path
import threading
import time
import zipfile

import pytest
from app.yue2_runner import atomic_json
from app.yue2_runtime import Yue2Runtime
from scripts import setup_yue2


def test_installer_uses_the_verified_baseline_cpu_portable_release():
    assert getattr(setup_yue2, 'RUNTIME_ARCHIVE', '').endswith('-windows-x64-cpu-portable.zip')
    assert setup_yue2.ZIP_HASH == '2fccc73dc97af84da1e81c3af545fa619348a46d023ea7d927ba80bc8680d2a0'


def test_install_replaces_an_existing_incompatible_executable(tmp_path):
    native = tmp_path / 'runtime'
    native.mkdir()
    (native / 'audiocpp_cli.exe').write_bytes(b'old-host-specific')
    archive = tmp_path / 'portable.zip'
    with zipfile.ZipFile(archive, 'w') as zipped:
        zipped.writestr('audiocpp_cli.exe', b'new-portable-code')
        zipped.writestr('model_specs/yue2.json', '{}')
    assert hasattr(setup_yue2, 'install_runtime_archive'), 'Installer must migrate an existing runtime'
    setup_yue2.install_runtime_archive(archive, native)
    assert (native / 'audiocpp_cli.exe').read_bytes() == b'new-portable-code'
    assert (native / 'model_specs/yue2.json').is_file()


def test_verified_nonportable_runtime_is_not_reported_ready(tmp_path):
    names = ['runtime/audiocpp_cli.exe', 'models/yue2-3b-q8_0.gguf', 'models/yue2-vae-f16.gguf',
             'models/sidecars/yue2-model-config.json', 'models/sidecars/yue2-generation-config.json',
             'models/sidecars/yue2-vae-config.json', 'models/sidecars/yue2-qwen.tiktoken']
    entries = []
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'fixture')
        entries.append({'path': name, 'size': 7, 'mtime_ns': path.stat().st_mtime_ns})
    manifest = {'verified': True, 'runtime_release': 'v0.9.1',
                'model_revision': 'cca74d8980c00f30ab8d52248a367c01ad633ec2', 'files': entries}
    (tmp_path / 'runtime.json').write_text(json.dumps(manifest))
    assert Yue2Runtime(tmp_path).health()['models_initialized'] is False
    manifest['runtime_variant'] = 'cpu-portable'
    (tmp_path / 'runtime.json').write_text(json.dumps(manifest))
    assert Yue2Runtime(tmp_path).health()['models_initialized'] is True


@pytest.mark.skipif(os.name != 'nt', reason='Windows file sharing semantics')
def test_atomic_heartbeat_survives_a_brief_windows_reader_lock(tmp_path):
    from ctypes import wintypes
    path = tmp_path / 'heartbeat.json'
    atomic_json(path, {'old': True})
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(path), 0x80000000, 3, None, 3, 0, None)
    assert handle not in (None, ctypes.c_void_p(-1).value)
    def release():
        time.sleep(.08)
        kernel.CloseHandle(handle)
    reader = threading.Thread(target=release)
    reader.start()
    try:
        atomic_json(path, {'new': True})
    finally:
        reader.join()
    assert json.loads(path.read_text()) == {'new': True}
