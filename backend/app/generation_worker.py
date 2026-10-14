"""One durable local generation at a time, never an automatic duplicate POST."""
import hashlib
import logging
import os
from pathlib import Path
import threading
import time
import uuid

import numpy as np
from sqlalchemy import or_, select
from .audio import decode, ffmpeg
from .db import GenerationJob
from .local_music_engine import LocalMusicEngine
from .music_generation import ACTIVE, MusicGenerationService


class GenerationWorker:
    def __init__(self, service, engine, temp_root, observation_seconds=1800, poll_seconds=2):
        self.service, self.engine = service, engine
        self.generations = MusicGenerationService(service, engine, engine_name='ace-step')
        self.temp_root = Path(temp_root).resolve()
        self.temp_root.mkdir(parents=True, exist_ok=True)
        self.observation_seconds, self.poll_seconds = observation_seconds, poll_seconds
        self.identity = str(uuid.uuid4())

    def claim(self):
        db = self.generations.transaction()
        try:
            legacy = or_(GenerationJob.request_snapshot['engine'].as_string() == 'ace-step',
                         GenerationJob.request_snapshot['engine'].as_string().is_(None))
            blocked = db.scalar(select(GenerationJob.id).where(
                legacy,
                GenerationJob.status == 'interrupted', GenerationJob.phase == 'awaiting_engine_idle'))
            if blocked:
                return None
            active = db.scalar(select(GenerationJob).where(legacy, GenerationJob.status.in_(ACTIVE)).order_by(GenerationJob.created_at))
            if active and active.leased_until > time.time():
                return None
            job = active or db.scalar(select(GenerationJob).where(legacy, GenerationJob.status == 'queued').order_by(GenerationJob.created_at))
            if job is None:
                return None
            if job.status == 'submitting' and not job.upstream_task_id:
                job.status, job.phase = 'interrupted', 'awaiting_engine_idle'
                job.error_code, job.error = 'submission_uncertain', 'The previous submission could not be confirmed. Waiting for the local engine to become idle.'
                db.commit()
                return None
            job.status = 'running' if job.upstream_task_id else 'submitting'
            job.phase = 'rendering' if job.upstream_task_id else 'preparing'
            job.lease_owner, job.leased_until = self.identity, time.time() + 60
            job.started_at = job.started_at or time.time()
            job.updated_at = time.time()
            jid = job.id
            db.commit()
            return jid
        finally:
            db.close()

    def update(self, jid, **values):
        with self.service.Session.begin() as db:
            job = db.get(GenerationJob, jid)
            if job.lease_owner != self.identity:
                raise RuntimeError('Generation lease ownership changed')
            for name, value in values.items():
                setattr(job, name, value)
            job.updated_at = time.time()
            job.leased_until = time.time() + 60

    def heartbeat(self, jid, stop):
        while not stop.wait(15):
            try:
                self.update(jid)
            except Exception:
                logging.exception('Generation lease heartbeat failed')
                return

    def reference_path(self, jid):
        return self.temp_root / f'musicbox-generation-{jid}.wav'

    def request(self, jid, snapshot, uid):
        for ref in snapshot['references']:
            with self.service.Session() as db:
                track = self.service.get_track(db, ref['id'], uid)
                if not track.can_analyze:
                    raise ValueError('A reference is no longer available for analysis')
        request = dict(prompt=snapshot['caption'], lyrics=snapshot['lyrics'] if snapshot['mode'] == 'vocal' else '[Instrumental]',
            vocal_language=snapshot['language'], audio_duration=20, audio_format='wav',
            batch_size=1, inference_steps=8, thinking=False, use_format=False,
            seed=snapshot['seed'], use_random_seed=False, model='acestep-v15-turbo', task_type='text2music')
        if snapshot.get('primary_audio_key'):
            source = self.service.path(snapshot['primary_audio_key'])
            with source.open('rb') as content:
                source_hash = hashlib.file_digest(content, 'sha256').hexdigest()
            if source_hash != snapshot['primary_audio_sha256']:
                raise ValueError('Reference snapshot changed')
            reference = self.reference_path(jid)
            ffmpeg(['-y', '-i', str(source), '-map', '0:a:0', '-vn', '-ac', '2', '-ar', '48000',
                    '-t', '20', '-c:a', 'pcm_s16le', str(reference)])
            with reference.open('rb') as content:
                submitted_hash = hashlib.file_digest(content, 'sha256').hexdigest()
            evidence = self.service.path(f'generation/{jid}/reference-submission.json')
            evidence.write_text(__import__('json').dumps({'source_sha256': source_hash, 'submitted_sha256': submitted_hash,
                                                        'primary_track_id': snapshot['primary_reference_track_id']}))
            request['reference_audio_path'] = str(reference)
        return request

    def resolve_interrupted(self):
        with self.service.Session() as db:
            waiting = [(j.id, j.upstream_task_id) for j in db.scalars(select(GenerationJob).where(
                GenerationJob.status == 'interrupted', GenerationJob.phase == 'awaiting_engine_idle'))]
        for jid, task in waiting:
            result = self.engine.query(task) if task else None
            recoverable = bool(task and result and result['status'] in (1, 2))
            idle = False if recoverable else self.engine.idle()
            if task and idle and not recoverable:
                # Completion may occur between query and idle. The known task,
                # rather than global queue stats, decides whether it is lost.
                result = self.engine.query(task)
                recoverable = bool(result and result['status'] in (1, 2))
            lost = idle and (not task or result is None)
            if recoverable or lost:
                db = self.generations.transaction()
                try:
                    job = db.get(GenerationJob, jid)
                    if job.status != 'interrupted' or job.phase != 'awaiting_engine_idle':
                        continue
                    if recoverable:
                        job.status, job.phase, job.leased_until = 'running', 'rendering', 0
                    else:
                        job.phase = 'interrupted'
                        job.error = 'Generation was interrupted. You can explicitly create another take.'
                    db.commit()
                finally:
                    db.close()
                if lost:
                    self.reference_path(jid).unlink(missing_ok=True)

    def run_once(self):
        if not self.engine.health().get('models_initialized'):
            return False
        self.resolve_interrupted()
        jid = self.claim()
        if jid is None:
            return False
        with self.service.Session() as db:
            job = db.get(GenerationJob, jid)
            task, snapshot, uid = job.upstream_task_id, dict(job.request_snapshot), job.owner_id
        stopped = threading.Event()
        heartbeat = threading.Thread(target=self.heartbeat, args=(jid, stopped), daemon=True)
        heartbeat.start()
        start = time.monotonic()
        submitting = False
        terminal = False
        operation = 'preparing'
        output = self.service.path(f'generation/{jid}/output.wav')
        try:
            if not task:
                request = self.request(jid, snapshot, uid)
                submitting = True
                task = self.engine.submit(request)
                self.update(jid, upstream_task_id=task, status='running', phase='rendering')
                submitting = False
            operation = 'rendering'
            while True:
                result = self.engine.query(task)
                if result is None:
                    self.update(jid, status='interrupted', phase='awaiting_engine_idle',
                                error_code='upstream_lost', error='The local engine no longer knows this generation.')
                    break
                if result['status'] == 2:
                    operation = 'upstream_failed'
                    raise ValueError('Local engine generation failed')
                if result['status'] == 1:
                    operation = 'validating'
                    self.update(jid, status='ingesting', phase='validating')
                    data = self.engine.fetch_wav(result['result'])
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(data)
                    signal = decode(output)
                    if abs(len(signal) / 48000 - 20) > .25 or float(np.sqrt(np.mean(signal ** 2))) < 1e-7:
                        raise ValueError('Audio duration or signal is invalid')
                    self.update(jid, status='completed', phase='completed', output_key=f'generation/{jid}/output.wav',
                                error=None, error_code=None, elapsed_seconds=round(time.monotonic()-start,3))
                    terminal = True
                    break
                if result['status'] != 0:
                    raise ValueError('Unknown generation status')
                if time.monotonic() - start >= self.observation_seconds:
                    self.update(jid, status='interrupted', phase='awaiting_engine_idle', error_code='observation_timeout',
                                error='Generation is taking longer than expected. Its existing task will be checked again.')
                    break
                stopped.wait(self.poll_seconds)
        except Exception:
            logging.exception('Local generation operation failed for %s', jid)
            if submitting:
                self.update(jid, status='interrupted', phase='awaiting_engine_idle', error_code='submission_uncertain',
                            error='Submission could not be confirmed. Waiting for the local engine to become idle.')
            elif task and operation == 'rendering':
                # Transport loss while polling may leave a live accepted task.
                self.update(jid, status='interrupted', phase='awaiting_engine_idle', error_code='engine_interrupted',
                            error='The local engine connection was interrupted. The existing task is preserved.')
            else:
                error = ('A reference is no longer available. Choose another inspiration.' if operation == 'preparing'
                         else 'The local engine did not produce valid audio. Try another take.')
                self.update(jid, status='failed', phase='failed', error_code=operation,
                            error=error)
                output.unlink(missing_ok=True)
                terminal = True
        finally:
            stopped.set()
            heartbeat.join(2)
            if terminal:
                self.reference_path(jid).unlink(missing_ok=True)
        return True


def main():
    if os.getenv('MUSICBOX_AI_ENGINE', 'yue2') == 'yue2':
        from .yue2_worker import main as yue2_main
        return yue2_main()
    from .service import Service
    logging.basicConfig(level=logging.INFO)
    root = Path(__file__).resolve().parents[2]
    data = Path(os.getenv('MUSICBOX_DATA_DIR', str(root / '.data')))
    worker = GenerationWorker(Service(data, os.getenv('DATABASE_URL')), LocalMusicEngine(),
        os.getenv('MUSICBOX_AI_TEMP_DIR', str(data / 'ai-music/cache/tmp')))
    while True:
        try:
            worker.run_once()
        except KeyboardInterrupt:
            return
        except Exception:
            logging.exception('Generation worker iteration failed')
        time.sleep(2)


if __name__ == '__main__':
    main()
