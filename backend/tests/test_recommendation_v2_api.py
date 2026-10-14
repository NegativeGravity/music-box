import time
import uuid
from datetime import datetime, timezone
import numpy as np
from sqlalchemy import select
from client import TestClient
from app.db import Event, Track
from test_mvp import env, account, wav_bytes


def listen(client, tid, session, played=60000, occurred_at=None):
    eid=str(uuid.uuid4())
    response=client.post('/v1/events/batch',json={'events':[{'event_id':eid,'track_id':tid,'event_type':'play_end','session_id':session,'played_ms':played,'occurred_at':occurred_at}]})
    assert response.status_code==200
    return eid


def test_real_catalog_and_persistent_and_active_session_profiles(env):
    app,client=env;account(client);svc=app.state.service
    ids=[svc.add_shared_track(f'Track {i}','Artist','International',wav_bytes(),embedding=np.array(v)) for i,v in enumerate([[1.,0.],[0.,1.],[.9,.1],[.1,.9],[.7,.7]])]
    demo=svc.add_shared_track('Demo','Demo artist','Electronic',wav_bytes(),embedding=np.array([1.,0.]),source='demo')
    old=listen(client,ids[0],'previous')
    with svc.Session.begin() as db:
        db.scalar(select(Event).where(Event.event_id==old)).created_at=time.time()-86400
    listen(client,ids[1],'active')
    listen(client,ids[2],'another-tab')
    result=client.get('/v1/recommendations/for-you?limit=20&session_id=active').json()
    assert result['candidate_count']==5
    assert result['scored_count']==5
    assert result['returned_count']==5
    assert result['profile_counts']=={'long_term':2,'session':1}
    assert result['profile_weights']=={'long_term':.7,'session':.3}
    assert result['profile_evidence']['session_scope']=='active_session'
    assert demo not in {t['id'] for t in result['items']}
    assert all('score_components' in t for t in result['items'])
    assert client.get('/v1/recommendations/for-you?session_id='+'x'*101).status_code==422


def test_other_users_private_tracks_and_history_cannot_influence_recommendations(env):
    app,client=env;account(client);svc=app.state.service
    public=svc.add_shared_track('Public','A','Pop',wav_bytes(),embedding=np.array([1.,0.]))
    private=svc.add_shared_track('Private','B','International',wav_bytes(),embedding=np.array([0.,1.]))
    with TestClient(app) as other:
        other_id=account(other)['user']['id']
        with svc.Session.begin() as db:
            track=db.get(Track,private);track.owner_id=other_id;track.visibility='private'
        listen(other,private,'active')
        other.post('/v1/feedback',json={'track_id':private,'signal':'like','event_id':str(uuid.uuid4())})
    result=client.get('/v1/recommendations/for-you?session_id=active').json()
    assert result['candidate_count']==1
    assert result['profile_counts']=={'long_term':0,'session':0}
    assert [t['id'] for t in result['items']]==[public]


def test_buffered_playback_is_history_instead_of_current_session(env):
    app,client=env;account(client);svc=app.state.service
    tid=svc.add_shared_track('Buffered','A','Pop',wav_bytes(),embedding=np.array([1.,0.]))
    stamp=datetime.fromtimestamp(time.time()-7200,timezone.utc).isoformat()
    listen(client,tid,'active',occurred_at=stamp)
    result=client.get('/v1/recommendations/for-you?session_id=active').json()
    assert result['profile_counts']=={'long_term':1,'session':0}
    assert result['profile_weights']=={'long_term':1.,'session':0.}
