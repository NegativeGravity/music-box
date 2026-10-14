"""Bounded FFmpeg processing and explicitly named audio feature backends."""
import os
from pathlib import Path
import subprocess
import numpy as np
import imageio_ffmpeg

MODEL_ID = 'laion/clap-htsat-unfused'
MODEL_REVISION = '8fa0f1c6d0433df6e97c127f64b2a1d6c0dcda8a'
CLAP_VERSION = f'clap:{MODEL_REVISION}:mono48k-3x10-v1'
SPECTRAL_VERSION = 'spectral-v1:mono48k-3x10'
_clap = None


def ffmpeg(args, timeout=90):
    result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-hide_banner', '-loglevel', 'error', '-threads', '1', *args], capture_output=True, timeout=timeout)
    if result.returncode != 0:
        raise ValueError(result.stderr.decode('utf-8', errors='replace')[-800:] or 'Audio decoding failed')
    return result.stdout


def decode(path):
    raw = ffmpeg(['-xerror', '-err_detect', 'explode', '-i', str(path), '-map', '0:a:0', '-vn', '-ac', '1', '-ar', '48000', '-t', '601', '-f', 'f32le', 'pipe:1'])
    signal = np.frombuffer(raw, dtype='<f4').copy()
    if len(signal) < 4800 or not np.isfinite(signal).all():
        raise ValueError('Audio is empty, too short, or invalid')
    if len(signal) / 48000 > 600:
        raise ValueError('Maximum duration is ten minutes')
    return signal


def transcode(original, destination):
    ffmpeg(['-y', '-i', str(original), '-map', '0:a:0', '-vn', '-codec:a', 'libmp3lame', '-b:a', '128k', str(destination)])


def windows(signal):
    width = 480000
    starts = np.unique(np.linspace(0, max(0, len(signal) - width), 3, dtype=int))
    chunks = [np.pad(signal[s:s+width], (0, max(0, width-len(signal[s:s+width])))) for s in starts]
    return [chunk for chunk in chunks if np.sqrt(np.mean(chunk.astype(float)**2)) > 1e-5]


def spectral_embedding(signal):
    chunks = windows(signal)
    if not chunks:
        raise ValueError('Sampled audio windows are silent')
    vectors = []
    for chunk in chunks:
        frames = chunk[:len(chunk)//2048*2048].reshape(-1, 2048)
        powers = np.abs(np.fft.rfft(frames * np.hanning(2048), axis=1))**2
        bins = np.unique(np.geomspace(1, 1024, 65).astype(int))
        mean = powers.mean(axis=0)
        bands = np.array([np.log1p(mean[a:b].mean()) for a, b in zip(bins[:-1], bins[1:])])
        energy = np.sqrt(np.mean(frames**2, axis=1))
        rhythm = np.abs(np.fft.rfft(energy-energy.mean(), n=256))[:32]
        rhythm /= max(float(rhythm.max()), 1e-10)
        vector = np.r_[bands, rhythm, np.quantile(energy, [.1, .5, .9])]
        vectors.append(vector / max(np.linalg.norm(vector), 1e-10))
    result = np.mean(vectors, axis=0)
    return (result / np.linalg.norm(result)).tolist()


def clap_embedding(signal, cache_dir):
    global _clap
    if _clap is None:
        # This checkpoint is pinned to .bin weights. Do not start remote HF conversion jobs.
        os.environ['DISABLE_SAFETENSORS_CONVERSION']='true'
        import torch
        from transformers import ClapFeatureExtractor, ClapModel
        torch.set_num_threads(min(4, os.cpu_count() or 2))
        extractor = ClapFeatureExtractor.from_pretrained(MODEL_ID, revision=MODEL_REVISION, cache_dir=cache_dir, token=False)
        model = ClapModel.from_pretrained(MODEL_ID, revision=MODEL_REVISION, cache_dir=cache_dir, use_safetensors=False, token=False).eval()
        _clap = extractor, model
    import torch
    extractor, model = _clap
    chunks = windows(signal)
    if not chunks:
        raise ValueError('Sampled audio windows are silent')
    with torch.inference_mode():
        inputs = extractor(chunks, sampling_rate=48000, return_tensors='pt', padding='pad', truncation='rand_trunc')
        vectors = model.get_audio_features(**inputs).cpu().numpy()
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    result = vectors.mean(axis=0)
    result /= np.linalg.norm(result)
    if result.shape != (512,) or not np.isfinite(result).all():
        raise ValueError('Invalid CLAP embedding')
    return result.tolist()
