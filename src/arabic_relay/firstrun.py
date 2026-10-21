"""First-run setup for the window: what this computer has, the download job, and the post-download speech check."""
import json
import os
import subprocess
import threading

from . import diagnostics, locate, models, paths, speech, store

GPU_FAILED = "The GPU couldn't run the speech model, so speech will use the CPU. You can turn this off in Settings."


def checklist():
    gpu = models.gpu_info()
    ready = models.gpu_runtime_dir() is not None
    return {'gpu': gpu, 'gpuRuntimeReady': ready, 'codex': bool(locate.tool_path('codex')), 'claude': bool(locate.tool_path('claude')),
            'wisprFlow': locate.tool_path('wispr') is not None, 'freeGb': round(models.free_bytes() / 1e9, 1),
            'recommended': 'large-v3' if gpu else 'large-v3-turbo',
            'models': {name: {'gb': round(spec['bytes'] / 1e9, 1)} for name, spec in models.MODELS.items()},
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
        self.state = {'state': 'idle', 'stage': None, 'done': 0, 'total': 0, 'error': None, 'device': None, 'notice': None}
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
            raise ValueError('Choose Whisper large-v3 or large-v3-turbo.')
        gpu_download = models.gpu_info() is not None and models.gpu_runtime_dir() is None
        target = models.models_dir() / name
        with self.lock:
            if self.state['state'] in ('downloading', 'checking'):
                raise ValueError('A download is already running.')
            # Bytes already here count as free: a resume reuses them, a fresh download deletes them first.
            here = models.folder_bytes(target) + (models.folder_bytes(models.runtime_dir()) if gpu_download else 0)
            need, free = models.bytes_needed(name, gpu_download), models.free_bytes() + here
            if free < need:
                raise ValueError(f'Free up space first: this download needs {need / 1e9:.1f} GB and {free / 1e9:.1f} GB is free.')
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
            self.state = {'state': 'downloading', 'stage': 'model', 'done': 0, 'total': models.MODELS[name]['bytes'],
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
            return self.update(state='error', error=error or 'The download stopped unexpectedly. Try again.')
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
