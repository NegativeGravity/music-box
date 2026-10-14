import hashlib
import json
import os
from pathlib import Path
import secrets
import time
import uuid
from .errors import APIError as HTTPException
import numpy as np
from sqlalchemy import case, or_, select, update
from .db import database, Event, Feedback, Job, Playlist, Recommendation, Session, Track, User
from .audio import CLAP_VERSION, SPECTRAL_VERSION, clap_embedding, decode, spectral_embedding, transcode
from .recommendation import GENRES
from .recommendation_v2 import recommend
from .taste_profiles import build_profiles
from .taste_discovery import discover

COLORS = ['#689cff', '#a78bfa', '#f06a81', '#526fe8', '#be83ee', '#df536e', '#819aff', '#9174db']


class Service:
    def __init__(self, data_dir, database_url=None):
        self.root = Path(data_dir).resolve()
        self.assets = self.root / 'assets'
        self.assets.mkdir(parents=True, exist_ok=True)
        self.engine, self.Session = database(database_url or f'sqlite:///{self.root / "musicbox.db"}')

    def path(self, key):
        path = (self.assets / key).resolve()
        if not path.is_relative_to(self.assets):
            raise HTTPException(400, 'Invalid asset path')
        return path

    def user_for_cookie(self, token):
        if not token:
            return None
        with self.Session() as db:
            session = db.get(Session, hashlib.sha256(token.encode()).hexdigest())
            if session is None or session.expires < time.time():
                return None
            user = db.get(User, session.user_id)
            return (user, session.csrf) if user else None

    def new_session(self, user_id):
        raw, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.Session.begin() as db:
            db.add(Session(id=hashlib.sha256(raw.encode()).hexdigest(), user_id=user_id, csrf=csrf, expires=time.time()+365*86400))
        return raw, csrf

    def renew_session(self, token):
        with self.Session.begin() as db:
            session = db.get(Session, hashlib.sha256(token.encode()).hexdigest())
            if session:
                session.expires = time.time() + 365*86400

    @staticmethod
    def public_user(user):
        return dict(id=user.id, name='Listener', preferences={'personalization_enabled': False, 'smart_autoplay_enabled': True, **user.preferences}, profile_version=user.profile_version)

    @staticmethod
    def eligible(user_id):
        return (Track.deleted.is_(False), Track.can_play.is_(True), or_(Track.visibility == 'shared', Track.owner_id == user_id))

    def get_track(self, db, tid, uid):
        track = db.scalar(select(Track).where(Track.id == tid, *self.eligible(uid)))
        if not track:
            raise HTTPException(404, 'Track is not available')
        return track

    @staticmethod
    def track_dict(track, feedback=None, embedding=False):
        result = {k: getattr(track, k) for k in ['id','title','artist','genre','source','visibility','duration','playback_status','embedding_status','embedding_model','color','created_at','error']}
        result['feedback'] = feedback
        if embedding:
            result['embedding'] = track.embedding
        return result

    def library(self, uid):
        with self.Session() as db:
            states = {r.track_id: r.signal for r in db.scalars(select(Feedback).where(Feedback.user_id == uid))}
            tracks = db.scalars(select(Track).where(*self.eligible(uid)).order_by(Track.created_at.desc())).all()
            return [self.track_dict(t, states.get(t.id)) for t in tracks]

    def add_shared_track(self, title, artist, genre, audio_bytes, embedding=None, source='catalog', provenance='Shared catalog supplied by operator'):
        key = f'{uuid.uuid4()}' + ('.wav' if audio_bytes[:4]==b'RIFF' else '.mp3')
        self.path(key).write_bytes(audio_bytes)
        with self.Session.begin() as db:
            track = Track(title=title, artist=artist, genre=genre, original_key=key, stream_key=key if embedding is not None else None,
                          sha256=hashlib.sha256(audio_bytes).hexdigest(), size=len(audio_bytes), visibility='shared', source=source, provenance=provenance,
                          color=COLORS[int(hashlib.sha256(title.encode()).hexdigest()[:2],16)%len(COLORS)],
                          embedding=np.asarray(embedding).tolist() if embedding is not None else None,
                          embedding_model='fixture' if embedding is not None else None,
                          playback_status='ready' if embedding is not None else 'queued', embedding_status='ready' if embedding is not None else 'pending')
            db.add(track)
            db.flush()
            if embedding is None:
                db.add(Job(track_id=track.id))
            return track.id

    def upload(self, uid, data, filename, title, artist, genre, idempotency_key):
        if genre not in GENRES:
            raise HTTPException(422, 'Choose a valid genre')
        if not data or len(data) > 100*1024*1024:
            raise HTTPException(413, 'File is empty or exceeds 100 MiB')
        suffix = Path(filename or '').suffix.lower()
        if suffix not in {'.mp3','.wav'}:
            raise HTTPException(415, 'Only MP3 and WAV files are supported')
        digest = hashlib.sha256(data).hexdigest()
        with self.Session.begin() as db:
            if idempotency_key:
                previous = db.scalar(select(Track).where(Track.owner_id == uid, Track.upload_key == idempotency_key))
                if previous:
                    if previous.sha256 != digest:
                        raise HTTPException(409, 'Upload key reused for a different file')
                    return self.track_dict(previous)
            pending = db.scalar(select(__import__('sqlalchemy').func.count()).select_from(Track).where(Track.owner_id == uid, Track.playback_status.in_(['queued','processing'])))
            if pending >= 5:
                raise HTTPException(429, 'At most five uploads may be processed at once')
            key = f'{uuid.uuid4()}{suffix}'
            self.path(key).write_bytes(data)
            if db.bind.dialect.name=='sqlite':
                from sqlalchemy.dialects.sqlite import insert
            else:
                from sqlalchemy.dialects.postgresql import insert
            statement=insert(Track).values(owner_id=uid,title=title or Path(filename).stem[:160],artist=artist or 'Unknown artist',genre=genre,
                                          original_key=key,sha256=digest,size=len(data),upload_key=idempotency_key,color=COLORS[int(digest[:2],16)%len(COLORS)])
            if idempotency_key:
                statement=statement.on_conflict_do_nothing(index_elements=['owner_id','upload_key'])
            inserted=db.execute(statement.returning(Track.id)).scalar_one_or_none()
            if inserted is None:
                self.path(key).unlink(missing_ok=True)
                previous=db.scalar(select(Track).where(Track.owner_id==uid,Track.upload_key==idempotency_key))
                if previous.sha256!=digest:
                    raise HTTPException(409,'Upload key reused for a different file')
                return self.track_dict(previous)
            track=db.get(Track,inserted)
            db.add(Job(track_id=inserted))
            return self.track_dict(track)

    def process_jobs(self, max_jobs=100, stage_filter=None):
        """Atomic lease + bounded attempts; safe across a restart or a second worker."""
        for _ in range(max_jobs):
            now = time.time()
            with self.Session.begin() as db:
                exhausted=db.scalars(select(Job).where(Job.status=='processing',Job.leased_until<now,Job.attempts>=3)).all()
                for expired in exhausted:
                    expired.status='failed'
                    expired.error='Worker lease expired after maximum attempts'
                    asset=db.get(Track,expired.track_id)
                    if asset and not asset.deleted:
                        asset.error=expired.error
                        if expired.stage=='decode' and asset.playback_status!='ready':
                            asset.playback_status='failed'
                            asset.embedding_status='failed'
                        else:
                            asset.embedding_status='baseline' if asset.embedding else 'unavailable'
                claimable = or_(Job.status == 'queued', (Job.status == 'processing') & (Job.leased_until < now))
                query=select(Job).where(claimable,Job.attempts<3)
                if stage_filter:
                    query=query.where(Job.stage==stage_filter)
                job=db.scalar(query.order_by(case((Job.stage=='decode',0),else_=1)))
                if job is None:
                    return
                claimed = db.execute(update(Job).where(Job.id == job.id, claimable).values(status='processing', attempts=Job.attempts+1, leased_until=now+900))
                if not claimed.rowcount:
                    continue
                jid, tid, stage = job.id, job.track_id, job.stage
                track = db.get(Track, tid)
                if track.deleted:
                    job.status='cancelled'
                    continue
                original_key = track.original_key
                if stage=='decode':
                    track.playback_status='processing'
            playback_prepared=False
            try:
                signal = decode(self.path(original_key))
                if stage=='decode':
                    stream_key = f'{tid}.mp3'
                    transcode(self.path(original_key), self.path(stream_key))
                    with self.Session.begin() as db:
                        asset=db.get(Track,tid)
                        if asset.deleted:
                            self.path(stream_key).unlink(missing_ok=True)
                            db.get(Job,jid).status='cancelled'
                            continue
                        asset.stream_key,asset.duration,asset.playback_status=stream_key,len(signal)/48000,'ready'
                    playback_prepared=True
                    vector, model = spectral_embedding(signal), SPECTRAL_VERSION
                else:
                    vector, model = clap_embedding(signal, str(self.root/'model-cache')), CLAP_VERSION
                with self.Session.begin() as db:
                    track = db.get(Track, tid)
                    job = db.get(Job, jid)
                    job.status='done'
                    if track.deleted:
                        continue
                    track.embedding, track.embedding_model = vector, model
                    track.embedding_status = 'ready' if stage=='clap' else 'baseline'
                    track.error = None
                    if stage=='decode':
                        track.stream_key, track.duration, track.playback_status = stream_key, len(signal)/48000, 'ready'
                        if self.ml_enabled() and track.can_analyze:
                            db.add(Job(track_id=tid, stage='clap'))
                            track.embedding_status='upgrading'
            except Exception as error:
                with self.Session.begin() as db:
                    track, job = db.get(Track,tid), db.get(Job,jid)
                    job.error=str(error)[:1000]
                    job.status='failed'
                    track.error=str(error)[:1000]
                    if stage=='decode' and not playback_prepared:
                        track.playback_status='failed'
                        track.embedding_status='failed'
                    else:
                        track.embedding_status='baseline' if track.embedding else 'unavailable'

    def ml_enabled(self):
        return os.getenv('MUSICBOX_ENABLE_CLAP')=='1' or (self.root/'clap.enabled').exists()

    def enqueue_clap(self):
        with self.Session.begin() as db:
            tracks = db.scalars(select(Track).where(Track.playback_status=='ready', Track.deleted.is_(False), Track.can_analyze.is_(True), Track.embedding_model!=CLAP_VERSION)).all()
            for track in tracks:
                existing = db.scalar(select(Job).where(Job.track_id==track.id, Job.stage=='clap', Job.status.in_(['queued','processing'])))
                if not existing:
                    db.add(Job(track_id=track.id, stage='clap'))
                    track.embedding_status='upgrading'

    def _taste_context(self, db, uid, session_id=None, query=None):
        user=db.get(User,uid)
        catalog=db.scalars(select(Track).where(*self.eligible(uid),Track.playback_status=='ready',Track.can_recommend.is_(True))).all()
        states={f.track_id:f.signal for f in db.scalars(select(Feedback).where(Feedback.user_id==uid))}
        likes=[tid for tid,state in states.items() if state=='like']
        dislikes=[tid for tid,state in states.items() if state=='dislike']
        tracks=[self.track_dict(t,states.get(t.id),embedding=True) for t in catalog]
        if query is not None and query.id not in {t['id'] for t in tracks}:
            tracks.append(self.track_dict(query,states.get(query.id),embedding=True))
        now=time.time()
        events=db.scalars(select(Event).where(Event.user_id==uid,Event.event_type=='play_end',Event.created_at>=now-90*86400,Event.created_at<=now).order_by(Event.created_at.desc()).limit(10000)).all()
        history=[dict(event_id=e.event_id,track_id=e.track_id,payload=e.payload,created_at=e.created_at) for e in events]
        profiles=build_profiles(tracks,likes=likes,references=user.preferences.get('seed_track_ids',[]),dislikes=dislikes,events=history,session_id=session_id,now=now)
        profiles['evidence'].update(history_window_days=90,history_event_limit=10000,history_limit_reached=len(events)==10000)
        return user,tracks,likes,dislikes,profiles,now

    def discovery(self, uid, session_id=None, current_id=None, random_seed=None):
        with self.Session.begin() as db:
            if current_id is not None:
                current=self.get_track(db,current_id,uid)
                if current.playback_status!='ready':raise HTTPException(409,'Track is not ready for playback')
            user,tracks,likes,dislikes,profiles,now=self._taste_context(db,uid,session_id)
            response=discover(tracks,profiles,dislikes=dislikes,current_id=current_id,random_seed=random_seed if random_seed is not None else 42)
            rid=str(uuid.uuid4())
            response.update(request_id=rid,profile_version=user.profile_version)
            db.add(Recommendation(id=rid,user_id=uid,response=response,profile_snapshot=dict(version='taste-mixes-v1',profiles=profiles,session_id=session_id,current_id=current_id,as_of=now,dislikes=dislikes)))
            return response

    def save_mix(self, uid, request_id, group_id):
        with self.Session.begin() as db:
            request=db.get(Recommendation,request_id)
            if request is None or request.user_id!=uid:raise HTTPException(404,'Recommendation not found')
            group=next((g for g in request.response.get('interest_groups',[]) if g['id']==group_id),None)
            if not group or not group['items']:raise HTTPException(404,'Mix not found')
            ids=[t['id'] for t in group['items']]
            for tid in ids:
                track=self.get_track(db,tid,uid)
                feedback=db.scalar(select(Feedback).where(Feedback.user_id==uid,Feedback.track_id==tid))
                if track.playback_status!='ready' or not track.can_recommend or (feedback and feedback.signal=='dislike'):
                    raise HTTPException(409,'This mix changed. Refresh recommendations before saving.')
            playlist=Playlist(user_id=uid,title=group['title'][:100],track_ids=ids)
            db.add(playlist);db.flush()
            return dict(id=playlist.id,title=playlist.title,track_ids=playlist.track_ids)

    def recommendations(self, uid, limit, query_id=None, random_seed=None, session_id=None):
        with self.Session.begin() as db:
            query=None
            if query_id is not None:
                query = self.get_track(db, query_id, uid)
                if query.playback_status!='ready':
                    raise HTTPException(409, 'Track is not ready for playback')
            user,tracks,likes,dislikes,profiles,now=self._taste_context(db,uid,session_id,query)
            prefs=user.preferences
            response=recommend(tracks, genre=prefs['genre'], mode=prefs['mode'],
                               likes=likes, dislikes=dislikes, long_weights=profiles['long_term'], session_weights=profiles['session'], evidence=profiles['evidence'], limit=limit,
                               surface='similar' if query_id is not None else 'for_you', query_id=query_id,
                               random_seed=random_seed if random_seed is not None else secrets.randbelow(2**31))
            rid=str(uuid.uuid4())
            response.update(request_id=rid, profile_version=user.profile_version, embedding_version=response['engine'])
            db.add(Recommendation(id=rid,user_id=uid,response=response,profile_snapshot=dict(version='full-catalog-v2',preferences=prefs,likes=likes,dislikes=dislikes,profiles=profiles,session_id=session_id,as_of=now)))
            return response

    def record_event(self, db, uid, event_id, event_type, tid, payload, request_id=None):
        self.get_track(db,tid,uid)
        if request_id:
            request=db.get(Recommendation,request_id)
            if request is None or request.user_id!=uid:
                raise HTTPException(403,'Invalid recommendation attribution')
            if tid not in {t['id'] for t in request.response['items']}:
                raise HTTPException(422,'Track does not belong to recommendation')
        existing=db.scalar(select(Event).where(Event.user_id==uid,Event.event_id==event_id))
        if existing:
            if existing.payload!=payload:
                raise HTTPException(409,'Event ID reused with a different payload')
            return False
        if db.bind.dialect.name=='sqlite':
            from sqlalchemy.dialects.sqlite import insert
        else:
            from sqlalchemy.dialects.postgresql import insert
        statement=insert(Event).values(user_id=uid,event_id=event_id,event_type=event_type,track_id=tid,request_id=request_id,payload=payload)
        inserted=db.execute(statement.on_conflict_do_nothing(index_elements=['user_id','event_id'])).rowcount
        if not inserted:
            existing=db.scalar(select(Event).where(Event.user_id==uid,Event.event_id==event_id))
            if existing.payload!=payload:
                raise HTTPException(409,'Event ID reused with a different payload')
        return bool(inserted)
