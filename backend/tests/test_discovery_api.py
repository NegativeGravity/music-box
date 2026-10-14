import uuid
import numpy as np
from sqlalchemy import select
from app.db import Track, Playlist
from client import TestClient
from test_mvp import env, account, wav_bytes


def seed(svc):
    return [svc.add_shared_track(f'Track {i}','Artist','Pop',wav_bytes(),embedding=np.array(v)) for i,v in enumerate([[1.,0.],[.99,.1],[-1.,0.],[-.99,.1]])]


def like(client,tid):
    assert client.post('/v1/feedback',json=dict(track_id=tid,signal='like',event_id=str(uuid.uuid4()))).status_code==200


def test_discovery_separates_interests_and_saves_a_private_ordered_mix(env):
    app,client=env;auth=account(client);ids=seed(app.state.service)
    for tid in [ids[0],ids[2]]:like(client,tid)
    response=client.get(f'/v1/recommendations/discovery?session_id=active&current_id={ids[0]}&seed=42')
    assert response.status_code==200
    result=response.json()
    assert len(result['interest_groups'])==2
    assert result['candidate_count']==4
    assert result['next_items'][0]['id']==ids[1]
    group=result['interest_groups'][0]
    saved=client.post('/v1/playlists/from-recommendation',json=dict(request_id=result['request_id'],group_id=group['id']))
    assert saved.status_code==201,saved.text
    assert saved.json()['track_ids']==[t['id'] for t in group['items']]
    assert client.get('/v1/playlists').json()['playlists'][0]['id']==saved.json()['id']
    assert auth['user']['preferences']['smart_autoplay_enabled'] is True
    assert client.patch('/v1/preferences',json={'smart_autoplay_enabled':False}).json()['preferences']['smart_autoplay_enabled'] is False


def test_foreign_requests_tracks_and_history_are_isolated(env):
    app,client=env;account(client);svc=app.state.service;ids=seed(svc)
    with TestClient(app) as other:
        oid=account(other)['user']['id']
        with svc.Session.begin() as db:
            asset=db.get(Track,ids[0]);asset.owner_id=oid;asset.visibility='private'
        like(other,ids[0])
        response=other.get('/v1/recommendations/discovery?session_id=active').json()
        group=response['interest_groups'][0]
        assert client.get(f'/v1/recommendations/discovery?current_id={ids[0]}').status_code==404
        assert client.post('/v1/playlists/from-recommendation',json=dict(request_id=response['request_id'],group_id=group['id'])).status_code==404
    result=client.get('/v1/recommendations/discovery').json()
    assert result['interest_groups']==[]
    assert ids[0] not in {t['id'] for t in result['items']}


def test_save_revalidates_the_entire_mix_before_creating_a_playlist(env):
    app,client=env;account(client);svc=app.state.service;ids=seed(svc);like(client,ids[0])
    result=client.get('/v1/recommendations/discovery').json();group=result['interest_groups'][0]
    with svc.Session.begin() as db:db.get(Track,group['items'][-1]['id']).deleted=True
    assert client.post('/v1/playlists/from-recommendation',json=dict(request_id=result['request_id'],group_id=group['id'])).status_code==404
    with svc.Session() as db:assert db.scalar(select(Playlist)) is None
