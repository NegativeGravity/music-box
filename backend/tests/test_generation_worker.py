import io
import json
import math
import struct
import threading
import time
import wave
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from app.main import create_app
from client import TestClient


@pytest.fixture(autouse=True)
def legacy_engine(monkeypatch):
    monkeypatch.setenv('MUSICBOX_AI_ENGINE', 'ace-step')


@contextmanager
def engine_stub(kind='valid', result_status=1):
    calls = []
    state = {'pending': kind == 'completion_race'}
    samples = (0 if kind == 'silent' else int(10000 * math.sin(i * .34)) for i in range(160000))
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav:
        wav.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        wav.writeframes(struct.pack('<160000h', *samples))
    audio = b'corrupt audio' if kind == 'corrupt' else buffer.getvalue()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, data):
            raw = json.dumps({'code': 200, 'data': data, 'error': None}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append((self.path, body))
            if self.path == '/release_task':
                if kind == 'submit_lost':
                    self.close_connection = True
                    return
                self.reply({'task_id': 'engine-1'})
            else:
                assert body['task_id_list'][0] in ('engine-1', 'persisted')
                file = 'http://example.invalid/audio.wav' if kind == 'external' else '/v1/audio?path=output.wav'
                self.reply([] if kind == 'lost' else [{'task_id': body['task_id_list'][0],
                    'status': 0 if state['pending'] else result_status, 'result': json.dumps([{'file': file, 'status': 1}])}])

        def do_GET(self):
            calls.append((self.path, None))
            if self.path == '/health':
                self.reply({'models_initialized': True, 'llm_initialized': False})
            elif self.path == '/v1/stats':
                state['pending'] = False
                self.reply({'jobs': {'running': 0 if kind in ('lost', 'completion_race', 'idle_but_pending') else 1,
                                    'queued': 0}, 'queue_size': 0})
            elif self.path.startswith('/v1/audio'):
                if kind == 'redirect':
                    self.send_response(302)
                    self.send_header('Location', 'http://example.invalid/audio.wav')
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(audio)))
                    self.end_headers()
                    self.wfile.write(audio)
            else:
                self.send_error(404)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.fixture
def classes():
    try:
        from app.local_music_engine import LocalMusicEngine
        from app.generation_worker import GenerationWorker
    except ImportError:
        pytest.fail('The local adapter and generation worker are not implemented.')
    return LocalMusicEngine, GenerationWorker


@pytest.fixture
def job_env(tmp_path):
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    auth = client.post('/v1/session').json()
    client.headers['X-CSRF-Token'] = auth['csrf_token']
    jid = client.post('/v1/generation/jobs', json={'prompt': 'Warm synths', 'mode': 'instrumental'},
                      headers={'Idempotency-Key': 'worker-test'}).json()['job']['id']
    yield app.state.service, client, jid
    client.close()


def test_worker_finishes_native_output_and_library_flow(classes, job_env, tmp_path):
    Engine, Worker = classes
    service, client, jid = job_env
    with engine_stub() as (url, calls):
        Worker(service, Engine(url), tmp_path / 'engine-tmp').run_once()
    report = client.get(f'/v1/generation/jobs/{jid}').json()['job']
    assert report['status'] == 'completed'
    assert sum(path == '/release_task' for path, _ in calls) == 1
    body = next(body for path, body in calls if path == '/release_task')
    assert body['thinking'] is False and body['use_random_seed'] is False
    assert body['batch_size'] == 1 and body['audio_duration'] == 20
    assert client.get(f'/v1/generation/jobs/{jid}/audio').content[:4] == b'RIFF'
    assert client.post(f'/v1/generation/jobs/{jid}/save').status_code == 200


@pytest.mark.parametrize('kind', ['corrupt', 'silent', 'external', 'redirect'])
def test_invalid_engine_output_never_completes(classes, job_env, tmp_path, kind):
    Engine, Worker = classes
    service, client, jid = job_env
    with engine_stub(kind) as (url, _calls):
        Worker(service, Engine(url), tmp_path / 'engine-tmp').run_once()
    report = client.get(f'/v1/generation/jobs/{jid}').json()['job']
    assert report['status'] == 'failed'
    assert report['audio_url'] is None
    assert client.get(f'/v1/generation/jobs/{jid}/audio').status_code == 409


