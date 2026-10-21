"""The Whisper models and the NVIDIA cuBLAS runtime: where they are, what the computer has, how to fetch them."""
import errno
import functools
import hashlib
import json
import os
from pathlib import Path
import shutil
import site
import subprocess
import sys
import threading
import urllib.request
import zipfile

from . import locate, paths

MODELS = {
    'large-v3': {'repo': 'Systran/faster-whisper-large-v3',
                 'revision': 'edaa852ec7e145841d8ffdb056a99866b5f0a478', 'bytes': 3_090_835_702},
    'large-v3-turbo': {'repo': 'mobiuslabsgmbh/faster-whisper-large-v3-turbo',
                       'revision': '0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf', 'bytes': 1_621_665_983},
}
# CTranslate2 ships its own cuDNN; GPU speech needs only these two cuBLAS DLLs from NVIDIA's official wheel.
GPU_WHEEL = {
    'url': 'https://files.pythonhosted.org/packages/20/e2/fc9a0e985249d873150276d5afb02e39a66817fedbf1a385724393e505ed/'
           'nvidia_cublas_cu12-12.9.2.10-py3-none-win_amd64.whl',
    'sha256': '623f43027d40d44ceadf0043f002bd25cf353e8f13ce90b9a87057019f560661',
    'bytes': 553_162_896,
    'files': ('nvidia/cublas/bin/cublas64_12.dll', 'nvidia/cublas/bin/cublasLt64_12.dll'),
    'extracted_bytes': 771_191_808,
}
MARGIN = 200_000_000


def models_dir():
    return paths.DATA / 'models'


def runtime_dir():
    return paths.DATA / 'runtime'


@functools.cache
def gpu_info():
    try:
        with locate.external_runtime():
            done = subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total,driver_version', '--format=csv,noheader,nounits'],
                                  capture_output=True, text=True, timeout=5, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        name, memory, driver = (part.strip() for part in done.stdout.splitlines()[0].split(','))
        if done.returncode or not name:
            return None
        return {'name': name, 'vramGb': round(int(memory) / 1024, 1), 'driver': driver}
    except Exception:
        return None


def gpu_runtime_dir():
    sites = [] if getattr(sys, 'frozen', False) else site.getsitepackages()  # the installed app has no site-packages
    candidates = [runtime_dir()] + [Path(base) / 'nvidia/cublas/bin' for base in sites]
    if os.environ.get('CUDA_PATH'):
        candidates.append(Path(os.environ['CUDA_PATH']) / 'bin')
    return next((folder for folder in candidates
                 if (folder / 'cublas64_12.dll').is_file() and (folder / 'cublasLt64_12.dll').is_file()), None)


def model_label(folder):
    name = str(folder).lower()
    if 'large-v3-turbo' in name:
        return 'Whisper large-v3-turbo'
    if 'large-v3' in name:
        return 'Whisper large-v3'
    return 'Whisper (your model)'


def app_owned(folder):
    folder, root = Path(folder).resolve(), models_dir().resolve()
    return folder != root and folder.is_relative_to(root)


def delete_model(folder):
    if not app_owned(folder):
        raise ValueError('Arabic Relay only deletes models it downloaded. Remove this folder yourself if you no longer need it.')
    shutil.rmtree(folder)


def free_bytes():
    return shutil.disk_usage(paths.DATA).free


def bytes_needed(name, gpu_download):
    gpu = GPU_WHEEL['bytes'] + GPU_WHEEL['extracted_bytes'] if gpu_download else 0
    return MODELS[name]['bytes'] + gpu + MARGIN


