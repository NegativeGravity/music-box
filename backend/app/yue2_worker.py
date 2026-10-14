"""Durable sequential YuE2 variations, with adoption of acknowledged runners."""
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import psutil
from sqlalchemy import select, text
from .db import GenerationJob
from .music_generation import MusicGenerationService
from .yue2_runner import atomic_json
from .yue2_runtime import Yue2Runtime
from .yue2_artifacts import adopt_completed


def worker_available(service):
    try:
        record = json.loads((service.root / 'yue2-worker.json').read_text())
        process = psutil.Process(record['pid'])
        return (0 <= time.time() - record['heartbeat'] < 15 and
                abs(process.create_time() - record['started_at']) < .1)
    except (OSError, ValueError, KeyError, psutil.Error):
        return False


def spawn_runner(config):
    env = os.environ.copy()
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
    with (config.parent / 'runner.log').open('ab') as log:
        return subprocess.Popen([sys.executable, '-m', 'app.yue2_runner', '--config', str(config)],
            env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


class Yue2Worker:
    def __init__(self, service, runtime=None, launcher=spawn_runner):
        self.service = service
        self.runtime = runtime or Yue2Runtime()
        self.generations = MusicGenerationService(service, self.runtime, engine_name='yue2')
        self.identity = str(uuid.uuid4())
        self.launcher = launcher

    def folder(self, jid, index):
        return self.service.path(f'generation/{jid}/candidate-{index}')

    def process(self, jid, candidate):
        if not candidate.process_id or not candidate.process_started_at or not candidate.process_command:
            return None
        try:
            process = psutil.Process(candidate.process_id)
            config = str(self.folder(jid, candidate.index) / 'request.json')
            if (abs(process.create_time() - candidate.process_started_at) < .1 and
                    process.cmdline() == candidate.process_command and config in process.cmdline()):
                return process
        except psutil.Error:
            pass
        return None

    def stop_cancelled(self):
        with self.service.Session() as db:
            jobs = list(db.scalars(select(GenerationJob).where(GenerationJob.status.in_(['cancelled', 'failed']))))
            for job in jobs:
                for candidate in job.candidates:
                    if candidate.status != 'cancelled':
                        continue
                    process = self.process(job.id, candidate)
                    if process is not None:
                        # Only the exact acknowledged process tree belongs to this job.
                        for child in reversed(process.children(recursive=True)):
                            try:
                                child.kill()
                            except psutil.Error:
                                pass
                        try:
                            process.kill()
                        except psutil.Error:
                            pass

    def claim(self):
        db = self.generations.transaction()
        try:
            engine = GenerationJob.request_snapshot['engine'].as_string() == 'yue2'
            job = db.scalar(select(GenerationJob).where(engine, GenerationJob.status == 'running').order_by(GenerationJob.created_at))
            if job and job.leased_until > time.time() and job.lease_owner != self.identity:
                return None
            if job is None:
                job = db.scalar(select(GenerationJob).where(engine, GenerationJob.status == 'queued').order_by(GenerationJob.created_at))
            if job is None:
                return None
            job.lease_owner, job.leased_until = self.identity, time.time() + 30
            job.status, job.updated_at = 'running', time.time()
            job.started_at = job.started_at or time.time()
            candidate = next((c for c in job.candidates if c.status in ('starting', 'rendering')), None)
            candidate = candidate or next((c for c in job.candidates if c.status == 'queued'), None)
            if candidate is None:
                ready = [c for c in job.candidates if c.status == 'completed']
                job.status = 'completed' if ready else 'failed'
                job.phase = job.status
                if ready and not job.output_key:
                    job.output_key, job.saved_track_id = ready[0].output_key, ready[0].saved_track_id
                if not ready:
                    job.error = 'YuE2 did not complete any playable variations. See the local worker logs.'
                job.elapsed_seconds = round(time.time() - job.started_at, 3)
            result = (job.id, candidate.index) if candidate else None
            db.commit()
            return result
        finally:
            db.close()

    def authorize(self, db, job):
        for reference in job.request_snapshot['references']:
            track = self.service.get_track(db, reference['id'], job.owner_id)
            if not track.can_analyze:
                raise ValueError('Inspiration is no longer available')

    def launch(self, db, job, candidate):
        folder = self.folder(job.id, candidate.index)
        folder.mkdir(parents=True, exist_ok=True)
        if (folder / 'runner-result.json').exists():
            raise ValueError('A completed runner record cannot be overwritten')
        if psutil.virtual_memory().available < 8 * 1024 ** 3:
            job.phase = 'awaiting_memory'
            return
        request = {**job.request_snapshot, 'seed': candidate.seed}
        config = folder / 'request.json'
        attempt = str(uuid.uuid4())
        candidate.status, candidate.attempt_id, job.phase = 'starting', attempt, 'starting'
        # Persist the one-shot launch intent before spawn; uncertainty is never retried.
        db.commit()
        atomic_json(config, {'request': request, 'attempt_id': attempt,
                            'runtime_root': str(self.runtime.root), 'timeout_seconds': 3600})
        process = self.launcher(config)
        acknowledged = False
        try:
            # Windows venv launchers have a different PID from the interpreter.
            # The interpreter owns the kernel job; persist and adopt that identity.
            deadline = time.monotonic() + 10
            identity = None
            while time.monotonic() < deadline:
                try:
                    identity = json.loads((folder / 'runner-identity.json').read_text())
                    if identity['attempt_id'] == attempt:
                        break
                except (OSError, ValueError, KeyError):
                    pass
                time.sleep(.05)
            else:
                raise RuntimeError('Runner did not identify itself')
            owned = psutil.Process(identity['pid'])
            if (abs(owned.create_time() - identity['started_at']) >= .1 or
                    config.as_posix().replace('/', os.sep) not in owned.cmdline() or
                    (owned.pid != process.pid and process.pid not in [p.pid for p in owned.parents()])):
                raise RuntimeError('Runner identity does not belong to the spawned process')
            if self.service.engine.dialect.name == 'sqlite':
                db.execute(text('BEGIN IMMEDIATE'))
            db.expire_all()
            db.refresh(job)
            db.refresh(candidate)
            if (job.status != 'running' or job.lease_owner != self.identity or job.leased_until <= time.time() or
                    candidate.status != 'starting' or candidate.attempt_id != attempt):
                return
            candidate.process_id, candidate.process_started_at = owned.pid, owned.create_time()
            candidate.process_command = owned.cmdline()
            candidate.status, job.phase = 'rendering', 'planning' if request['mode'] == 'instrumental' else 'rendering'
            db.commit()
            atomic_json(folder / 'start.ready', {'attempt_id': attempt})
            acknowledged = True
        finally:
            if not acknowledged and process.poll() is None:
                process.kill()
                process.wait()

    def consume(self, db, job, candidate):
        folder = self.folder(job.id, candidate.index)
        record = folder / 'runner-result.json'
        if record.exists():
            result = json.loads(record.read_text(encoding='utf-8'))
            if result['status'] == 'completed':
                adopt_completed(self.service, db, job, candidate)
            else:
                candidate.status = 'cancelled' if result['status'] == 'cancelled' else 'failed'
                candidate.error = result.get('error', 'This variation was cancelled.')
        elif self.process(job.id, candidate) is None:
            candidate.status = 'failed'
            candidate.error = 'The runner was interrupted. This variation was not submitted again automatically.'
        else:
            state = folder / 'runner-state.json'
            if state.exists():
                job.phase = json.loads(state.read_text()).get('phase', 'rendering')

    def run_once(self):
        self.stop_cancelled()
        if not self.runtime.health()['models_initialized']:
            return False
        claimed = self.claim()
        if claimed is None:
            return False
        jid, index = claimed
        db = self.generations.transaction()
        try:
            job = db.get(GenerationJob, jid)
            if job.status != 'running' or job.lease_owner != self.identity:
                return False
            candidate = next(c for c in job.candidates if c.index == index)
            try:
                self.authorize(db, job)
            except Exception:
                job.status, job.phase, job.error_code = 'failed', 'failed', 'inspiration_unavailable'
                job.error = 'An inspiration is no longer available. Choose another track.'
                for item in job.candidates:
                    if item.status in ('starting', 'rendering', 'queued'):
                        cancel = self.folder(jid, item.index) / 'cancel.requested'
                        cancel.parent.mkdir(parents=True, exist_ok=True)
                        cancel.touch()
                        item.status = 'cancelled'
                db.commit()
                return True
            if candidate.status == 'queued':
                self.launch(db, job, candidate)
            else:
                self.consume(db, job, candidate)
            db.commit()
        except Exception:
            db.rollback()
            logging.exception('YuE2 variation failed for %s/%s', jid, index)
            with self.service.Session.begin() as recovery:
                job = recovery.get(GenerationJob, jid)
                if job.status == 'running' and job.lease_owner == self.identity:
                    candidate = next(c for c in job.candidates if c.index == index)
                    candidate.status = 'failed'
                    candidate.error = 'This variation could not be completed. See the local worker logs.'
        finally:
            db.close()
        return True


def main():
    from .service import Service
    logging.basicConfig(level=logging.INFO)
    root = Path(__file__).resolve().parents[2]
    data = Path(os.getenv('MUSICBOX_DATA_DIR', str(root / '.data')))
    service = Service(data, os.getenv('DATABASE_URL'))
    worker = Yue2Worker(service)
    started = psutil.Process().create_time()
    while True:
        try:
            atomic_json(data / 'yue2-worker.json', {'pid': os.getpid(), 'started_at': started, 'heartbeat': time.time()})
            worker.run_once()
        except KeyboardInterrupt:
            return
        except Exception:
            logging.exception('YuE2 worker iteration failed')
        time.sleep(2)
