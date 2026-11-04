"""First-run setup for the window: what this computer has, the download job, and the post-download speech check."""
import json
import os
import subprocess
import threading
from pathlib import Path

from . import diagnostics, locate, models, paths, speech, store

GPU_FAILED = "كارت الشاشة ما قدرش يشغّل النموذج، فالكلام هيشتغل على المعالج. تقدر تغيّر ده من الإعدادات."


def whisper_models():
    """Each Whisper model this app can download: its size, whether it is here, and whether it is the one in use."""
    current = speech.speech_model_path()
    current = Path(current).resolve() if current else None
    rows = {}
    for name, spec in models.MODELS.items():
        try:
            path = locate.validate_model_folder(models.models_dir() / name)
        except (ValueError, OSError):
            path = None
        rows[name] = {'gb': round(spec['bytes'] / 1e9, 1), 'installed': path is not None,
                      'active': path is not None and Path(path).resolve() == current, 'path': str(path) if path else None}
    return rows


def custom_model():
    """The model in use when it lives outside this app's folder, such as one the user pointed at."""
    current = speech.speech_model_path()
    if current is None or models.app_owned(current):
        return None
    return {'label': models.model_label(current).replace(' (your model)', ''), 'path': str(current)}


def checklist():
    gpu = models.gpu_info()
    ready = models.gpu_runtime_dir() is not None
    return {'gpu': gpu, 'gpuRuntimeReady': ready, 'codex': bool(locate.tool_path('codex')), 'claude': bool(locate.tool_path('claude')),
            'wisprFlow': locate.tool_path('wispr') is not None, 'freeGb': round(models.free_bytes() / 1e9, 1),
            'recommended': 'large-v3' if gpu else 'large-v3-turbo',
            'models': whisper_models(), 'customModel': custom_model(),
            'gpuDownloadGb': round(models.GPU_WHEEL['bytes'] / 1e9, 2) if gpu and not ready else 0}


def setup_needed():
    return speech.speech_model_path() is None and not store.get_setting('setup_done')


def skip():
    store.set_setting('setup_done', '1')
    return {'setupNeeded': False}


class SetupJob:
    """One download at a time, run by the `--setup` child so Cancel can stop it and keep partial files."""

    def __init__(self):
        self.lock = threading.Lock()
        self.state = {'state': 'idle', 'model': None, 'stage': None, 'done': 0, 'total': 0, 'error': None, 'device': None, 'notice': None}
        self.process = None
        self.thread = None
        self.cancelled = False

    def update(self, **values):
        with self.lock:
            self.state.update(values)

    def progress(self):
        with self.lock:
            return dict(self.state)

    def start(self, name, fresh=False):
        if name not in models.MODELS:
            raise ValueError('اختار Whisper large-v3 أو large-v3-turbo.')
        gpu_download = models.gpu_info() is not None and models.gpu_runtime_dir() is None
        target = models.models_dir() / name
        with self.lock:
            if self.state['state'] in ('downloading', 'checking'):
                raise ValueError('فيه تحميل شغال بالفعل.')
            # Bytes already here count as free: a resume reuses them, a fresh download deletes them first.
            here = models.folder_bytes(target) + (models.folder_bytes(models.runtime_dir()) if gpu_download else 0)
            need, free = models.bytes_needed(name, gpu_download), models.free_bytes() + here
            if free < need:
                raise ValueError(f'فضّي مساحة الأول: التحميل محتاج {need / 1e9:.1f} GB والمتاح {free / 1e9:.1f} GB.')
            if fresh and target.is_dir():
                models.delete_model(target)
            env = os.environ.copy()
            for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'ANTHROPIC_API_KEY'):
                env.pop(key, None)
            command = paths.self_command('--setup', str(os.getpid()), name, '1' if gpu_download else '0')
            with locate.external_runtime(), (diagnostics.logs_dir() / 'setup.log').open('wb') as log:
                self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=log,
                                                cwd=paths.DATA, env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.cancelled = False
            self.state = {'state': 'downloading', 'model': name, 'stage': 'model', 'done': 0, 'total': models.MODELS[name]['bytes'],
                          'error': None, 'device': None, 'notice': None}
            self.thread = threading.Thread(target=self.follow, args=(self.process,), daemon=True)
            self.thread.start()
        return self.progress()

    def follow(self, process):
        folder = error = None
        for raw in process.stdout:
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            if 'stage' in message:
                self.update(stage=message['stage'], done=message['done'], total=message['total'])
            folder = message.get('modelPath', folder)
            error = message.get('error', error)
        process.wait()
        if self.cancelled:
            return
        if folder is None:
            return self.update(state='error', error=error or 'التحميل وقف فجأة. جرّب تاني.')
        self.update(state='checking', stage='check')
        try:
            speech.set_whisper_model(folder)
            result = speech.check()
            notice = None
            if result['fallback']:
                store.set_setting('speech_device', 'cpu')
                notice = GPU_FAILED
            store.set_setting('setup_done', '1')
            self.update(state='done', device=result['device'], notice=notice)
        except Exception as failure:
            self.update(state='error', error=str(failure))

    def cancel(self):
        with self.lock:
            if self.state['state'] == 'downloading':
                self.cancelled = True
                self.process.kill()
                self.state['state'] = 'cancelled'
        return self.progress()


JOB = SetupJob()
