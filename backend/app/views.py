"""Musicbox endpoints implemented as native Django views."""
import os
import secrets
import time
from django.http import JsonResponse
from django.urls import path
from pydantic import BaseModel, Field
from sqlalchemy import select
from .db import Feedback, Job, Playlist, Track, User
from .errors import APIError as HTTPException
from .http import endpoint, identity, audio_response
from .recommendation import GENRES, GENRE_LABELS

class Preferences(BaseModel):
    genre: str | None = None
    mode: str | None = None
    seed_track_ids: list[int] | None = Field(default=None, max_length=3)
    personalization_enabled: bool | None = None
    smart_autoplay_enabled: bool | None = None

class FeedbackInput(BaseModel):
    track_id: int
    signal: str = Field(pattern='^(like|dislike|clear)$')
    event_id: str = Field(min_length=8, max_length=100)

class PlaybackEvent(BaseModel):
    event_id: str = Field(min_length=8, max_length=100)
    track_id: int
    event_type: str = Field(pattern='^(exposure|play_start|play_end)$')
    request_id: str | None = None
    session_id: str | None = Field(default=None, max_length=100)
    position: int | None = Field(default=None, ge=1, le=100)
    played_ms: int | None = Field(default=None, ge=0, le=86400000)
    occurred_at: str | None = Field(default=None, max_length=50)
    termination_reason: str | None = Field(default=None, max_length=50)
    playback_origin: str | None = Field(default=None, max_length=50)

class Batch(BaseModel):
    events: list[PlaybackEvent] = Field(max_length=100)

class PlaylistInput(BaseModel):
    title: str = Field(min_length=1, max_length=100)

class TrackInput(BaseModel):
    track_id: int

class SaveMixInput(BaseModel):
    request_id: str = Field(min_length=1, max_length=100)
    group_id: str = Field(min_length=1, max_length=100)

