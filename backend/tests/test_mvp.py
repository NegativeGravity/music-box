import io
import uuid
import wave

import numpy as np
import pytest
from client import TestClient

from app.main import create_app


@pytest.fixture
def env(tmp_path):
    app = create_app(data_dir=tmp_path, start_worker=False)
    with TestClient(app) as client:
        yield app, client


def account(client, email='a@example.com'):
    response = client.post('/v1/session')
    assert response.status_code == 200, response.text
    client.headers['X-CSRF-Token'] = response.json()['csrf_token']
    return response.json()


def wav_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        tone = (np.sin(np.arange(32000) * 440 * 2 * np.pi / 16000) * 12000).astype('<i2')
        output.writeframes(tone.tobytes())
    return buffer.getvalue()


def test_health_and_guest_sessions_are_distinct(env):
    app, client = env
    assert client.get('/health').json()['status'] == 'ok'
    first = client.post('/v1/session').json()
    second = TestClient(app).post('/v1/session').json()
    assert first['user']['id'] != second['user']['id']


def test_automatic_session_persists_and_requires_csrf(env):
    app, client = env
    user = account(client)
    assert client.get('/v1/session').json()['user']['id'] == user['user']['id']
    assert client.post('/v1/session').json()['user']['id'] == user['user']['id']
    missing = TestClient(app)
    missing.cookies.update(client.cookies)
    assert missing.patch('/v1/preferences', json={'mode': 'focus', 'genre': 'Hip-Hop'}).status_code == 403
    assert client.patch('/v1/preferences', json={'mode': 'focus', 'genre': 'Hip-Hop'}).status_code == 200
    assert client.post('/v1/auth/login', json={}).status_code == 404
    assert client.post('/v1/session', headers={'Origin': 'https://evil.example'}).status_code == 403


def test_upload_processing_range_and_cross_user_denied(env):
    app, client = env
    account(client)
    upload = client.post('/v1/uploads', files={'file': ('tone.wav', wav_bytes(), 'audio/wav')}, data={'title': 'My track', 'artist': 'Me', 'genre': 'Electronic'}, headers={'Idempotency-Key': 'same-upload'})
    assert upload.status_code == 202, upload.text
    track_id = upload.json()['track']['id']
    app.state.service.process_jobs()
    tracks = client.get('/v1/library/tracks').json()['tracks']
    assert tracks[0]['playback_status'] == 'ready'
    stream = client.get(f'/v1/tracks/{track_id}/stream', headers={'Range': 'bytes=0-99'})
    assert stream.status_code == 206
    assert len(stream.content) == 100
    assert stream.headers['content-range'].startswith('bytes 0-99/')
    assert client.get(f'/v1/tracks/{track_id}/stream', headers={'Range': 'bytes=999999999-'}).status_code == 416
    again = client.post('/v1/uploads', files={'file': ('tone.wav', wav_bytes(), 'audio/wav')}, data={'title': 'My track', 'genre': 'Electronic'}, headers={'Idempotency-Key': 'same-upload'})
    assert again.json()['track']['id'] == track_id
    other = TestClient(app)
    account(other, 'b@example.com')
    assert other.get('/v1/library/tracks').json()['tracks'] == []
    assert other.get(f'/v1/tracks/{track_id}/stream').status_code == 404
    assert other.get(f'/v1/recommendations/similar/{track_id}').status_code == 404
    assert other.post('/v1/feedback', json={'track_id': track_id, 'signal': 'like', 'event_id': str(uuid.uuid4())}).status_code == 404


def test_corrupt_upload_and_invalid_genre(env):
    app, client = env
    account(client)
    bad = client.post('/v1/uploads', files={'file': ('bad.wav', b'not audio', 'audio/wav')}, data={'genre': 'Rock'})
    assert bad.status_code == 202
    app.state.service.process_jobs()
    result = client.get('/v1/library/tracks').json()['tracks'][0]
    assert result['playback_status'] == 'failed'
    assert client.get(f"/v1/tracks/{result['id']}/stream").status_code == 409
    assert client.patch('/v1/preferences', json={'mode': 'focus', 'genre': 'invented'}).status_code == 422


