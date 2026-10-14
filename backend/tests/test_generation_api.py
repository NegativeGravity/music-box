import io
import json
import wave

import pytest
from client import TestClient
from app.main import create_app


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('MUSICBOX_AI_ENGINE', 'ace-step')
    app = create_app(data_dir=tmp_path)
    client = TestClient(app)
    session = client.post('/v1/session').json()
    client.headers['X-CSRF-Token'] = session['csrf_token']
    yield app, client, session['user']['id']
    client.close()


def submit(client, key='one', **changes):
    return client.post('/v1/generation/jobs', json={
        'prompt': 'Warm electronic music', 'mode': 'instrumental', **changes},
        headers={'Idempotency-Key': key})


def test_create_and_idempotency_preserve_seed_and_payload(env):
    _, client, _ = env
    first = submit(client)
    assert first.status_code == 202, first.text
    job = first.json()['job']
    again = submit(client)
    assert again.status_code == 202
    assert again.json()['job']['id'] == job['id']
    assert again.json()['job']['request']['seed'] == job['request']['seed']
    assert job['status'] == 'queued'
    assert job['request']['duration_seconds'] == 20
    assert submit(client, prompt='Another prompt').status_code == 409


def test_jobs_survive_new_wsgi_instance(env, tmp_path):
    _, client, _ = env
    first = submit(client)
    assert first.status_code == 202
    other = TestClient(create_app(data_dir=tmp_path))
    other.cookies.update(client.cookies)
    jobs = other.get('/v1/generation/jobs').json()['jobs']
    assert jobs[0]['id'] == first.json()['job']['id']
    other.close()


def test_identity_csrf_and_key_required(env):
    app, client, _ = env
    assert submit(client).status_code == 202
    other = TestClient(app)
    assert other.post('/v1/generation/jobs', json={'prompt': 'p', 'mode': 'instrumental'}).status_code == 401
    other.cookies.update(client.cookies)
    assert submit(other).status_code == 403
    assert client.post('/v1/generation/jobs', json={'prompt': 'p', 'mode': 'instrumental'}).status_code == 422


@pytest.mark.parametrize('changes', [
    {'mode': 'vocal', 'lyrics': ''}, {'prompt': '   '}, {'language': 'invalid'},
    {'duration_seconds': 300}, {'reference_track_ids': [1, 2, 3, 4]},
    {'primary_reference_track_id': 9}, {'seed': -1}, {'mode': 'unknown'}])
def test_invalid_generation_request_is_rejected(env, changes):
    _, client, _ = env
    assert submit(client, **changes).status_code == 422


def test_foreign_job_preview_and_save_are_private(env):
    app, client, _ = env
    first = submit(client)
    assert first.status_code == 202
    job_id = first.json()['job']['id']
    other = TestClient(app)
    auth = other.post('/v1/session').json()
    other.headers['X-CSRF-Token'] = auth['csrf_token']
    assert other.get('/v1/generation/jobs').json()['jobs'] == []
    for suffix in ['', '/audio']:
        assert other.get('/v1/generation/jobs/' + job_id + suffix).status_code == 404
    assert other.post('/v1/generation/jobs/' + job_id + '/save').status_code == 404
    assert client.get('/v1/generation/jobs/' + job_id + '/audio').status_code == 409
    assert client.post('/v1/generation/jobs/' + job_id + '/save').status_code == 409


def test_reference_permission_and_immutable_snapshot(env):
    from app.db import Track
    app, client, uid = env
    service = app.state.service
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav:
        wav.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\x01\x02' * 8000)
    track = service.upload(uid, buffer.getvalue(), 'reference.wav', 'My reference', '', 'Electronic', 'ref')
    tid = track['id']
    result = submit(client, reference_track_ids=[tid], primary_reference_track_id=tid)
    assert result.status_code == 202, result.text
    request = result.json()['job']['request']
    assert request['references'][0]['title'] == 'My reference'
    with service.Session.begin() as db:
        item = db.get(Track, tid)
        item.title = 'Changed title'
        item.can_analyze = False
    assert client.get('/v1/generation/jobs/' + result.json()['job']['id']).json()['job']['request']['references'][0]['title'] == 'My reference'
    assert submit(client, key='blocked', reference_track_ids=[tid]).status_code == 422
    with service.Session.begin() as db:
        db.get(Track, tid).deleted = True
    assert submit(client, key='deleted', reference_track_ids=[tid]).status_code == 404
    other = TestClient(app)
    other.headers['X-CSRF-Token'] = other.post('/v1/session').json()['csrf_token']
    assert submit(other, key='foreign', reference_track_ids=[tid]).status_code == 404


def test_completed_preview_and_concurrent_save_are_idempotent(env):
    import concurrent.futures
    from app.db import GenerationJob, Track
    app, client, uid = env
    service = app.state.service
    response = submit(client)
    assert response.status_code == 202
    jid = response.json()['job']['id']
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\x01\x02' * 8000)
    key = f'generation/{jid}/output.wav'
    service.path(key).parent.mkdir(parents=True, exist_ok=True)
    service.path(key).write_bytes(output.getvalue())
    with service.Session.begin() as db:
        job = db.get(GenerationJob, jid)
        job.status, job.output_key = 'completed', key
    preview = client.get(f'/v1/generation/jobs/{jid}/audio', headers={'Range': 'bytes=0-99'})
    assert preview.status_code == 206
    assert preview.headers['content-type'].startswith('audio/wav')
    def save():
        with TestClient(app) as caller:
            caller.cookies.update(client.cookies)
            caller.headers.update(client.headers)
            return caller.post(f'/v1/generation/jobs/{jid}/save')
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(lambda _: save(), range(2)))
    assert all(r.status_code == 200 for r in answers), [r.text for r in answers]
    assert answers[0].json()['track']['id'] == answers[1].json()['track']['id']
    with service.Session() as db:
        from sqlalchemy import select
        tracks = db.scalars(select(Track).where(Track.owner_id == uid)).all()
        assert len(tracks) == 1
        assert tracks[0].source == 'generated'
        assert tracks[0].visibility == 'private'
    assert service.path(key).read_bytes() == output.getvalue()


def test_failed_save_rolls_back_track_and_cleans_new_copy(env):
    from sqlalchemy import event, select
    from app.db import GenerationJob, Track
    from app.music_generation import MusicGenerationService
    app, client, uid = env
    service = app.state.service
    result = submit(client)
    assert result.status_code == 202
    jid = result.json()['job']['id']
    key = f'generation/{jid}/output.wav'
    source = service.path(key)
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b'retained original output')
    with service.Session.begin() as db:
        job = db.get(GenerationJob, jid)
        job.status, job.output_key = 'completed', key
    def fail_commit(connection):
        raise RuntimeError('simulated database commit failure')
    event.listen(service.engine, 'commit', fail_commit)
    try:
        with pytest.raises(RuntimeError, match='commit failure'):
            MusicGenerationService(service).save(uid, jid)
    finally:
        event.remove(service.engine, 'commit', fail_commit)
    assert list(service.assets.glob('*.wav')) == []
    assert source.read_bytes() == b'retained original output'
    with service.Session() as db:
        assert db.scalar(select(Track).where(Track.owner_id == uid)) is None
        assert db.get(GenerationJob, jid).saved_track_id is None
