"""Measure actual local ACE-Step inference without treating valid audio as good music."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import threading
import time
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import wave


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise ValueError('Engine HTTP redirects are not accepted.')


def _origin(url: str) -> tuple:
    parsed = urlparse(url)
    if parsed.username or parsed.password:
        raise ValueError('Credentials in engine URLs are not accepted.')
    return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80)


def _read(url: str, body: dict | None = None, timeout: float = 30) -> bytes:
    request = Request(url, data=json.dumps(body).encode() if body is not None else None,
                      headers={'Content-Type': 'application/json'} if body is not None else {})
    with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=timeout) as response:
        data = response.read(64 * 1024 * 1024 + 1)
    if len(data) > 64 * 1024 * 1024:
        raise ValueError('Engine response exceeds the 64 MiB evaluation limit.')
    return data


def _json(url: str, body: dict, timeout: float) -> dict | list:
    wrapper = json.loads(_read(url, body, timeout))
    if wrapper.get('code') != 200 or wrapper.get('error'):
        raise ValueError(str(wrapper.get('error') or 'Engine returned an unsuccessful response.'))
    return wrapper['data']


def _save(path: Path, data: dict) -> None:
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def inspect_audio(path: Path) -> dict:
    result = {'valid': False, 'path': str(path), 'duration_seconds': None,
              'sample_rate': None, 'channels': None, 'peak_amplitude': None,
              'rms_amplitude': None, 'error': None}
    try:
        try:
            import soundfile as sf
            import numpy as np
        except ImportError:
            # Allows boundary checks in an application environment without extra
            # dependencies. Real inference uses the engine's soundfile decoder.
            with wave.open(str(path), 'rb') as reader:
                rate, channels, frames, width = reader.getframerate(), reader.getnchannels(), reader.getnframes(), reader.getsampwidth()
                if width not in (2, 4):
                    raise ValueError('This environment requires soundfile for this audio encoding.')
                raw = reader.readframes(frames)
            if len(raw) != frames * channels * width:
                raise ValueError('PCM audio is truncated relative to its declared frames.')
            values = [value[0] / (2 ** (8 * width - 1)) for value in struct.iter_unpack('<h' if width == 2 else '<i', raw)]
            peak = max(map(abs, values), default=0)
            rms = math.sqrt(sum(value * value for value in values) / len(values)) if values else 0
        else:
            data, rate = sf.read(str(path), dtype='float64', always_2d=True)
            frames, channels = data.shape
            if not np.isfinite(data).all():
                raise ValueError('Decoded audio contains nonfinite samples.')
            peak = float(np.abs(data).max()) if frames else 0
            rms = float(np.sqrt(np.mean(data * data))) if frames else 0
        result.update(duration_seconds=frames / rate, sample_rate=rate, channels=channels,
                      peak_amplitude=peak, rms_amplitude=rms)
        if frames == 0 or rms < 1e-7:
            raise ValueError('Audio is empty or silent.')
        result['valid'] = True
    except Exception as exc:
        result['error'] = str(exc)
    return result


def _memory_monitor(pid: int | None, stop: threading.Event, measurement: dict) -> None:
    if pid is None:
        return
    try:
        import psutil
        process = psutil.Process(pid)
        while not stop.is_set():
            processes = [process, *process.children(recursive=True)]
            rss = 0
            observed = 0
            for item in processes:
                try:
                    rss += item.memory_info().rss
                    observed += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            if observed:
                measurement['peak'] = max(measurement['peak'] or 0, rss)
            stop.wait(.5)
    except Exception:
        # Lack of a measurement must never be represented as zero memory usage.
        return


def run_probe(base_url: str, request: dict, output_dir: Path,
              timeout_seconds: int = 1800, engine_pid: int | None = None) -> dict:
    parsed = urlparse(base_url)
    if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1')
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise ValueError('The evaluation engine must be an HTTP loopback origin.')
    base_origin = _origin(base_url)
    if timeout_seconds <= 0:
        raise ValueError('Observation timeout must be positive.')
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if (output_dir / 'report.json').exists() or (output_dir / 'request.json').exists():
        raise ValueError('Use a fresh output directory; previous evaluation evidence is preserved.')
    _save(output_dir / 'request.json', request)
    started = time.monotonic()
    deadline = started + timeout_seconds
    lyrics = request.get('lyrics', '').strip()
    case = 'reference' if request.get('reference_audio_path') else ('vocal' if lyrics and lyrics != '[Instrumental]' else 'instrumental')
    report = {'case': case, 'request': request, 'task_id': None, 'status': 'failed',
              'elapsed_seconds': 0, 'peak_process_rss_bytes': None, 'audio': None,
              'error': None, 'runtime_manifest': None}
    manifest_path = next((parent.parent / 'runtime.json' for parent in output_dir.parents
                          if parent.name == 'evaluation'),
                         output_dir.parent.parent / 'runtime.json')
    if manifest_path.exists():
        report['runtime_manifest'] = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    stop = threading.Event()
    measurement = {'peak': None}
    monitor = threading.Thread(target=_memory_monitor, args=(engine_pid, stop, measurement), daemon=True)
    monitor.start()
    try:
        if request.get('reference_audio_path'):
            reference = Path(request['reference_audio_path'])
            digest = hashlib.sha256()
            with reference.open('rb') as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b''):
                    digest.update(chunk)
            _save(output_dir / 'reference.json', {'path': str(reference.resolve()),
                                                'sha256': digest.hexdigest(),
                                                'size_bytes': reference.stat().st_size})
        task = _json(urljoin(base_url, '/release_task'), request, min(30, timeout_seconds))
        report['task_id'] = task['task_id']
        if not isinstance(report['task_id'], str) or not report['task_id']:
            raise ValueError('Engine did not return a task ID.')
        report['status'] = 'running'
        _save(output_dir / 'submission.json', task)
        _save(output_dir / 'report.json', report)
        print(f"Submitted {case} task {report['task_id']}.", flush=True)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                report['status'] = 'timed_out'
                report['error'] = 'Observation deadline reached; accepted task was not resubmitted.'
                break
            statuses = _json(urljoin(base_url, '/query_result'), {'task_id_list': [report['task_id']]}, min(30, remaining))
            task_result = next((item for item in statuses if item['task_id'] == report['task_id']), None)
            if task_result is None:
                raise ValueError('Engine task is missing from the status response.')
            _save(output_dir / 'result.json', task_result)
            status = task_result['status']
            if status == 2:
                raise ValueError(str(task_result.get('result') or 'Engine generation failed.'))
            if status == 1:
                takes = task_result['result']
                takes = json.loads(takes) if isinstance(takes, str) else takes
                if not isinstance(takes, list) or not takes or takes[0].get('status') != 1:
                    raise ValueError('Engine returned no successful audio take.')
                file_url = urljoin(base_url, takes[0]['file'])
                if _origin(file_url) != base_origin or urlparse(file_url).path != '/v1/audio':
                    raise ValueError('Audio result origin or endpoint differs from the local engine.')
                content = _read(file_url, timeout=min(30, max(.01, deadline - time.monotonic())))
                if content[:4] not in (b'RIFF', b'RIFX', b'RF64') or content[8:12] != b'WAVE':
                    raise ValueError('Requested native WAV output was not returned.')
                audio_path = output_dir / 'output.wav'
                audio_path.write_bytes(content)
                report['audio'] = inspect_audio(audio_path)
                if not report['audio']['valid']:
                    raise ValueError(report['audio']['error'])
                report['status'] = 'completed'
                break
            if status != 0:
                raise ValueError(f'Engine returned unknown task status: {status}.')
            time.sleep(max(0, min(2, deadline - time.monotonic())))
    except TimeoutError as exc:
        report['status'] = 'timed_out'
        report['error'] = str(exc)
    except Exception as exc:
        report['status'] = 'failed'
        report['error'] = str(exc)
    finally:
        stop.set()
        monitor.join(timeout=2)
        report['elapsed_seconds'] = round(time.monotonic() - started, 3)
        report['peak_process_rss_bytes'] = measurement['peak']
        _save(output_dir / 'report.json', report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8001')
    parser.add_argument('--case', choices=['instrumental', 'vocal', 'reference'], required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--timeout-seconds', type=int, default=1800)
    parser.add_argument('--engine-pid', type=int)
    parser.add_argument('--reference-audio', type=Path)
    args = parser.parse_args()
    request = {'prompt': 'Soft electronic and R&B instrumental, warm analog synths, gentle drums, 96 BPM, dreamy atmosphere, no vocals.',
               'lyrics': '[Instrumental]', 'audio_duration': 20, 'audio_format': 'wav',
               'batch_size': 1, 'inference_steps': 8, 'thinking': False,
               'use_format': False, 'vocal_language': 'en', 'seed': 42, 'use_random_seed': False,
               'model': 'acestep-v15-turbo', 'task_type': 'text2music'}
    if args.case == 'vocal':
        request['prompt'] = 'Dreamy English indie pop, warm synthesizers, gentle R&B drums, intimate clear vocals, 96 BPM.'
        request['lyrics'] = '[Verse]\nCity lights are fading slow\nI find a quiet place to go\n[Chorus]\nLet the morning carry me\nInto somewhere I can breathe'
    elif args.case == 'reference':
        if args.reference_audio is None or not args.reference_audio.is_file():
            parser.error('Reference evaluation requires an existing --reference-audio file.')
        request['reference_audio_path'] = str(args.reference_audio.resolve())
        request['prompt'] = 'Soft electronic instrumental with warm synths and gentle drums, matching the reference atmosphere, no vocals.'
        request['seed'] = 84
    report = run_probe(args.base_url, request, args.output_dir, args.timeout_seconds, args.engine_pid)
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
