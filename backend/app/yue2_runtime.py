"""Pinned local YuE2 native runtime; no cloud transport or model auto-download."""
import json
import os
from pathlib import Path
import numpy as np
from .audio import decode

MODEL = 'YuE2-3B-Q8'
REVISION = 'cca74d8980c00f30ab8d52248a367c01ad633ec2'
RELEASE = 'v0.9.1'
LANGUAGES = ['en', 'zh']


class Yue2Runtime:
    kind = 'yue2'
    def __init__(self, root=None):
        data = Path(os.getenv('MUSICBOX_DATA_DIR', Path(__file__).resolve().parents[2] / '.data'))
        self.root = Path(root or os.getenv('MUSICBOX_YUE2_ROOT', data / 'ai-music/yue2')).resolve()
        self.cli = self.root / 'runtime/audiocpp_cli.exe'
        self.models = self.root / 'models'

    def health(self):
        try:
            manifest = json.loads((self.root / 'runtime.json').read_text(encoding='utf-8-sig'))
            files = manifest['files']
            required = {'runtime/audiocpp_cli.exe', 'models/yue2-3b-q8_0.gguf',
                        'models/yue2-vae-f16.gguf', 'models/sidecars/yue2-model-config.json',
                        'models/sidecars/yue2-generation-config.json',
                        'models/sidecars/yue2-vae-config.json', 'models/sidecars/yue2-qwen.tiktoken'}
            ready = (manifest.get('verified') is True and manifest.get('runtime_release') == RELEASE and
                     manifest.get('runtime_variant') == 'cpu-portable' and
                     manifest.get('model_revision') == REVISION and required <= {f['path'] for f in files})
            for item in files:
                path = (self.root / item['path']).resolve()
                ready = (ready and path.is_relative_to(self.root) and path.is_file() and
                         path.stat().st_size == item['size'] and path.stat().st_mtime_ns == item.get('mtime_ns'))
        except (OSError, KeyError, TypeError, ValueError):
            ready = False
        return {'models_initialized': bool(ready), 'model': MODEL, 'loaded': False}

    def command(self, request, folder, *, score=None, plan_only=False):
        folder = Path(folder)
        language = {'en': 'English', 'zh': 'Chinese'}[request['language']]
        style = language + ', ' + request['caption']
        lyrics = request['lyrics'] if request['mode'] == 'vocal' else '[instrumental]'
        if request['mode'] == 'instrumental':
            style += ', instrumental only, no singing, no spoken words'
        options = ['style=' + style, 'cot=full', 'num_inference_steps=8',
                   f"semantic_min_tokens={min(250, request['duration_seconds'] * 25)}",
                   f"semantic_max_tokens={request['duration_seconds'] * 25}",
                   'abc_max_tokens=2048']
        if plan_only:
            options.append('stop_after=abc')
        if score is not None:
            options.append('abc_file=' + str(score))
        cmd = [str(self.cli), '--task', 'gen', '--family', 'yue2', '--model', str(self.models),
               '--backend', 'cpu', '--threads', '4', '--lyrics', lyrics,
               '--seed', str(request['seed']), '--out', str(folder / 'audio.wav'),
               '--out-dir', str(folder), '--log']
        for option in options:
            cmd.extend(['--request-option', option])
        return cmd


def inspect_audio(path, max_seconds):
    if not path.is_file() or path.stat().st_size > 100 * 1024 * 1024:
        raise ValueError('Native audio is missing or exceeds the size limit')
    signal = decode(path)
    duration = len(signal) / 48000
    rms = float(np.sqrt(np.mean(signal.astype(float) ** 2)))
    if rms < 1e-7:
        raise ValueError('Native audio is silent')
    if not 1 <= duration <= max_seconds + .5:
        raise ValueError('Native audio duration is outside the requested limit')
    return {'duration_seconds': duration, 'rms': rms,
            'clipped_fraction': float(np.mean(np.abs(signal) >= .999))}
