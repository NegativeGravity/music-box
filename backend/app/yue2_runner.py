"""Isolated native inference with an atomic completion record for crash recovery."""
import argparse
import json
import logging
from pathlib import Path
import subprocess
import time

import psutil
from .yue2_runtime import Yue2Runtime, inspect_audio
from .yue2_score import instrumental_score


def atomic_json(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value), encoding='utf-8')
    temp.replace(path)


class Cancelled(Exception):
    pass


def execute(command, folder, timeout):
    if (folder / 'cancel.requested').exists():
        raise Cancelled()
    if psutil.virtual_memory().available < 8 * 1024 ** 3:
        raise MemoryError('At least 8 GiB available memory is required')
    with (folder / 'native.log').open('ab') as log:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        began = time.monotonic()
        try:
            while process.poll() is None:
                if (folder / 'cancel.requested').exists():
                    raise Cancelled()
                if time.monotonic() - began > timeout:
                    raise TimeoutError('Native generation exceeded its time limit')
                if psutil.virtual_memory().available < 2 * 1024 ** 3:
                    raise MemoryError('Available memory fell below the safety reserve')
                time.sleep(.5)
            if process.returncode:
                raise RuntimeError(f'Native generation exited with code {process.returncode}')
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()


def run_candidate(config_path, runtime=None):
    folder = config_path.resolve().parent
    began = time.monotonic()
    attempt = None
    try:
        config = json.loads(config_path.read_text(encoding='utf-8'))
        attempt = config['attempt_id']
        import os
        atomic_json(folder / 'runner-identity.json', {'pid': os.getpid(),
                    'started_at': psutil.Process().create_time(), 'attempt_id': attempt})
        # Parent acknowledges its durable PID record before inference may start.
        while True:
            if (folder / 'cancel.requested').exists():
                raise Cancelled()
            if time.monotonic() - began > 30:
                raise RuntimeError('Worker did not acknowledge this runner')
            try:
                if json.loads((folder / 'start.ready').read_text())['attempt_id'] == attempt:
                    break
            except (OSError, ValueError, KeyError):
                pass
            time.sleep(.1)
        if (folder / 'cancel.requested').exists():
            raise Cancelled()
        runtime = runtime or Yue2Runtime(config['runtime_root'])
        request = config['request']
        timeout = int(config.get('timeout_seconds', 3600))
        score = None
        if request['mode'] == 'instrumental':
            atomic_json(folder / 'runner-state.json', {'phase': 'planning'})
            execute(runtime.command(request, folder, plan_only=True), folder, timeout)
            planned = folder / 'score.abc'
            score = folder / 'instrumental.abc'
            score.write_text(instrumental_score(planned.read_text(encoding='utf-8')), encoding='utf-8')
        atomic_json(folder / 'runner-state.json', {'phase': 'rendering'})
        execute(runtime.command(request, folder, score=score), folder, timeout)
        if (folder / 'cancel.requested').exists():
            raise Cancelled()
        audio = folder / 'audio.wav'
        diagnostics = inspect_audio(audio, request['duration_seconds'])
        score_path = score or folder / 'score.abc'
        result = {'status': 'completed', 'audio': 'audio.wav', 'score': score_path.name if score_path.exists() else None,
                  'diagnostics': diagnostics, 'seed': request['seed'],
                  'duration_capped': diagnostics['duration_seconds'] >= request['duration_seconds'] - .2,
                  'elapsed_seconds': round(time.monotonic() - began, 3)}
    except Cancelled:
        result = {'status': 'cancelled'}
    except Exception as error:
        logging.exception('YuE2 runner failed')
        result = {'status': 'failed', 'error_code': type(error).__name__,
                  'error': 'YuE2 could not complete this variation. See the private local runner log.'}
    result['attempt_id'] = attempt
    atomic_json(folder / 'runner-result.json', result)


if __name__ == '__main__':
    from .yue2_process_guard import bind_runner_job
    bind_runner_job()  # Bind the actual interpreter before any native child can start.
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    run_candidate(parser.parse_args().config)