def fetch(url, dest, sha256, size, report):
    """Resumable download: bytes collect in `<dest>.part`, a rerun asks only for the rest, the hash gates the rename."""
    part = dest.with_suffix('.part')
    dest.parent.mkdir(parents=True, exist_ok=True)
    have = part.stat().st_size if part.exists() else 0
    if have > size:
        have = 0
    if have < size:
        request = urllib.request.Request(url, headers={'Range': f'bytes={have}-'} if have else {})
        with urllib.request.urlopen(request, timeout=60) as response:
            if response.status != 206:
                have = 0  # the server ignored the range; start over
            with part.open('ab' if have else 'wb') as out:
                while have <= size and (chunk := response.read(1 << 20)):
                    out.write(chunk)
                    have += len(chunk)
                    report('gpu', have, size)
    if have > size:  # more than promised: never fill the disk
        part.unlink()
        raise ValueError('The download was damaged. Try again.')
    digest = hashlib.sha256()
    with part.open('rb') as data:
        while chunk := data.read(1 << 20):
            digest.update(chunk)
    if digest.hexdigest() != sha256:
        part.unlink()
        raise ValueError('The download was damaged. Try again.')
    part.replace(dest)
    return dest


def install_gpu_runtime(wheel):
    """Keep only the two cuBLAS DLLs from NVIDIA's wheel; nothing of NVIDIA's is redistributed by us."""
    runtime_dir().mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(wheel) as archive:
        for name in GPU_WHEEL['files']:
            target = runtime_dir() / Path(name).name
            temporary = target.with_suffix('.tmp')
            with archive.open(name) as source, temporary.open('wb') as out:
                shutil.copyfileobj(source, out, 1 << 20)
            temporary.replace(target)
    wheel.unlink()
    return runtime_dir()


def download_model(name, report):
    from huggingface_hub import snapshot_download
    spec, target = MODELS[name], models_dir() / name
    target.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()

    def watch():  # the library resumes .incomplete files itself; report what is on disk
        while not stop.wait(0.5):
            report('model', min(folder_bytes(target), spec['bytes']), spec['bytes'])
    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        snapshot_download(repo_id=spec['repo'], revision=spec['revision'], local_dir=str(target), token=False,
                          allow_patterns=['config.json', 'model.bin', 'tokenizer.json', 'vocabulary.*', 'preprocessor_config.json'])
    finally:
        stop.set()
        watcher.join()  # no progress line may land after (or inside) the final modelPath line
    return locate.validate_model_folder(target)


def folder_bytes(folder):
    """Bytes on disk under `folder`; files renamed or removed mid-count are skipped."""
    total = 0
    for item in Path(folder).rglob('*'):
        try:
            total += item.stat().st_size if item.is_file() else 0
        except OSError:
            continue
    return total


NETWORK_ERROR = 'The download stopped. Check your internet connection and try again; it resumes where it left off.'
DISK_FULL = 'The disk is full. Free up space and try again; the download resumes where it left off.'


def setup_main(args):
    """`--setup <parent pid> <model> <1|0>`: download the model (and cuBLAS when 1), one JSON line per update."""
    os.environ['HF_HUB_DISABLE_XET'] = '1'  # plain HTTPS with resumable .incomplete files
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    os.environ['DO_NOT_TRACK'] = '1'

    def say(**message):
        print(json.dumps(message), flush=True)
    try:
        from .speech_worker import watch_parent
        watch_parent(int(args[0]))  # closing the window stops the download; partial files stay for the next try
        name = args[1]
        if name not in MODELS:
            raise ValueError('Choose Whisper large-v3 or large-v3-turbo.')
        folder = download_model(name, lambda stage, done, total: say(stage=stage, done=done, total=total))
        if args[2] == '1' and gpu_runtime_dir() is None:
            wheel = fetch(GPU_WHEEL['url'], runtime_dir() / 'cublas.whl', GPU_WHEEL['sha256'], GPU_WHEEL['bytes'],
                          lambda stage, done, total: say(stage=stage, done=done, total=total))
            install_gpu_runtime(wheel)
        say(modelPath=str(folder))
        return 0
    except Exception as error:
        print(f'{type(error).__name__}: {str(error)[:800]}', file=sys.stderr, flush=True)
        if isinstance(error, OSError):
            say(error=DISK_FULL if error.errno == errno.ENOSPC else NETWORK_ERROR)
        else:
            say(error=str(error) if isinstance(error, ValueError) else 'The download failed. Try again.')
        return 1
