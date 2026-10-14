"""Bounded HTTP transport to a private loopback ACE-Step service only."""
import json
import os
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise ValueError('Local engine redirects are not accepted')


def origin(url):
    parsed = urlparse(url)
    if parsed.username or parsed.password:
        raise ValueError('Engine URL must not contain credentials')
    return parsed.scheme, parsed.hostname, parsed.port or 80


class LocalMusicEngine:
    kind = 'ace-step'
    def __init__(self, base_url=None):
        self.base_url = base_url or os.getenv('MUSICBOX_AI_ENGINE_URL', 'http://127.0.0.1:8001')
        parsed = urlparse(self.base_url)
        if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1')
                or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            raise ValueError('Music generation requires a plain HTTP loopback origin')
        self.origin = origin(self.base_url)

    def read(self, url, body=None, timeout=10):
        request = Request(url, data=json.dumps(body).encode() if body is not None else None,
                          headers={'Content-Type': 'application/json'} if body is not None else {})
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=timeout) as response:
            data = response.read(64 * 1024 * 1024 + 1)
        if len(data) > 64 * 1024 * 1024:
            raise ValueError('Local engine response exceeds the audio size limit')
        return data

    def json(self, path, body=None, timeout=10):
        result = json.loads(self.read(urljoin(self.base_url, path), body, timeout))
        if result.get('code') != 200 or result.get('error'):
            raise ValueError('Local engine returned an unsuccessful response')
        return result['data']

    def health(self):
        return self.json('/health', timeout=2)

    def inventory(self):
        return self.json('/v1/model_inventory')

    def idle(self):
        data = self.json('/v1/stats', timeout=2)
        return not (data['jobs'].get('running') or data['jobs'].get('queued') or data.get('queue_size'))

    def submit(self, request):
        task = self.json('/release_task', request, timeout=30)['task_id']
        if not isinstance(task, str) or not task:
            raise ValueError('Local engine did not confirm a task ID')
        return task

    def query(self, task_id):
        data = self.json('/query_result', {'task_id_list': [task_id]})
        return next((item for item in data if item.get('task_id') == task_id), None)

    def fetch_wav(self, result):
        takes = json.loads(result) if isinstance(result, str) else result
        if not isinstance(takes, list) or not takes or takes[0].get('status') != 1:
            raise ValueError('Local engine returned no successful take')
        url = urljoin(self.base_url, takes[0]['file'])
        if origin(url) != self.origin or urlparse(url).path != '/v1/audio':
            raise ValueError('Audio must come from the same local engine')
        data = self.read(url, timeout=30)
        if data[:4] not in (b'RIFF', b'RIFX', b'RF64') or data[8:12] != b'WAVE':
            raise ValueError('Local engine did not return native WAV audio')
        return data