def create_routes(service):
    def categories():
        return dict(categories=[dict(id=g, label=GENRE_LABELS[g]) for g in GENRES])

    def library(user=None):
        tracks = service.library(user.id)
        return dict(tracks=tracks, stats=dict(total=len(tracks), private=sum((t['visibility'] == 'private' for t in tracks)), shared=sum((t['visibility'] == 'shared' for t in tracks)), ready=sum((t['playback_status'] == 'ready' for t in tracks)), clap=sum(((t['embedding_model'] or '').startswith('clap:') for t in tracks))))

    def job_status(job_id: str, user=None):
        with service.Session() as db:
            job = db.get(Job, job_id)
            if not job:
                raise HTTPException(404, 'Job not found')
            service.get_track(db, job.track_id, user.id)
            return dict(id=job.id, track_id=job.track_id, status=job.status, stage=job.stage, error=job.error)

    def playback(tid: int, user=None):
        with service.Session() as db:
            track = service.get_track(db, tid, user.id)
            return dict(stream_url=f'/api/v1/tracks/{tid}/stream', track=service.track_dict(track))

    def delete(tid: int, user=None):
        keys = []
        with service.Session.begin() as db:
            track = service.get_track(db, tid, user.id)
            if track.owner_id != user.id:
                raise HTTPException(403, 'You can only delete your own private tracks')
            track.deleted = True
            keys = [track.original_key, track.stream_key]
            owner = db.get(User, user.id)
            owner.preferences = {**owner.preferences, 'seed_track_ids': [i for i in owner.preferences.get('seed_track_ids', []) if i != tid]}
            owner.profile_version += 1
            for playlist in db.scalars(select(Playlist).where(Playlist.user_id == user.id)):
                playlist.track_ids = [i for i in playlist.track_ids if i != tid]
        for key in set(keys):
            if key:
                service.path(key).unlink(missing_ok=True)
        return dict(ok=True)

    def preferences(body, user):
        if body.genre is not None and body.genre not in GENRES or (body.mode is not None and body.mode not in {'focus', 'balanced'}):
            raise HTTPException(422, 'Invalid preferences')
        with service.Session.begin() as db:
            existing = db.get(User, user.id)
            if body.seed_track_ids is not None:
                for tid in body.seed_track_ids:
                    service.get_track(db, tid, user.id)
            existing.preferences = {'personalization_enabled': False, 'smart_autoplay_enabled': True, **existing.preferences, **body.model_dump(exclude_none=True)}
            existing.profile_version += 1
            return dict(preferences=existing.preferences, profile_version=existing.profile_version)

    def patch_preferences(body: Preferences, user=None):
        return preferences(body, user)

    def onboarding(body: Preferences, user=None):
        return preferences(body, user)

    def for_you(limit: int=20, seed: int | None=None, session_id: str | None=None, user=None):
        return service.recommendations(user.id, limit, random_seed=seed, session_id=session_id)

    def discovery(session_id: str | None=None, current_id: int | None=None, seed: int | None=None, user=None):
        return service.discovery(user.id,session_id=session_id,current_id=current_id,random_seed=seed)

    def save_mix(body: SaveMixInput, user=None):
        return service.save_mix(user.id,body.request_id,body.group_id)

    def similar(tid: int, limit: int=10, user=None):
        return service.recommendations(user.id, limit, query_id=tid)

    def feedback(body: FeedbackInput, user=None):
        with service.Session.begin() as db:
            inserted = service.record_event(db, user.id, body.event_id, 'feedback', body.track_id, body.model_dump())
            if inserted:
                state = db.get(Feedback, (user.id, body.track_id))
                if state:
                    state.signal = body.signal
                else:
                    db.add(Feedback(user_id=user.id, track_id=body.track_id, signal=body.signal))
                db.get(User, user.id).profile_version += 1
        return dict(ok=True, inserted=inserted, signal=body.signal)

    def events(body: Batch, user=None):
        inserted = 0
        rejected = []
        accepted = []
        for event in body.events:
            try:
                with service.Session.begin() as db:
                    inserted += service.record_event(db, user.id, event.event_id, event.event_type, event.track_id, event.model_dump(), event.request_id)
                accepted.append(event.event_id)
            except HTTPException as error:
                rejected.append(dict(event_id=event.event_id, status=error.status_code, detail=error.detail))
        if rejected and (not accepted):
            raise HTTPException(rejected[0]['status'], rejected[0]['detail'])
        return dict(inserted=inserted, accepted=accepted, rejected=rejected)

    def playlists(user=None):
        with service.Session() as db:
            return dict(playlists=[dict(id=p.id, title=p.title, track_ids=p.track_ids) for p in db.scalars(select(Playlist).where(Playlist.user_id == user.id))])

    def new_playlist(body: PlaylistInput, user=None):
        with service.Session.begin() as db:
            playlist = Playlist(user_id=user.id, title=body.title)
            db.add(playlist)
            db.flush()
            return dict(id=playlist.id, title=playlist.title, track_ids=[])

    def playlist_add(pid: str, body: TrackInput, user=None):
        with service.Session.begin() as db:
            service.get_track(db, body.track_id, user.id)
            playlist = db.get(Playlist, pid)
            if not playlist or playlist.user_id != user.id:
                raise HTTPException(404, 'Playlist not found')
            playlist.track_ids = list(dict.fromkeys(playlist.track_ids + [body.track_id]))
            return dict(ok=True)

    def playlist_remove(pid: str, tid: int, user=None):
        with service.Session.begin() as db:
            playlist = db.get(Playlist, pid)
            if not playlist or playlist.user_id != user.id:
                raise HTTPException(404, 'Playlist not found')
            playlist.track_ids = [i for i in playlist.track_ids if i != tid]
            return dict(ok=True)

    def health():
        return dict(status='ok', version='0.2.0', framework='django')

    def session(request):
        current = service.user_for_cookie(request.COOKIES.get('musicbox_session'))
        if current:
            result = dict(user=service.public_user(current[0]), csrf_token=current[1])
            if request.method == 'POST':
                token = request.COOKIES['musicbox_session']
                service.renew_session(token)
                response = JsonResponse(result)
                response.set_cookie('musicbox_session', token, max_age=365*86400, httponly=True, samesite='Lax', secure=os.getenv('MUSICBOX_SECURE_COOKIE')=='1', path='/')
                return response
            return result
        if request.method == 'GET':
            raise HTTPException(401, 'Listening session not initialized')
        with service.Session.begin() as db:
            user = User(name='Listener')
            db.add(user)
            db.flush()
        raw, csrf = service.new_session(user.id)
        response = JsonResponse(dict(user=service.public_user(user), csrf_token=csrf))
        response.set_cookie('musicbox_session', raw, max_age=365*86400, httponly=True, samesite='Lax', secure=os.getenv('MUSICBOX_SECURE_COOKIE')=='1', path='/')
        return response

    def upload(request, user=None):
        file = request.FILES.get('file')
        if file is None:
            raise HTTPException(422, 'Choose an MP3 or WAV file')
        if file.size > 100*1024*1024:
            raise HTTPException(413, 'Maximum file size is 100 MiB')
        title, artist = request.POST.get('title','').strip(), request.POST.get('artist','').strip()
        if len(title)>160 or len(artist)>160:
            raise HTTPException(422, 'Title and artist must be at most 160 characters')
        track = service.upload(user.id, file.read(), file.name, title, artist, request.POST.get('genre',''), request.headers.get('Idempotency-Key'))
        return dict(track=track)

    def stream(tid, request, user=None):
        with service.Session() as db:
            track = service.get_track(db, tid, user.id)
            if track.playback_status!='ready' or not track.stream_key:
                raise HTTPException(409, 'Track is not ready for playback')
            asset = service.path(track.stream_key)
            if not asset.is_file():
                raise HTTPException(404, 'Audio asset missing')
        return audio_response(request, asset)

    registry = {}
    registry.setdefault('v1/recommendations/discovery', {})['GET'] = endpoint('v1/recommendations/discovery',['GET'],discovery,service,status=200).callback
    registry.setdefault('v1/playlists/from-recommendation', {})['POST'] = endpoint('v1/playlists/from-recommendation',['POST'],save_mix,service,status=201).callback
    registry.setdefault('health', {})['GET'] = endpoint('health', ['GET'], health, service, status=200).callback
    registry.setdefault('v1/session', {})['POST'] = endpoint('v1/session', ['POST'], session, service, status=200).callback
    registry.setdefault('v1/session', {})['GET'] = endpoint('v1/session', ['GET'], session, service, status=200).callback
    registry.setdefault('v1/uploads', {})['POST'] = endpoint('v1/uploads', ['POST'], upload, service, status=202).callback
    registry.setdefault('v1/tracks/<int:tid>/stream', {})['GET'] = endpoint('v1/tracks/<int:tid>/stream', ['GET'], stream, service, status=200).callback
    registry.setdefault('v1/categories', {})['GET'] = endpoint('v1/categories', ['GET'], categories, service, status=200).callback
    registry.setdefault('v1/library/tracks', {})['GET'] = endpoint('v1/library/tracks', ['GET'], library, service, status=200).callback
    registry.setdefault('v1/jobs/<str:job_id>', {})['GET'] = endpoint('v1/jobs/<str:job_id>', ['GET'], job_status, service, status=200).callback
    registry.setdefault('v1/tracks/<int:tid>/playback', {})['GET'] = endpoint('v1/tracks/<int:tid>/playback', ['GET'], playback, service, status=200).callback
    registry.setdefault('v1/tracks/<int:tid>', {})['DELETE'] = endpoint('v1/tracks/<int:tid>', ['DELETE'], delete, service, status=200).callback
    registry.setdefault('v1/preferences', {})['PATCH'] = endpoint('v1/preferences', ['PATCH'], patch_preferences, service, status=200).callback
    registry.setdefault('v1/preferences/onboarding', {})['POST'] = endpoint('v1/preferences/onboarding', ['POST'], onboarding, service, status=200).callback
    registry.setdefault('v1/recommendations/for-you', {})['GET'] = endpoint('v1/recommendations/for-you', ['GET'], for_you, service, status=200).callback
    registry.setdefault('v1/recommendations/similar/<int:tid>', {})['GET'] = endpoint('v1/recommendations/similar/<int:tid>', ['GET'], similar, service, status=200).callback
    registry.setdefault('v1/feedback', {})['POST'] = endpoint('v1/feedback', ['POST'], feedback, service, status=200).callback
    registry.setdefault('v1/events/batch', {})['POST'] = endpoint('v1/events/batch', ['POST'], events, service, status=200).callback
    registry.setdefault('v1/playlists', {})['GET'] = endpoint('v1/playlists', ['GET'], playlists, service, status=200).callback
    registry.setdefault('v1/playlists', {})['POST'] = endpoint('v1/playlists', ['POST'], new_playlist, service, status=200).callback
    registry.setdefault('v1/playlists/<str:pid>/tracks', {})['POST'] = endpoint('v1/playlists/<str:pid>/tracks', ['POST'], playlist_add, service, status=200).callback
    registry.setdefault('v1/playlists/<str:pid>/tracks/<int:tid>', {})['DELETE'] = endpoint('v1/playlists/<str:pid>/tracks/<int:tid>', ['DELETE'], playlist_remove, service, status=200).callback

    def dispatch(handlers):
        def view(request, **kwargs):
            handler = handlers.get(request.method)
            if handler is None:
                response = JsonResponse({'detail': 'Method not allowed'}, status=405)
                response['Allow'] = ', '.join(handlers)
                return response
            return handler(request, **kwargs)
        return view
    from .generation_views import generation_routes
    return [path(pattern, dispatch(handlers)) for pattern, handlers in registry.items()] + generation_routes(service)