def test_recovery_queries_persisted_task_without_resubmission(classes, job_env, tmp_path):
    from app.db import GenerationJob
    Engine, Worker = classes
    service, client, jid = job_env
    with service.Session.begin() as db:
        job = db.get(GenerationJob, jid)
        job.status, job.upstream_task_id, job.leased_until = 'running', 'persisted', 0
    with engine_stub() as (url, calls):
        Worker(service, Engine(url), tmp_path / 'engine-tmp').run_once()
    assert client.get(f'/v1/generation/jobs/{jid}').json()['job']['status'] == 'completed'
    assert not any(path == '/release_task' for path, _ in calls)


def test_expired_uncertain_submission_is_never_resubmitted(classes, job_env, tmp_path):
    from app.db import GenerationJob
    Engine, Worker = classes
    service, client, jid = job_env
    with service.Session.begin() as db:
        job = db.get(GenerationJob, jid)
        job.status, job.leased_until = 'submitting', 0
    with engine_stub() as (url, calls):
        worker = Worker(service, Engine(url), tmp_path / 'engine-tmp')
        worker.run_once()
        worker.run_once()
    report = client.get(f'/v1/generation/jobs/{jid}').json()['job']
    assert report['status'] == 'interrupted'
    assert report['phase'] == 'awaiting_engine_idle'
    assert client.get('/v1/generation/status').json()['active'] is True
    assert not any(path == '/release_task' for path, _ in calls)


def test_lost_task_after_engine_restart_is_interrupted(classes, job_env, tmp_path):
    from app.db import GenerationJob
    Engine, Worker = classes
    service, client, jid = job_env
    with service.Session.begin() as db:
        job = db.get(GenerationJob, jid)
        job.status, job.upstream_task_id = 'running', 'persisted'
    with engine_stub('lost') as (url, calls):
        Worker(service, Engine(url), tmp_path / 'engine-tmp').run_once()
    assert client.get(f'/v1/generation/jobs/{jid}').json()['job']['status'] == 'interrupted'
    assert not any(path == '/release_task' for path, _ in calls)


