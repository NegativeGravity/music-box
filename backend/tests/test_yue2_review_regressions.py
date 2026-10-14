"""Review regressions use synthesized audio/processes only, never model inference."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import wave

import numpy as np
import psutil
import pytest
from app.db import GenerationJob, Track
from app.music_generation import MusicGenerationService
from test_yue2_generation import env, submit, completed, PreparedRuntime, worker_module, fixture_launcher


def test_selection_and_save_are_serialized(env, monkeypatch):
    app, client = env
    jid = completed(app, client)
    service = app.state.service
    with service.Session() as db:
        uid = db.get(GenerationJob, jid).owner_id
    generations = MusicGenerationService(service, engine_name='yue2')
    loaded, saved = threading.Event(), threading.Event()
    original = generations.candidate
    errors = []
    def candidate(*args):
        result = original(*args)
        loaded.set()
        saved.wait(.4)  # Fixed selection holds the write lock, so concurrent save waits.
        return result
    monkeypatch.setattr(generations, 'candidate', candidate)
    def choose():
        try:
            generations.select_candidate(uid, jid, 2)
        except Exception as error:
            errors.append(error)
    def save():
        loaded.wait(3)
        try:
            generations.save(uid, jid)
        except Exception as error:
            errors.append(error)
        finally:
            saved.set()
    threads = [threading.Thread(target=choose), threading.Thread(target=save)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive()
    assert not errors, errors
    selected = generations.get(uid, jid)
    track = generations.save(uid, jid)
    assert selected['selected_candidate_index'] == 2
    assert selected['saved_track_id'] == selected['candidates'][1]['saved_track_id']
    with service.Session() as db:
        assert db.get(Track, track['id']).sha256 == hashlib.sha256(generations.candidate_output(uid, jid, 2).read_bytes()).hexdigest()


def test_launch_intent_is_durable_before_spawn_and_uncertain_attempt_is_not_retried(env):
    app, client = env
    jid = submit(client).json()['job']['id']
    observed = []
    def crash_after_spawn(config):
        with app.state.service.Session() as db:
            candidate = db.get(GenerationJob, jid).candidates[0]
            observed.append((candidate.status, getattr(candidate, 'attempt_id', None)))
        raise SystemExit('Simulated worker death at the spawn boundary')
    Worker = worker_module().Yue2Worker
    worker = Worker(app.state.service, PreparedRuntime(), launcher=crash_after_spawn)
    with pytest.raises(SystemExit):
        worker.run_once()
    assert observed[0][0] == 'starting' and observed[0][1]
    with app.state.service.Session.begin() as db:
        db.get(GenerationJob, jid).leased_until = 0
    replacement = Worker(app.state.service, PreparedRuntime(), launcher=lambda _: pytest.fail('Uncertain attempt was relaunched'))
    replacement.run_once()
    with app.state.service.Session() as db:
        assert db.get(GenerationJob, jid).candidates[0].status == 'failed'


def test_cancel_ingests_completed_manifest_before_canceling_remaining_candidates(env):
    app, client = env
    jid = submit(client).json()['job']['id']
    folder = app.state.service.path(f'generation/{jid}/candidate-1')
    folder.mkdir(parents=True)
    signal = (np.sin(np.arange(96000) * 2 * np.pi * 220 / 48000) * 8000).astype('<i2')
    with wave.open(str(folder / 'audio.wav'), 'wb') as wav:
        wav.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
        wav.writeframes(np.repeat(signal[:, None], 2, axis=1).tobytes())
    (folder / 'runner-result.json').write_text(json.dumps({'status': 'completed', 'seed': 42, 'audio': 'audio.wav'}))
    with app.state.service.Session.begin() as db:
        job = db.get(GenerationJob, jid)
        job.status = 'running'
        job.candidates[0].status = 'rendering'
    base = f'/v1/generation/jobs/{jid}'
    response = client.post(base + '/cancel')
    assert response.status_code == 200
    job = response.json()['job']
    assert job['candidates'][0]['status'] == 'completed'
    assert all(c['status'] == 'cancelled' for c in job['candidates'][1:])
    assert client.get(base + '/candidates/1/audio', headers={'Range': 'bytes=0-99'}).status_code == 206
    assert client.post(base + '/save').status_code == 200


@pytest.mark.skipif(os.name != 'nt', reason='Windows native runtime process guard')
def test_abrupt_runner_death_also_terminates_native_child(tmp_path):
    record = tmp_path / 'processes.json'
    helper = Path(__file__).parent / 'fixtures/yue2_guard_fixture.py'
    launcher = subprocess.Popen([sys.executable, str(helper), str(record)])
    identities = []
    try:
        deadline = time.monotonic() + 8
        while not record.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        assert record.exists()
        payload = json.loads(record.read_text())
        identities = [psutil.Process(pid) for pid in (payload['parent'], payload['child'])]
        identities[0].kill()  # Kill actual interpreter, including the venv launcher indirection.
        deadline = time.monotonic() + 5
        while identities[1].is_running() and time.monotonic() < deadline:
            time.sleep(.05)
        assert not identities[1].is_running(), 'Native child survived abrupt runner death'
    finally:
        for process in reversed(identities):
            try:
                if process.is_running():
                    process.kill()
            except psutil.Error:
                pass
        if launcher.poll() is None:
            launcher.kill()
        launcher.wait()