def test_balanced_focus_feedback_and_event_attribution(env):
    app, client = env
    account(client)
    svc = app.state.service
    for i in range(30):
        svc.add_shared_track(f'Primary {i}', f'Artist {i}', 'Hip-Hop', wav_bytes(), embedding=np.array([1., .02 * i, .01]))
    for i in range(15):
        svc.add_shared_track(f'Neighbor {i}', f'Neighbor artist {i}', 'Electronic', wav_bytes(), embedding=np.array([.8, .3 + .01 * i, .1]))
    client.patch('/v1/preferences', json={'genre': 'Hip-Hop', 'mode': 'balanced'})
    rec = client.get('/v1/recommendations/for-you?limit=20&seed=42').json()
    assert len(rec['items']) == 20
    assert rec['candidate_count'] == 45
    assert rec['scored_count'] == 45
    assert rec['strategy'] == 'cold_start_diverse_audio'
    track_id = rec['items'][0]['id']
    evt = {'event_id': str(uuid.uuid4()), 'track_id': track_id, 'signal': 'dislike'}
    assert client.post('/v1/feedback', json=evt).status_code == 200
    assert client.post('/v1/feedback', json=evt).status_code == 200
    next_rec = client.get('/v1/recommendations/for-you?limit=20&seed=42').json()
    assert track_id not in [x['id'] for x in next_rec['items']]
    changed = {**evt, 'signal': 'like'}
    assert client.post('/v1/feedback', json=changed).status_code == 409
    client.patch('/v1/preferences', json={'mode': 'focus'})
    focus = client.get('/v1/recommendations/for-you?limit=20').json()
    assert len(focus['items']) == 20
    assert focus['candidate_count'] == 44
    assert track_id not in [x['id'] for x in focus['items']]
    event = {'event_id': str(uuid.uuid4()), 'track_id': track_id, 'request_id': rec['request_id'], 'event_type': 'exposure'}
    assert client.post('/v1/events/batch', json={'events': [event]}).status_code == 200
    assert client.post('/v1/events/batch', json={'events': [event]}).json()['inserted'] == 0
    other = TestClient(app)
    account(other, 'b@example.com')
    assert other.post('/v1/events/batch', json={'events': [{**event, 'event_id': str(uuid.uuid4())}]}).status_code == 403


def test_similar_audio_ungated_and_playlist_persists(env):
    app, client = env
    account(client)
    svc = app.state.service
    seed = svc.add_shared_track('Seed', 'A', 'Hip-Hop', wav_bytes(), embedding=np.array([1., 0., 0.]))
    close = svc.add_shared_track('Closest', 'B', 'Electronic', wav_bytes(), embedding=np.array([.99, .01, 0.]))
    svc.add_shared_track('Far', 'C', 'Hip-Hop', wav_bytes(), embedding=np.array([0., 1., 0.]))
    result = client.get(f'/v1/recommendations/similar/{seed}?limit=2').json()
    assert result['items'][0]['id'] == close
    assert seed not in [t['id'] for t in result['items']]
    playlist = client.post('/v1/playlists', json={'title': 'My mix'}).json()
    assert client.post(f"/v1/playlists/{playlist['id']}/tracks", json={'track_id': close}).status_code == 200
    assert client.get('/v1/playlists').json()['playlists'][0]['track_ids'] == [close]


def test_delete_revokes_stream_and_library(env):
    app, client = env
    account(client)
    response = client.post('/v1/uploads', files={'file': ('tone.wav', wav_bytes(), 'audio/wav')}, data={'genre': 'Pop'})
    tid = response.json()['track']['id']
    app.state.service.process_jobs()
    assert client.delete(f'/v1/tracks/{tid}').status_code == 200
    assert client.get(f'/v1/tracks/{tid}/stream').status_code == 404
    assert client.get('/v1/library/tracks').json()['tracks'] == []