def test_two_workers_cannot_claim_two_generations(classes, job_env, tmp_path):
    import concurrent.futures
    Engine, Worker = classes
    service, client, _ = job_env
    client.post('/v1/generation/jobs', json={'prompt': 'Another', 'mode': 'instrumental'},
                headers={'Idempotency-Key': 'second'})
    workers = [Worker(service, Engine(), tmp_path / 'temp') for _ in range(2)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(pool.map(lambda worker: worker.claim(), workers))
    assert sum(job is not None for job in jobs) == 1


@pytest.mark.parametrize('url', ['https://music-provider.invalid', 'http://example.com',
                                 'http://user:pass@localhost:8001', 'http://localhost:8001/path'])
def test_only_plain_loopback_engine_origin_is_allowed(classes, url):
    Engine, _ = classes
    with pytest.raises(ValueError):
        Engine(url)


def test_observation_timeout_keeps_task_and_does_not_resubmit(classes, job_env, tmp_path):
    Engine, Worker = classes
    service, client, jid = job_env
    with engine_stub(result_status=0) as (url, calls):
        Worker(service, Engine(url), tmp_path / 'temp', observation_seconds=.1, poll_seconds=.01).run_once()
    job = client.get(f'/v1/generation/jobs/{jid}').json()['job']
    assert job['status'] == 'interrupted'
    from app.db import GenerationJob
    with service.Session() as db:
        assert db.get(GenerationJob, jid).upstream_task_id == 'engine-1'
    assert sum(path == '/release_task' for path, _ in calls) == 1


def test_confirmed_upstream_failure_is_terminal(classes, job_env, tmp_path):
    Engine, Worker = classes
    service, client, jid = job_env
    with engine_stub(result_status=2) as (url, _calls):
        Worker(service, Engine(url), tmp_path / 'temp').run_once()
    assert client.get(f'/v1/generation/jobs/{jid}').json()['job']['status'] == 'failed'


def test_lost_submission_response_does_not_create_another_task(classes, job_env, tmp_path):
    Engine, Worker = classes
    service, client, jid = job_env
    with engine_stub('submit_lost') as (url, calls):
        worker = Worker(service, Engine(url), tmp_path / 'temp')
        worker.run_once()
        worker.run_once()
    assert client.get(f'/v1/generation/jobs/{jid}').json()['job']['status'] == 'interrupted'
    assert sum(path == '/release_task' for path, _ in calls) == 1


def test_reconciliation_does_not_clear_another_workers_new_lease(classes, job_env, tmp_path):
    from app.db import GenerationJob
    Engine, Worker = classes
    service, _, jid = job_env
    with service.Session.begin() as db:
        job = db.get(GenerationJob, jid)
        job.status, job.phase, job.upstream_task_id = 'interrupted', 'awaiting_engine_idle', 'persisted'
    with engine_stub() as (url, _calls):
        second = Worker(service, Engine(url), tmp_path / 'temp')
        class ConcurrentEngine(Engine):
            def query(self, task):
                result = super().query(task)
                second.resolve_interrupted()
                assert second.claim() == jid
                return result
        first = Worker(service, ConcurrentEngine(url), tmp_path / 'temp')
        first.resolve_interrupted()
    with service.Session() as db:
        job = db.get(GenerationJob, jid)
        assert job.lease_owner == second.identity
        assert job.leased_until > time.time()


def test_reconciliation_recovers_completion_between_query_and_idle(classes, job_env, tmp_path):
    from app.db import GenerationJob
    Engine, Worker = classes
    service, client, jid = job_env
    with service.Session.begin() as db:
        job = db.get(GenerationJob, jid)
        job.status, job.phase, job.upstream_task_id = 'interrupted', 'awaiting_engine_idle', 'persisted'
    with engine_stub('completion_race') as (url, calls):
        Worker(service, Engine(url), tmp_path / 'temp').run_once()
    assert client.get(f'/v1/generation/jobs/{jid}').json()['job']['status'] == 'completed'
    assert not any(path == '/release_task' for path, _ in calls)


def test_idle_stats_do_not_abandon_a_known_pending_task(classes, job_env, tmp_path):
    from app.db import GenerationJob
    Engine, Worker = classes
    service, client, jid = job_env
    with service.Session.begin() as db:
        job = db.get(GenerationJob, jid)
        job.status, job.phase, job.upstream_task_id = 'interrupted', 'awaiting_engine_idle', 'persisted'
    with engine_stub('idle_but_pending', result_status=0) as (url, calls):
        worker = Worker(service, Engine(url), tmp_path / 'temp')
        worker.run_once()
        assert worker.claim() is None
    assert client.get(f'/v1/generation/jobs/{jid}').json()['job']['phase'] == 'awaiting_engine_idle'
    assert not any(path == '/release_task' for path, _ in calls)


@pytest.mark.parametrize(('upstream_status', 'expected'), [(1, 'completed'), (2, 'failed')])
def test_reconciliation_reclaims_a_known_terminal_task(classes, job_env, tmp_path, upstream_status, expected):
    from app.db import GenerationJob
    Engine, Worker = classes
    service, client, jid = job_env
    with service.Session.begin() as db:
        job = db.get(GenerationJob, jid)
        job.status, job.phase, job.upstream_task_id = 'interrupted', 'awaiting_engine_idle', 'persisted'
    with engine_stub(result_status=upstream_status) as (url, calls):
        Worker(service, Engine(url), tmp_path / 'temp').run_once()
    assert client.get(f'/v1/generation/jobs/{jid}').json()['job']['status'] == expected
    assert not any(path == '/release_task' for path, _ in calls)


@pytest.mark.parametrize('transport', ['adapter', 'probe'])
def test_local_adapter_never_sends_private_requests_through_a_proxy(classes, monkeypatch, transport):
    Engine, _ = classes
    received = []
    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({'code': 200, 'error': None, 'data': {'task_id': 'proxy-task'}}).encode())
    proxy = ThreadingHTTPServer(('127.0.0.1', 0), Proxy)
    thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv('http_proxy', f'http://127.0.0.1:{proxy.server_port}')
        monkeypatch.setenv('no_proxy', '')
        monkeypatch.setenv('NO_PROXY', '')
        monkeypatch.setattr('urllib.request.proxy_bypass', lambda _host: False)
        with engine_stub() as (url, calls):
            private = {'prompt': 'Private test prompt', 'lyrics': 'Private test lyrics'}
            if transport == 'adapter':
                task = Engine(url).submit(private)
            else:
                import importlib.util
                from pathlib import Path
                spec = importlib.util.spec_from_file_location('probe_proxy_test', Path(__file__).resolve().parents[2] / 'scripts/ai_music_probe.py')
                probe = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(probe)
                task = probe._json(url + '/release_task', private, timeout=2)['task_id']
        assert received == []
        assert task == 'engine-1'
        assert any(path == '/release_task' for path, _ in calls)
    finally:
        proxy.shutdown()
        proxy.server_close()
        thread.join(2)
