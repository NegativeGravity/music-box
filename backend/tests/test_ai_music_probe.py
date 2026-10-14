"""Boundary checks for local inference; no model or provider is needed."""
import importlib.util
import hashlib
import io
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import threading
from types import SimpleNamespace
import wave
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


@pytest.fixture
def probe():
    path = Path(__file__).resolve().parents[2] / 'scripts' / 'ai_music_probe.py'
    if not path.exists():
        pytest.fail('The local inference probe has not been implemented yet.')
    spec = importlib.util.spec_from_file_location('ai_music_probe', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def wav_bytes(silent=False):
    output = io.BytesIO()
    samples = [0 if silent else int(10000 * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(1600)]
    with wave.open(output, 'wb') as wav:
        wav.setparams((1, 2, 16000, 1600, 'NONE', 'not compressed'))
        wav.writeframes(struct.pack('<1600h', *samples))
    return output.getvalue()


@contextmanager
def engine_stub(status=1, file_url='/v1/audio?path=sample.wav', redirect=False,
                query_started=None, query_continue=None):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append((self.path, body))
            if self.path == '/release_task':
                data = {'task_id': 'task-1', 'status': 'queued', 'queue_position': 1}
            elif self.path == '/query_result':
                assert body == {'task_id_list': ['task-1']}
                if query_started is not None:
                    query_started.set()
                    query_continue.wait(timeout=3)
                data = [{'task_id': 'task-1', 'status': status,
                         'result': json.dumps([{'file': file_url, 'status': 1}])}]
            else:
                self.send_error(404)
                return
            encoded = json.dumps({'data': data, 'code': 200, 'error': None, 'timestamp': 0, 'extra': None}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            calls.append((self.path, None))
            if redirect:
                self.send_response(302)
                self.send_header('Location', 'http://example.invalid/output.wav')
                self.end_headers()
                return
            audio = wav_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'audio/wav')
            self.send_header('Content-Length', str(len(audio)))
            self.end_headers()
            self.wfile.write(audio)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_timeout_keeps_task_id_without_resubmitting(probe, tmp_path):
    with engine_stub(status=0) as (url, calls):
        report = probe.run_probe(url, {'prompt': 'original instrumental', 'audio_format': 'wav'}, tmp_path, timeout_seconds=.05)
    assert report['status'] == 'timed_out'
    assert report['task_id'] == 'task-1'
    assert sum(path == '/release_task' for path, _ in calls) == 1
    assert json.loads((tmp_path / 'report.json').read_text())['task_id'] == 'task-1'
    assert not (tmp_path / 'output.wav').exists()


def test_accepted_task_is_reported_running_until_result(probe, tmp_path):
    started, proceed = threading.Event(), threading.Event()
    with engine_stub(query_started=started, query_continue=proceed) as (url, _calls):
        reports = []
        worker = threading.Thread(target=lambda: reports.append(
            probe.run_probe(url, {'prompt': 'original instrumental'}, tmp_path)))
        worker.start()
        try:
            assert started.wait(timeout=3)
            pending = json.loads((tmp_path / 'report.json').read_text())
            assert pending['task_id'] == 'task-1'
            assert pending['status'] == 'running'
            assert pending['error'] is None
        finally:
            proceed.set()
            worker.join(timeout=5)
        assert reports[0]['status'] == 'completed'


def test_external_result_url_is_rejected_without_fetching(probe, tmp_path):
    with engine_stub(file_url='http://example.invalid/output.wav') as (url, calls):
        report = probe.run_probe(url, {'prompt': 'original instrumental'}, tmp_path)
    assert report['status'] == 'failed'
    assert 'origin' in report['error'].lower()
    assert not any(path.startswith('/v1/audio') for path, _ in calls)
    assert not (tmp_path / 'output.wav').exists()


def test_audio_redirect_is_rejected(probe, tmp_path):
    with engine_stub(redirect=True) as (url, _calls):
        report = probe.run_probe(url, {'prompt': 'original instrumental'}, tmp_path)
    assert report['status'] == 'failed'
    assert 'redirect' in report['error'].lower()
    assert not (tmp_path / 'output.wav').exists()


def test_corrupt_audio_is_not_valid(probe, tmp_path):
    path = tmp_path / 'corrupt.wav'
    path.write_bytes(b'this is not audio')
    assert probe.inspect_audio(path)['valid'] is False


def test_truncated_pcm_is_rejected_by_dependency_free_decoder(probe, tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'soundfile', None)
    path = tmp_path / 'truncated.wav'
    path.write_bytes(wav_bytes()[:64])
    result = probe.inspect_audio(path)
    assert result['valid'] is False
    assert 'truncated' in result['error'].lower()


def test_silent_audio_is_not_valid(probe, tmp_path):
    path = tmp_path / 'silence.wav'
    path.write_bytes(wav_bytes(silent=True))
    result = probe.inspect_audio(path)
    assert result['valid'] is False
    assert result['rms_amplitude'] == 0


def test_completed_audio_is_inspected_and_persisted(probe, tmp_path):
    with engine_stub() as (url, calls):
        report = probe.run_probe(url, {'prompt': 'original instrumental', 'audio_format': 'wav'}, tmp_path)
    assert report['status'] == 'completed'
    assert report['audio']['valid'] is True
    assert report['audio']['duration_seconds'] == .1
    assert report['audio']['sample_rate'] == 16000
    assert report['audio']['channels'] == 1
    assert report['audio']['peak_amplitude'] > .3
    assert json.loads((tmp_path / 'request.json').read_text())['prompt'] == 'original instrumental'
    assert (tmp_path / 'output.wav').read_bytes() == wav_bytes()
    assert sum(path == '/release_task' for path, _ in calls) == 1


def test_nested_case_directory_records_runtime_manifest(probe, tmp_path):
    root = tmp_path / 'local-engine'
    root.mkdir()
    runtime = {'source_commit': 'pinned-source', 'device': 'cpu', 'model': 'turbo'}
    (root / 'runtime.json').write_text(json.dumps(runtime))
    with engine_stub() as (url, _calls):
        report = probe.run_probe(url, {'prompt': 'original instrumental'},
                                 root / 'evaluation' / 'run-1' / 'instrumental')
    assert report['status'] == 'completed'
    assert report['runtime_manifest'] == runtime


def test_cli_disables_random_seed_and_requests_one_take(probe, tmp_path):
    with engine_stub() as (url, calls):
        result = subprocess.run([sys.executable, probe.__file__, '--case', 'instrumental',
                                 '--base-url', url, '--output-dir', str(tmp_path)],
                                capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    submitted = next(body for path, body in calls if path == '/release_task')
    assert submitted['use_random_seed'] is False
    assert submitted['seed'] == 42
    assert submitted['batch_size'] == 1


def test_reference_probe_records_the_actual_reference_hash(probe, tmp_path):
    reference = tmp_path / 'reference.wav'
    source = wav_bytes()
    reference.write_bytes(source)
    output = tmp_path / 'new-take'
    with engine_stub() as (url, _calls):
        report = probe.run_probe(url, {'prompt': 'related original instrumental',
                                      'reference_audio_path': str(reference)}, output)
    assert report['status'] == 'completed'
    recorded = json.loads((output / 'reference.json').read_text())
    assert recorded['sha256'] == hashlib.sha256(source).hexdigest()
    assert recorded['size_bytes'] == len(source)
    assert report['case'] == 'reference'


def test_missing_reference_is_not_submitted(probe, tmp_path):
    with engine_stub() as (url, calls):
        report = probe.run_probe(url, {'prompt': 'related instrumental',
                                      'reference_audio_path': str(tmp_path / 'missing.wav')}, tmp_path / 'take')
    assert report['status'] == 'failed'
    assert report['task_id'] is None
    assert not any(path == '/release_task' for path, _ in calls)


def test_unavailable_memory_measurement_is_null_not_zero(probe, tmp_path, monkeypatch):
    class AccessDenied(Exception):
        pass

    class Process:
        def children(self, recursive=True):
            return []

        def memory_info(self):
            raise AccessDenied()

    monkeypatch.setitem(sys.modules, 'psutil', SimpleNamespace(
        Process=lambda _pid: Process(), AccessDenied=AccessDenied, NoSuchProcess=ProcessLookupError))
    with engine_stub() as (url, _calls):
        report = probe.run_probe(url, {'prompt': 'original instrumental'}, tmp_path, engine_pid=123)
    assert report['status'] == 'completed'
    assert report['peak_process_rss_bytes'] is None


def metadata_only_engine(tmp_path, include_vae=True):
    """Stand in for the vendor's costly startup, keeping its readiness contract."""
    root = tmp_path / 'vendor'
    package = root / 'acestep'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text('import tempfile\ntempfile.gettempdir()\n')
    (package / 'model_downloader.py').write_text(
        "MAIN_MODEL_COMPONENTS = ['acestep-v15-turbo', 'vae', 'Qwen3-Embedding-0.6B', 'acestep-5Hz-lm-1.7B']\n"
        "def check_main_model_exists(root):\n"
        "    return all((root / item / 'model.safetensors').exists() for item in MAIN_MODEL_COMPONENTS)\n")
    (package / 'api_server.py').write_text(
        "from pathlib import Path\nimport os, tempfile\nfrom acestep.model_downloader import check_main_model_exists\n"
        "expected_temp = os.environ.get('ACESTEP_TMPDIR')\n"
        "if expected_temp:\n"
        "    for name in ('TMPDIR', 'TEMP', 'TMP'): os.environ[name] = expected_temp\n"
        "    if Path(tempfile.gettempdir()).resolve() != Path(expected_temp).resolve():\n"
        "        raise RuntimeError('reference path is outside engine temp directory')\n"
        "root = Path(__file__).resolve().parents[1] / 'checkpoints'\n"
        "if not check_main_model_exists(root): raise RuntimeError('missing core weights; download required')\n"
        "print('core-models-ready')\n")
    for component in ['acestep-v15-turbo', 'Qwen3-Embedding-0.6B', *(['vae'] if include_vae else [])]:
        folder = root / 'checkpoints' / component
        folder.mkdir(parents=True)
        (folder / 'model.safetensors').write_bytes(b'metadata fixture, not model weights')
    return root


def test_runner_does_not_require_unused_planner_weights(probe, tmp_path):
    root = metadata_only_engine(tmp_path)
    runner = Path(probe.__file__).with_name('ai_music_engine.py')
    result = subprocess.run([sys.executable, str(runner), '--engine-root', str(root)],
                            env={**os.environ, 'ACESTEP_INIT_LLM': 'false'},
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'core-models-ready' in result.stdout


def test_runner_still_requires_actual_core_components(probe, tmp_path):
    root = metadata_only_engine(tmp_path, include_vae=False)
    runner = Path(probe.__file__).with_name('ai_music_engine.py')
    result = subprocess.run([sys.executable, str(runner), '--engine-root', str(root)],
                            env={**os.environ, 'ACESTEP_INIT_LLM': 'false'},
                            capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert 'missing core weights' in result.stderr


def test_runner_configures_temp_root_before_vendor_imports(probe, tmp_path):
    root = metadata_only_engine(tmp_path)
    runner = Path(probe.__file__).with_name('ai_music_engine.py')
    result = subprocess.run([sys.executable, str(runner), '--engine-root', str(root)],
                            env={**os.environ, 'ACESTEP_INIT_LLM': 'false',
                                 'ACESTEP_TMPDIR': str(tmp_path / 'engine-tmp')},
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'core-models-ready' in result.stdout
