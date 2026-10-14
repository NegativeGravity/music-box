"""Private generation requests and durable state, independent of model inference."""
import hashlib
import json
import os
import secrets
import shutil
import time
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import and_, func, or_, select, text

from .db import GenerationJob, GenerationCandidate, Job, Track
from .errors import APIError
from .recommendation import GENRES

LANGUAGES = ['en', 'es', 'fr', 'de', 'zh', 'ja', 'ko']
ACTIVE = ['submitting', 'running', 'ingesting']


class GenerationInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    prompt: str = Field(min_length=1, max_length=1200)
    mode: str = Field(pattern='^(vocal|instrumental)$')
    lyrics: str = Field(default='', max_length=4000)
    language: str = 'en'
    reference_track_ids: list[int] = Field(default_factory=list, max_length=3)
    primary_reference_track_id: int | None = None
    seed: int | None = Field(default=None, ge=0, lt=2**31)
    duration_seconds: int | None = Field(default=None, ge=10, le=60)

    @field_validator('prompt', 'lyrics')
    @classmethod
    def clean_text(cls, value):
        return value.strip()

    @model_validator(mode='after')
    def validate_choices(self):
        if not self.prompt:
            raise ValueError('Describe the music you want to create')
        if self.language not in LANGUAGES:
            raise ValueError('Choose a supported vocal language')
        if self.mode == 'vocal' and not self.lyrics:
            raise ValueError('Add lyrics for vocal generation')
        if self.primary_reference_track_id is not None and self.primary_reference_track_id not in self.reference_track_ids:
            raise ValueError('Choose the primary reference from selected tracks')
        self.reference_track_ids = list(dict.fromkeys(self.reference_track_ids))
        return self


