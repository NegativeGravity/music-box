"""Native authenticated Django generation routes."""
from django.urls import path
from .http import audio_response, endpoint
from .music_generation import GenerationInput, MusicGenerationService
from .local_music_engine import LocalMusicEngine
import os


def generation_routes(service):
    if os.getenv('MUSICBOX_AI_ENGINE', 'yue2') == 'yue2':
        from .yue2_runtime import Yue2Runtime
        engine = Yue2Runtime(os.getenv('MUSICBOX_YUE2_ROOT', str(service.root / 'ai-music/yue2')))
    else:
        engine = LocalMusicEngine()
    generations = MusicGenerationService(service, engine)

    def status(user=None):
        return generations.status()

    def create(body: GenerationInput, request, user=None):
        return dict(job=generations.create(user.id, body, request.headers.get('Idempotency-Key')))

    def jobs(user=None):
        return dict(jobs=generations.list(user.id))

    def job(jid: str, user=None):
        return dict(job=generations.get(user.id, jid))

    def audio(jid: str, request, user=None):
        return audio_response(request, generations.output(user.id, jid))

    def save(jid: str, user=None):
        return dict(track=generations.save(user.id, jid))

    def candidate_audio(jid: str, index: int, request, user=None):
        return audio_response(request, generations.candidate_output(user.id, jid, index))

    def candidate_select(jid: str, index: int, user=None):
        return dict(job=generations.select_candidate(user.id, jid, index))

    def cancel(jid: str, user=None):
        return dict(job=generations.cancel(user.id, jid))

    def collection(request):
        handler = create_view if request.method == 'POST' else list_view
        return handler(request)

    create_view = endpoint('v1/generation/jobs', ['POST'], create, service, status=202).callback
    list_view = endpoint('v1/generation/jobs', ['GET'], jobs, service).callback
    return [
        endpoint('v1/generation/status', ['GET'], status, service),
        path('v1/generation/jobs', collection),
        endpoint('v1/generation/jobs/<str:jid>', ['GET'], job, service),
        endpoint('v1/generation/jobs/<str:jid>/audio', ['GET'], audio, service),
        endpoint('v1/generation/jobs/<str:jid>/save', ['POST'], save, service),
        endpoint('v1/generation/jobs/<str:jid>/candidates/<int:index>/audio', ['GET'], candidate_audio, service),
        endpoint('v1/generation/jobs/<str:jid>/candidates/<int:index>/select', ['POST'], candidate_select, service),
        endpoint('v1/generation/jobs/<str:jid>/cancel', ['POST'], cancel, service),
    ]
