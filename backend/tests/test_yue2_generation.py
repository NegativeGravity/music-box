import importlib
import io
import wave
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
from sqlalchemy import select
from app.main import create_app
from app.db import GenerationJob, Track
from client import TestClient


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('MUSICBOX_AI_ENGINE', 'yue2')
    # Native inference is replaced by a tiny synthesized fixture process; host load must not determine the test.
    import psutil
    from types import SimpleNamespace
    monkeypatch.setattr(psutil, 'virtual_memory', lambda: SimpleNamespace(available=24 * 1024**3))
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    client.headers['X-CSRF-Token'] = client.post('/v1/session').json()['csrf_token']
    yield app, client
    client.close()


def submit(client, key='one', **changes):
    return client.post('/v1/generation/jobs', json={'prompt': 'Quiet piano', 'mode': 'instrumental',
                       'seed': 42, **changes}, headers={'Idempotency-Key': key})


def test_yue2_creates_eight_durable_candidates_with_distinct_seeds(env):
    app, client = env
    response = submit(client)
    assert response.status_code == 202, response.text
    job = response.json()['job']
    assert len(job.get('candidates', [])) == 8
    assert [c['seed'] for c in job['candidates']] == list(range(42, 50))
    assert job['request']['engine'] == 'yue2'
    assert job['request']['duration_seconds'] == 30
    assert submit(client).json()['job']['id'] == job['id']
    assert submit(client, prompt='Changed').status_code == 409
    assert len(client.get('/v1/generation/jobs').json()['jobs']) == 1


def completed(app, client, indices=(1, 2)):
    candidate_module = importlib.import_module('app.db')
    assert hasattr(candidate_module, 'GenerationCandidate'), 'Candidates require durable persistence'
    Candidate = candidate_module.GenerationCandidate
    jid = submit(client).json()['job']['id']
    service = app.state.service
    for index in indices:
        data = io.BytesIO()
        with wave.open(data, 'wb') as wav:
            wav.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            wav.writeframes(bytes([index, 2]) * 8000)
        key = f'generation/{jid}/candidate-{index}/audio.wav'
        path = service.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.getvalue())
        with service.Session.begin() as db:
            item = db.scalar(select(Candidate).where(Candidate.generation_id == jid, Candidate.index == index))
            item.status, item.output_key = 'completed', key
            item.diagnostics = {'duration_seconds': 1.0}
            job = db.get(GenerationJob, jid)
            job.status, job.output_key = 'completed', key if index == 1 else job.output_key
    return jid


def test_candidate_selection_save_and_preview_refer_to_the_same_audio(env):
    app, client = env
    jid = completed(app, client)
    base = f'/v1/generation/jobs/{jid}'
    response = client.post(base + '/candidates/2/select')
    assert response.status_code == 200
    assert response.json()['job']['selected_candidate_index'] == 2
    first = client.post(base + '/save').json()['track']
    assert client.post(base + '/save').json()['track']['id'] == first['id']
    assert client.get(base + '/audio').content == client.get(base + '/candidates/2/audio').content
    assert client.post(base + '/candidates/1/select').status_code == 200
    second = client.post(base + '/save').json()['track']
    assert first['id'] != second['id']
    with app.state.service.Session() as db:
        assert 'YuE2-3B-Q8' in db.get(Track, first['id']).provenance
        assert db.get(Track, first['id']).visibility == 'private'


def test_foreign_candidates_selection_cancel_and_audio_are_hidden(env):
    app, client = env
    jid = completed(app, client)
    with TestClient(app) as other:
        other.headers['X-CSRF-Token'] = other.post('/v1/session').json()['csrf_token']
        base = f'/v1/generation/jobs/{jid}'
        assert other.get(base + '/candidates/1/audio').status_code == 404
        assert other.post(base + '/candidates/1/select').status_code == 404
        assert other.post(base + '/cancel').status_code == 404
    assert client.post(f'/v1/generation/jobs/{jid}/candidates/8/select').status_code == 409