class MusicGenerationService:
    def __init__(self, service, engine=None, engine_name=None):
        self.service, self.engine = service, engine
        self.engine_name = engine_name or getattr(engine, 'kind', os.getenv('MUSICBOX_AI_ENGINE', 'yue2'))
        if self.engine_name not in ('yue2', 'ace-step'):
            raise ValueError('Choose yue2 or ace-step as the local engine')

    def transaction(self):
        db = self.service.Session()
        if self.service.engine.dialect.name == 'sqlite':
            db.execute(text('BEGIN IMMEDIATE'))
        return db

    @staticmethod
    def dto(job):
        request = {key: value for key, value in job.request_snapshot.items()
                   if key not in ('primary_audio_key', 'primary_audio_sha256')}
        selected = next((c for c in job.candidates if c.output_key and c.output_key == job.output_key), None)
        return dict(id=job.id, request=request, status=job.status, phase=job.phase,
                    saved_track_id=job.saved_track_id, error=job.error,
                    error_code=job.error_code, created_at=job.created_at, updated_at=job.updated_at,
                    elapsed_seconds=job.elapsed_seconds,
                    audio_url=(f'/api/v1/generation/jobs/{job.id}/candidates/{selected.index}/audio' if selected else
                               f'/api/v1/generation/jobs/{job.id}/audio' if job.status == 'completed' and job.output_key else None),
                    selected_candidate_index=selected.index if selected else None,
                    candidates=[dict(index=c.index, seed=c.seed, status=c.status, error=c.error,
                        duration_seconds=c.diagnostics.get('duration_seconds'), duration_capped=c.diagnostics.get('duration_capped', False),
                        saved_track_id=c.saved_track_id, selected=bool(selected and selected.index == c.index),
                        audio_url=f'/api/v1/generation/jobs/{job.id}/candidates/{c.index}/audio' if c.status == 'completed' else None)
                        for c in job.candidates],
                    progress=dict(completed=sum(c.status == 'completed' for c in job.candidates),
                                  finished=sum(c.status in ('completed', 'failed', 'cancelled') for c in job.candidates),
                                  total=len(job.candidates), active_candidate_index=next((c.index for c in job.candidates if c.status in ('starting', 'rendering')), None)))

    @staticmethod
    def owned(db, uid, jid):
        job = db.scalar(select(GenerationJob).where(GenerationJob.id == jid, GenerationJob.owner_id == uid))
        if not job:
            raise APIError(404, 'Generation not found')
        return job

    def create(self, uid, body, key):
        if not key or len(key) > 100:
            raise APIError(422, 'A generation idempotency key is required (maximum 100 characters)')
        requested = body.model_dump()
        if body.duration_seconds is None:
            requested.pop('duration_seconds')
        digest = hashlib.sha256(json.dumps(requested, sort_keys=True).encode()).hexdigest()
        allocated = None
        db = self.transaction()
        try:
            previous = db.scalar(select(GenerationJob).where(GenerationJob.owner_id == uid, GenerationJob.idempotency_key == key))
            if previous:
                if previous.request_hash != digest:
                    raise APIError(409, 'Generation key reused for a different request')
                return self.dto(previous)
            if self.engine_name == 'yue2':
                if body.primary_reference_track_id is not None:
                    raise APIError(422, 'Direct audio reference is not available in this YuE2 version. Use style inspirations instead.')
                if body.language not in ('en', 'zh'):
                    raise APIError(422, 'Choose English or Chinese for YuE2 generation')
            elif body.duration_seconds not in (None, 20):
                raise APIError(422, 'ACE-Step takes are limited to 20 seconds')
            pending = db.scalar(select(func.count()).select_from(GenerationJob).where(
                GenerationJob.owner_id == uid, GenerationJob.status.in_(['queued', *ACTIVE])))
            if pending >= 3:
                raise APIError(429, 'At most three of your generations may be queued or active')
            references = []
            for tid in body.reference_track_ids:
                track = self.service.get_track(db, tid, uid)
                if not track.can_analyze:
                    raise APIError(422, 'This track is not available for generation inspiration')
                references.append(dict(id=tid, title=track.title, artist=track.artist, genre=track.genre))
            snapshot = {**requested, 'seed': body.seed if body.seed is not None else secrets.randbelow(2**31),
                        'duration_seconds': (body.duration_seconds or 30) if self.engine_name == 'yue2' else 20,
                        'engine': self.engine_name, 'model': 'YuE2-3B-Q8' if self.engine_name == 'yue2' else 'acestep-v15-turbo',
                        'takes': 8 if self.engine_name == 'yue2' else 1, 'selection_policy': 'listener' if self.engine_name == 'yue2' else 'single',
                        'references': references,
                        'genre': references[0]['genre'] if references and references[0]['genre'] in GENRES else 'Electronic'}
            cues = ', '.join(dict.fromkeys(ref['genre'] for ref in references))
            snapshot['caption'] = body.prompt + (f'\nStyle cues: {cues}.' if cues else '')
            if self.engine_name == 'yue2':
                from .yue2_runtime import REVISION, RELEASE
                snapshot.update(model_revision=REVISION, runtime_release=RELEASE, precision='q8_0')
            jid = str(uuid.uuid4())
            if body.primary_reference_track_id is not None:
                track = self.service.get_track(db, body.primary_reference_track_id, uid)
                source = self.service.path(track.original_key)
                if not source.is_file():
                    raise APIError(409, 'Reference audio is no longer available')
                reference_key = f'generation/{jid}/reference{source.suffix}'
                allocated = self.service.path(reference_key)
                allocated.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, allocated)
                snapshot.update(primary_audio_key=reference_key, primary_audio_sha256=track.sha256)
            job = GenerationJob(id=jid, owner_id=uid, idempotency_key=key,
                                request_hash=digest, request_snapshot=snapshot)
            if self.engine_name == 'yue2':
                job.candidates = [GenerationCandidate(index=i, seed=(snapshot['seed'] + i - 1) % 2**31) for i in range(1, 9)]
            db.add(job)
            db.flush()
            result = self.dto(job)
            db.commit()
            return result
        except Exception:
            db.rollback()
            if allocated is not None:
                allocated.unlink(missing_ok=True)
            raise
        finally:
            db.close()

    def get(self, uid, jid):
        with self.service.Session() as db:
            return self.dto(self.owned(db, uid, jid))

    def list(self, uid):
        with self.service.Session() as db:
            return [self.dto(job) for job in db.scalars(select(GenerationJob).where(
                GenerationJob.owner_id == uid).order_by(GenerationJob.created_at.desc()).limit(30))]

    def output(self, uid, jid):
        with self.service.Session() as db:
            job = self.owned(db, uid, jid)
            ready = job.status == 'completed' or any(c.status == 'completed' and c.output_key == job.output_key for c in job.candidates)
            if not ready or not job.output_key:
                raise APIError(409, 'Generation audio is not ready')
            path = self.service.path(job.output_key)
            if not path.is_file():
                raise APIError(404, 'Generation audio is unavailable')
            return path

    def candidate(self, db, uid, jid, index):
        job = self.owned(db, uid, jid)
        candidate = next((c for c in job.candidates if c.index == index), None)
        if candidate is None:
            raise APIError(404, 'Variation not found')
        return job, candidate

    def candidate_output(self, uid, jid, index):
        with self.service.Session() as db:
            _, candidate = self.candidate(db, uid, jid, index)
            if candidate.status != 'completed' or not candidate.output_key:
                raise APIError(409, 'Variation audio is not ready')
            path = self.service.path(candidate.output_key)
            if not path.is_file():
                raise APIError(404, 'Variation audio is unavailable')
            return path

    def select_candidate(self, uid, jid, index):
        db = self.transaction()
        try:
            job, candidate = self.candidate(db, uid, jid, index)
            if candidate.status != 'completed' or not candidate.output_key:
                raise APIError(409, 'Finish this variation before selecting it')
            if not self.service.path(candidate.output_key).is_file():
                raise APIError(404, 'Variation audio is unavailable')
            job.output_key, job.saved_track_id = candidate.output_key, candidate.saved_track_id
            job.updated_at = time.time()
            result = self.dto(job)
            db.commit()
            return result
        finally:
            db.close()

    def cancel(self, uid, jid):
        from .yue2_artifacts import adopt_completed
        db = self.transaction()
        markers = []
        try:
            job = self.owned(db, uid, jid)
            if job.request_snapshot.get('engine') != 'yue2':
                raise APIError(409, 'Cancellation is available for YuE2 generations')
            if job.status in ('completed', 'failed', 'cancelled'):
                return self.dto(job)
            job.status, job.phase, job.updated_at = 'cancelled', 'cancelled', time.time()
            for candidate in job.candidates:
                if candidate.status in ('starting', 'rendering'):
                    try:
                        if adopt_completed(self.service, db, job, candidate):
                            continue
                    except Exception:
                        candidate.error = 'The completed variation could not be validated.'
                if candidate.status in ('queued', 'starting', 'rendering'):
                    cancel = self.service.path(f'generation/{jid}/candidate-{candidate.index}/cancel.requested')
                    markers.append(cancel)
                    candidate.status = 'cancelled'
            result = self.dto(job)
            db.commit()
            for cancel in markers:
                cancel.parent.mkdir(parents=True, exist_ok=True)
                cancel.touch()
            return result
        finally:
            db.close()

    def save(self, uid, jid):
        allocated = None
        db = self.transaction()
        try:
            job = self.owned(db, uid, jid)
            candidate = next((c for c in job.candidates if c.output_key and c.output_key == job.output_key and c.status == 'completed'), None)
            if not job.output_key or (job.status != 'completed' and candidate is None):
                raise APIError(409, 'Finish generation before saving to your library')
            saved_id = candidate.saved_track_id if candidate else job.saved_track_id
            if saved_id:
                return self.service.track_dict(self.service.get_track(db, saved_id, uid))
            source = self.service.path(job.output_key)
            if not source.is_file():
                raise APIError(404, 'Generation audio is unavailable')
            key = str(uuid.uuid4()) + '.wav'
            allocated = self.service.path(key)
            shutil.copyfile(source, allocated)
            with allocated.open('rb') as content:
                digest = hashlib.file_digest(content, 'sha256').hexdigest()
            track = Track(owner_id=uid, title=job.request_snapshot['prompt'][:80], artist='Musicbox AI',
                genre=job.request_snapshot['genre'], source='generated', visibility='private',
                provenance=json.dumps({'model': job.request_snapshot.get('model', 'acestep-v15-turbo'), 'generation_id': jid,
                    'candidate_index': candidate.index if candidate else None, 'seed': candidate.seed if candidate else job.request_snapshot['seed'],
                    'selection_policy': job.request_snapshot.get('selection_policy', 'single'), 'request': job.request_snapshot}),
                original_key=key, sha256=digest, size=allocated.stat().st_size,
                upload_key='generation:' + jid + (f':candidate:{candidate.index}' if candidate else ''))
            db.add(track)
            db.flush()
            db.add(Job(track_id=track.id))
            job.saved_track_id, job.updated_at = track.id, time.time()
            if candidate:
                candidate.saved_track_id = track.id
            result = self.service.track_dict(track)
            db.commit()
            return result
        except Exception:
            db.rollback()
            if allocated is not None:
                allocated.unlink(missing_ok=True)
            raise
        finally:
            db.close()

    def status(self):
        available = False
        if self.engine is not None:
            try:
                available = bool(self.engine.health()['models_initialized'])
            except Exception:
                pass
        with self.service.Session() as db:
            queued = db.scalar(select(func.count()).select_from(GenerationJob).where(GenerationJob.status == 'queued'))
            busy = db.scalar(select(func.count()).select_from(GenerationJob).where(or_(
                GenerationJob.status.in_(ACTIVE), and_(GenerationJob.status == 'interrupted',
                                                        GenerationJob.phase == 'awaiting_engine_idle'))))
        if self.engine_name == 'yue2':
            from .yue2_worker import worker_available
            available = available and worker_available(self.service)
            capabilities = dict(engine='yue2', model='YuE2-3B-Q8', duration_seconds=30, takes=8,
                                languages=['en', 'zh'], tested_languages=[], automatic_lyrics=False,
                                audio_reference=False, selection_policy='listener', duration_is_maximum=True)
        else:
            capabilities = dict(engine='ace-step', model='acestep-v15-turbo', duration_seconds=20, takes=1,
                                languages=LANGUAGES, tested_languages=['en'], automatic_lyrics=False, audio_reference=True)
        return dict(available=bool(available), active=bool(busy), queued_count=queued, capabilities=capabilities)
