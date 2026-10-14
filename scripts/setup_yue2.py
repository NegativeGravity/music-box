"""Install a pinned, checksum-verified Windows CPU runtime and YuE2 weights."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import urllib.request
import zipfile

RELEASE = 'v0.9.1'
REVISION = 'cca74d8980c00f30ab8d52248a367c01ad633ec2'
REPO = 'audio-cpp/Yue2-3B-GGUF'
RUNTIME_ARCHIVE = f'audio-{RELEASE}-bin-windows-x64-cpu-portable.zip'
ZIP_HASH = '2fccc73dc97af84da1e81c3af545fa619348a46d023ea7d927ba80bc8680d2a0'
MODEL_FILES = ['yue2-3b-q8_0.gguf', 'yue2-vae-f16.gguf',
               'sidecars/yue2-generation-config.json', 'sidecars/yue2-model-config.json',
               'sidecars/yue2-qwen.tiktoken', 'sidecars/yue2-vae-config.json']


def verify_file(path, *, size=None, sha256=None, blob_id=None):
    if size is not None and path.stat().st_size != size:
        raise ValueError(f'Asset size mismatch: {path.name}')
    with path.open('rb') as content:
        digest = hashlib.file_digest(content, 'sha256').hexdigest()
    if sha256 is not None and digest != sha256:
        raise ValueError(f'Asset checksum mismatch: {path.name}')
    if blob_id is not None:
        blob = hashlib.sha1(f'blob {path.stat().st_size}\0'.encode() + path.read_bytes()).hexdigest()
        if blob != blob_id:
            raise ValueError(f'Asset git checksum mismatch: {path.name}')
    return digest


def download(url, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + '.part')
    curl = shutil.which('curl.exe') or shutil.which('curl')
    if not curl:
        raise RuntimeError('curl is required for resumable downloads')
    subprocess.run([curl, '-f', '-L', '--retry', '5', '--connect-timeout', '30',
                    '--max-time', '3600', '-C', '-', '-o', str(partial), url], check=True)
    return partial


def install_runtime_archive(archive, native):
    """Replace host-specific/corrupt files using the already verified archive."""
    native = native.resolve()
    with zipfile.ZipFile(archive) as zipped:
        entries = zipped.infolist()
        for entry in entries:
            if not (native / entry.filename).resolve().is_relative_to(native):
                raise ValueError('Unsafe runtime archive entry')
        for entry in entries:
            if entry.is_dir():
                continue
            path = native / entry.filename
            data = zipped.read(entry)
            expected = hashlib.sha256(data).hexdigest()
            matches = path.is_file() and path.stat().st_size == entry.file_size
            if matches:
                matches = verify_file(path) == expected
            if not matches:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_name(path.name + '.installing')
                temporary.write_bytes(data)
                temporary.replace(path)
            verify_file(path, size=entry.file_size, sha256=expected)


def prepare(root):
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    invalid = root / 'runtime.json.tmp'
    invalid.write_text('{"verified":false}', encoding='utf-8')
    invalid.replace(root / 'runtime.json')
    archive = root / RUNTIME_ARCHIVE
    if not archive.exists():
        partial = download(f'https://github.com/0xShug0/audio.cpp/releases/download/{RELEASE}/{RUNTIME_ARCHIVE}', archive)
        verify_file(partial, sha256=ZIP_HASH)
        partial.replace(archive)
    verify_file(archive, sha256=ZIP_HASH)
    native = root / 'runtime'
    install_runtime_archive(archive, native)
    with urllib.request.urlopen(f'https://huggingface.co/api/models/{REPO}/revision/{REVISION}?blobs=true', timeout=60) as response:
        metadata = json.load(response)
    if metadata['sha'] != REVISION:
        raise ValueError('Unexpected model revision')
    lookup = {f['rfilename']: f for f in metadata['siblings']}
    verified = []
    for name in MODEL_FILES:
        entry = lookup[name]
        path = root / 'models' / name
        if not path.is_file():
            partial = download(f'https://huggingface.co/{REPO}/resolve/{REVISION}/{name}?download=true', path)
            verify_file(partial, size=entry['size'], sha256=entry.get('lfs', {}).get('sha256'),
                        blob_id=None if entry.get('lfs') else entry.get('blobId'))
            partial.replace(path)
        digest = verify_file(path, size=entry['size'], sha256=entry.get('lfs', {}).get('sha256'),
                             blob_id=None if entry.get('lfs') else entry.get('blobId'))
        verified.append({'path': path.relative_to(root).as_posix(), 'size': path.stat().st_size,
                         'mtime_ns': path.stat().st_mtime_ns, 'sha256': digest})
        print('Verified ' + name, flush=True)
    for path in native.rglob('*'):
        if path.is_file():
            verified.append({'path': path.relative_to(root).as_posix(), 'size': path.stat().st_size,
                             'mtime_ns': path.stat().st_mtime_ns, 'sha256': verify_file(path)})
    manifest = {'verified': True, 'engine': 'yue2', 'model': 'YuE2-3B-Q8', 'runtime_release': RELEASE,
                'runtime_variant': 'cpu-portable', 'runtime_archive_sha256': ZIP_HASH,
                'model_repository': REPO, 'model_revision': REVISION, 'device': 'cpu',
                'files': verified, 'preliminary_generation_test': 'skipped at user request'}
    temp = root / 'runtime.json.tmp'
    temp.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    temp.replace(root / 'runtime.json')
    print('YuE2 CPU assets are installed. No model inference was run.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    prepare(parser.parse_args().root)
