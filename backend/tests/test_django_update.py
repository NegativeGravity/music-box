from client import TestClient
from app.main import create_app


def test_django_backend_without_login(tmp_path):
    app = create_app(data_dir=tmp_path, start_worker=False)
    with TestClient(app) as client:
        assert client.get('/health').json()['framework'] == 'django'
        response = client.post('/v1/session')
        assert 'HttpOnly' in response.headers['set-cookie']
        assert 'Max-Age=31536000' in response.headers['set-cookie']
        session = response.json()
        assert session['user']['name'] == 'Listener'
        assert client.get('/v1/session').json()['user']['id'] == session['user']['id']
        for route in ('login', 'register', 'logout', 'guest', 'me'):
            assert client.post(f'/v1/auth/{route}').status_code == 404
        client.headers['X-CSRF-Token'] = session['csrf_token']
        error = client.patch('/v1/preferences', json={'genre': 'invalid'}).json()['detail']
        assert error.isascii()


def test_django_range_suffix_and_validation(tmp_path):
    import numpy as np
    from test_mvp import wav_bytes
    app = create_app(data_dir=tmp_path)
    with TestClient(app) as client:
        state = client.post('/v1/session').json()
        client.headers['X-CSRF-Token'] = state['csrf_token']
        tid = app.state.service.add_shared_track('Range fixture', 'Artist', 'Pop', wav_bytes(), embedding=np.array([1., 0.]))
        full = client.get(f'/v1/tracks/{tid}/stream').content
        suffix = client.get(f'/v1/tracks/{tid}/stream', headers={'Range': 'bytes=-12'})
        assert suffix.status_code == 206
        assert suffix.content == full[-12:]
        assert client.get(f'/v1/tracks/{tid}/stream', headers={'Range': 'bytes=10-'}).content == full[10:]
        for invalid in ('bytes=-0', 'bytes=50-10', 'bytes=0-1,5-6', 'not-a-range'):
            response = client.get(f'/v1/tracks/{tid}/stream', headers={'Range': invalid})
            assert response.status_code == 416
            assert response.headers['content-range'] == f'bytes */{len(full)}'
        assert client.patch('/v1/preferences', content=b'{', headers={'Content-Type': 'application/json'}).status_code == 422
        for query in ('limit=0', 'limit=51', 'limit=abc', 'seed=-1', 'seed=2147483648'):
            assert client.get(f'/v1/recommendations/for-you?{query}').status_code == 422
        assert client.put('/v1/library/tracks').status_code == 405
        assert client.get('/v1/categories').json()['categories'][0]['label'].isascii()


def test_personalization_is_opt_in_and_saved_for_existing_sessions(tmp_path):
    from app.db import User
    app = create_app(data_dir=tmp_path)
    with TestClient(app) as client:
        state = client.post('/v1/session').json()
        assert state['user']['preferences']['personalization_enabled'] is False
        client.headers['X-CSRF-Token'] = state['csrf_token']
        # Profiles saved before this feature must also default to browsing.
        with app.state.service.Session.begin() as db:
            user = db.get(User, state['user']['id'])
            user.preferences = {'genre': 'Pop', 'mode': 'focus', 'seed_track_ids': []}
        assert client.get('/v1/session').json()['user']['preferences']['personalization_enabled'] is False
        response = client.patch('/v1/preferences', json={'personalization_enabled': True})
        assert response.status_code == 200
        assert response.json()['preferences']['personalization_enabled'] is True
        assert client.post('/v1/session').json()['user']['preferences']['personalization_enabled'] is True
        response = client.patch('/v1/preferences', json={'personalization_enabled': False})
        assert response.json()['preferences'] == {'genre': 'Pop', 'mode': 'focus', 'seed_track_ids': [], 'personalization_enabled': False, 'smart_autoplay_enabled': True}