def test_embedding_failure_preserves_playable_audio(env):
    app,client=env
    account(client)
    signal=np.zeros(40*16000,dtype='<i2')
    signal[12*16000:13*16000]=(12000*np.sin(np.arange(16000)*2*np.pi*440/16000)).astype('<i2')
    buf=io.BytesIO()
    with wave.open(buf,'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(signal.tobytes())
    tid=client.post('/v1/uploads',files={'file':('sparse.wav',buf.getvalue(),'audio/wav')},data={'genre':'Instrumental'}).json()['track']['id']
    app.state.service.process_jobs()
    result=client.get('/v1/library/tracks').json()['tracks'][0]
    assert result['playback_status']=='ready'
    assert result['embedding_status']=='unavailable'
    assert client.get(f'/v1/tracks/{tid}/stream').status_code==200


def test_worker_exhausted_lease_terminal_and_decode_priority(env,monkeypatch):
    import time
    from app.db import Job,Track
    from sqlalchemy import select
    app,client=env;account(client)
    svc=app.state.service
    tid=client.post('/v1/uploads',files={'file':('tone.wav',wav_bytes(),'audio/wav')},data={'genre':'Pop'}).json()['track']['id']
    with svc.Session.begin() as db:
        job=db.scalar(select(Job).where(Job.track_id==tid));job.status='processing';job.attempts=3;job.leased_until=time.time()-1
        db.get(Track,tid).playback_status='processing'
    svc.process_jobs(1)
    with svc.Session() as db:
        assert db.scalar(select(Job).where(Job.track_id==tid)).status=='failed'
        assert db.get(Track,tid).playback_status=='failed'
    shared=svc.add_shared_track('Audio','A','Pop',wav_bytes(),embedding=np.array([1.,0.]))
    with svc.Session.begin() as db:db.add(Job(track_id=shared,stage='clap'))
    uploaded=client.post('/v1/uploads',files={'file':('new.wav',wav_bytes(),'audio/wav')},data={'genre':'Pop'}).json()['track']['id']
    monkeypatch.setattr('app.service.clap_embedding',lambda *args: (_ for _ in ()).throw(RuntimeError('External model disabled in test')))
    svc.process_jobs(1)
    with svc.Session() as db:assert db.get(Track,uploaded).playback_status=='ready'


def test_concurrent_event_and_upload_retries_are_idempotent(env):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from sqlalchemy import event as sql_event
    app,client=env
    uid=account(client)['user']['id'];svc=app.state.service
    tid=svc.add_shared_track('Track','A','Pop',wav_bytes(),embedding=np.array([1.,0.]))
    barrier=threading.Barrier(2)
    seen=set()
    def synchronize(_conn,_cursor,statement,_params,_context,_many):
        thread=threading.get_ident()
        if 'FROM events' in statement and thread not in seen:
            seen.add(thread);barrier.wait(timeout=5)
    sql_event.listen(svc.engine,'after_cursor_execute',synchronize)
    payload={'event_id':'retry-event-0001','track_id':tid,'event_type':'exposure'}
    def record():
        with svc.Session.begin() as db:return svc.record_event(db,uid,payload['event_id'],'exposure',tid,payload)
    try:
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:record(),range(2)))
        assert sorted(results)==[False,True]
    finally:sql_event.remove(svc.engine,'after_cursor_execute',synchronize)
    barrier=threading.Barrier(2);seen=set()
    def synchronize_upload(_conn,_cursor,statement,_params,_context,_many):
        thread=threading.get_ident()
        if 'tracks.upload_key =' in statement and thread not in seen:
            seen.add(thread);barrier.wait(timeout=5)
    sql_event.listen(svc.engine,'after_cursor_execute',synchronize_upload)
    def upload():return svc.upload(uid,wav_bytes(),'test.wav','Retry','Me','Pop','retry-upload-1')['id']
    try:
        before=set(svc.assets.iterdir())
        with ThreadPoolExecutor(2) as pool:ids=list(pool.map(lambda _:upload(),range(2)))
        assert ids[0]==ids[1]
        assert len(set(svc.assets.iterdir())-before)==1
    finally:sql_event.remove(svc.engine,'after_cursor_execute',synchronize_upload)


def test_deleted_seed_is_removed_from_preferences(env):
    app,client=env;account(client)
    tid=client.post('/v1/uploads',files={'file':('tone.wav',wav_bytes(),'audio/wav')},data={'genre':'Pop'}).json()['track']['id']
    assert client.patch('/v1/preferences',json={'seed_track_ids':[tid]}).status_code==200
    assert client.delete(f'/v1/tracks/{tid}').status_code==200
    assert client.get('/v1/session').json()['user']['preferences']['seed_track_ids']==[]


def test_batch_retains_valid_events_when_one_track_is_gone(env):
    app,client=env;account(client)
    tid=app.state.service.add_shared_track('Track','A','Pop',wav_bytes(),embedding=np.array([1.,0.]))
    response=client.post('/v1/events/batch',json={'events':[{'event_id':str(uuid.uuid4()),'track_id':999999,'event_type':'exposure'},{'event_id':str(uuid.uuid4()),'track_id':tid,'event_type':'exposure'}]})
    assert response.status_code==200
    assert response.json()['inserted']==1
    assert len(response.json()['accepted'])==1
    assert response.json()['rejected'][0]['status']==404


def test_backup_restore_preserves_library_and_audio(env,tmp_path):
    import subprocess,sys
    from pathlib import Path
    app,client=env;account(client)
    tid=client.post('/v1/uploads',files={'file':('tone.wav',wav_bytes(),'audio/wav')},data={'genre':'Pop'}).json()['track']['id']
    app.state.service.process_jobs()
    original=client.get(f'/v1/tracks/{tid}/stream').content
    archive=tmp_path/'backup.zip';destination=tmp_path/'restored'
    subprocess.run([sys.executable,'scripts/backup.py','--data',str(app.state.service.root),'--output',str(archive)],check=True,capture_output=True)
    subprocess.run([sys.executable,'scripts/restore.py','--archive',str(archive),'--destination',str(destination)],check=True,capture_output=True)
    restored=create_app(data_dir=destination,start_worker=False)
    with TestClient(restored) as other:
        other.cookies.update(client.cookies)
        assert other.get('/v1/library/tracks').json()['tracks'][0]['id']==tid
        assert other.get(f'/v1/tracks/{tid}/stream').content==original
