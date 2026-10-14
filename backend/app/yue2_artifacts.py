"""Shared, fail-closed adoption of atomically completed local variations."""
import json
from .yue2_runtime import inspect_audio


def adopt_completed(service, db, job, candidate):
    folder = service.path(f'generation/{job.id}/candidate-{candidate.index}')
    record = folder / 'runner-result.json'
    if not record.is_file():
        return False
    result = json.loads(record.read_text(encoding='utf-8'))
    if result.get('status') != 'completed':
        return False
    for reference in job.request_snapshot['references']:
        track = service.get_track(db, reference['id'], job.owner_id)
        if not track.can_analyze:
            raise ValueError('Inspiration is no longer available')
    if (result.get('audio') != 'audio.wav' or result.get('seed') != candidate.seed or
            (candidate.attempt_id and result.get('attempt_id') != candidate.attempt_id)):
        raise ValueError('Runner result does not match the candidate attempt')
    diagnostics = inspect_audio(folder / 'audio.wav', job.request_snapshot['duration_seconds'])
    candidate.status = 'completed'
    candidate.output_key = f'generation/{job.id}/candidate-{candidate.index}/audio.wav'
    candidate.diagnostics = {**diagnostics, 'duration_capped': bool(result.get('duration_capped')),
                             'elapsed_seconds': result.get('elapsed_seconds')}
    if not job.output_key:
        job.output_key, job.saved_track_id = candidate.output_key, candidate.saved_track_id
    return True