def test_cancellation_preserves_completed_candidates_but_stops_remaining_work(env):
    app, client = env
    jid = completed(app, client, (1,))
    with app.state.service.Session.begin() as db:
        db.get(GenerationJob, jid).status = 'running'
    base = f'/v1/generation/jobs/{jid}'
    result = client.post(base + '/cancel')
    assert result.status_code == 200
    job = result.json()['job']
    assert job['status'] == 'cancelled'
    assert job['candidates'][0]['status'] == 'completed'
    assert all(c['status'] == 'cancelled' for c in job['candidates'][1:])
    assert client.get(base + '/candidates/1/audio').status_code == 200
    assert client.post(base + '/save').status_code == 200


def test_unsupported_primary_audio_reference_is_explicit_and_not_silently_ignored(env):
    _, client = env
    response = submit(client, reference_track_ids=[1], primary_reference_track_id=1)
    assert response.status_code == 422
    assert 'audio reference' in response.json()['detail'].lower()
    assert client.get('/v1/generation/jobs').json()['jobs'] == []


class PreparedRuntime:
    root = Path('/unused-ML-fixture')
    def health(self):
        return {'models_initialized': True}


def worker_module():
    assert importlib.util.find_spec('app.yue2_worker') is not None, 'Durable YuE2 worker is missing'
    return importlib.import_module('app.yue2_worker')


def fixture_launcher(config):
    return subprocess.Popen([sys.executable, str(Path(__file__).parent / 'fixtures/yue2_fake_runner.py'), str(config)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def test_worker_renders_eight_real_fixture_outputs_and_keeps_observed_durations(env):
    app, client = env
    Worker = worker_module().Yue2Worker
    jid = submit(client).json()['job']['id']
    worker = Worker(app.state.service, PreparedRuntime(), launcher=fixture_launcher)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        worker.run_once()
        job = client.get(f'/v1/generation/jobs/{jid}').json()['job']
        if job['status'] == 'completed':
            break
        time.sleep(.05)
    assert job['status'] == 'completed', job
    assert job['progress']['completed'] == 8
    assert {c['duration_seconds'] for c in job['candidates']} == {2.0}
    for index in range(1, 9):
        assert client.get(f'/v1/generation/jobs/{jid}/candidates/{index}/audio', headers={'Range': 'bytes=0-99'}).status_code == 206


def test_restarted_worker_adopts_existing_runner_without_duplicate_launch(env):
    app, client = env
    Worker = worker_module().Yue2Worker
    jid = submit(client).json()['job']['id']
    first = Worker(app.state.service, PreparedRuntime(), launcher=fixture_launcher)
    first.run_once()
    identity = json.loads((first.folder(jid, 1) / 'runner-identity.json').read_text())
    with app.state.service.Session.begin() as db:
        job = db.get(GenerationJob, jid)
        assert job.candidates[0].process_id == identity['pid'], 'Track the interpreter, not its Windows venv launcher'
        job.leased_until = 0
    def duplicate(config):
        pytest.fail('A surviving runner was launched twice')
    second = Worker(app.state.service, PreparedRuntime(), launcher=duplicate)
    second.run_once()
    with app.state.service.Session() as db:
        assert db.get(GenerationJob, jid).candidates[0].status in ('rendering', 'completed')
    client.post(f'/v1/generation/jobs/{jid}/cancel')
    second.run_once()


def test_reused_or_unrelated_pid_is_not_adopted_or_killed(env):
    import psutil
    app, client = env
    Worker = worker_module().Yue2Worker
    jid = submit(client).json()['job']['id']
    with app.state.service.Session.begin() as db:
        job = db.get(GenerationJob, jid)
        job.status = 'running'
        candidate = job.candidates[0]
        candidate.status, candidate.process_id = 'rendering', os.getpid()
        candidate.process_started_at = psutil.Process().create_time()
    worker = Worker(app.state.service, PreparedRuntime(), launcher=lambda _: pytest.fail('Interrupted candidate was resubmitted'))
    worker.run_once()
    with app.state.service.Session() as db:
        assert db.get(GenerationJob, jid).candidates[0].status == 'failed'
    assert psutil.Process(os.getpid()).is_running()
