"""The three speech choices: local Whisper (via speech_worker.py), Cohere, and Wispr Flow."""
import io
import json
import logging
import os
import secrets
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import wave
import webbrowser
from concurrent.futures import ThreadPoolExecutor

from . import locate, models, paths, store


TRANSCRIBE_LOCK = threading.Lock()

COHERE_MODEL = 'cohere-transcribe-arabic-07-2026'


MAX_AUDIO = 24 * 1024 * 1024


def speech_model_path():
    return locate.whisper_model(store.get_setting('whisper_model_dir'))


def set_whisper_model(folder):
    if not isinstance(folder, str) or not folder.strip() or len(folder) > 1000:
        raise ValueError('Choose a model folder.')
    model = locate.validate_model_folder(folder.strip())
    store.set_setting('whisper_model_dir', str(model))
    return {'speechConfigured': True, 'modelPath': str(model)}


def speech_device():
    if store.get_setting('speech_device') == 'cpu' or models.gpu_info() is None or models.gpu_runtime_dir() is None:
        return 'cpu'
    return 'cuda'


def set_cpu_only(enabled):
    if not isinstance(enabled, bool):
        raise ValueError('Choose whether speech always uses the CPU.')
    store.set_setting('speech_device', 'cpu' if enabled else 'auto')
    return {'cpuOnly': enabled}


def missing_model():
    """The saved model folder when it was moved or deleted since it was chosen."""
    saved = store.get_setting('whisper_model_dir')
    if not saved:
        return None
    try:
        locate.validate_model_folder(saved)
        return None
    except (ValueError, OSError):
        return saved


def delete_current_model():
    current = speech_model_path()
    if current is None:
        raise ValueError('No speech model is set up.')
    models.delete_model(current)
    store.set_setting('whisper_model_dir', '')
    return {'speechConfigured': speech_model_path() is not None}


def status():
    model = speech_model_path()
    gpu = models.gpu_info()
    return {'speechConfigured': model is not None, 'speechModel': models.model_label(model) if model else None,
            'modelPath': str(model) if model else None, 'speechDevice': 'GPU' if speech_device() == 'cuda' else 'CPU',
            'cpuOnly': store.get_setting('speech_device') == 'cpu', 'gpu': gpu['name'] if gpu else None,
            'modelOwned': model is not None and models.app_owned(model), 'missingModel': missing_model()}


def open_wispr_flow():
    executable = locate.tool_path('wispr')
    with locate.external_runtime():
        if executable:
            subprocess.Popen([str(executable)], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        elif not webbrowser.open('https://wisprflow.ai/downloads'):
            raise ValueError('Open wisprflow.ai/downloads in your browser to install Wispr Flow.')
    return {'installed': executable is not None}


def load_cohere_key():
    with store.AUTH_LOCK:
        path = paths.DATA / 'cohere.bin'
        try:
            return store.protect(path.read_bytes(), True).decode() if path.exists() else ''
        except Exception:
            raise ValueError('Re-enter your Cohere API key in Speech details.') from None


def save_cohere_key(key):
    if not isinstance(key, str) or len(key) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in key.strip()):
        raise ValueError('Enter a Cohere API key without spaces or line breaks.')
    key = key.strip()
    with store.AUTH_LOCK:
        path = paths.DATA / 'cohere.bin'
        if key:
            temporary = paths.DATA / 'cohere.tmp'
            temporary.write_bytes(store.protect(key.encode()))
            temporary.replace(path)
        else:
            path.unlink(missing_ok=True)
    return {'configured': bool(key)}


def transcript_result(text, model):
    if not isinstance(text, str) or not text.strip():
        raise ValueError('No speech was detected. Record again or type your transcript.')
    if len(text) > 12000:
        raise ValueError('This transcript is too long. Use a shorter recording.')
    raw = text.strip()
    # Return transcription immediately. Optional correction is a separate request after the UI displays it.
    return {'text': raw, 'rawText': raw, 'termsRestored': False, 'model': model}


class NoCohereRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a recording or bearer key to a redirect target.
        return None


def cohere_clips(audio):
    """Split recordings over two minutes at quiet pauses; preserve every PCM sample in order."""
    try:
        with wave.open(io.BytesIO(audio), 'rb') as clip:
            frames = clip.getnframes()
            pcm = clip.readframes(frames)
            if (clip.getnchannels() != 1 or clip.getsampwidth() != 2 or clip.getframerate() != 16000
                    or not 0 < frames <= 300 * 16000 or len(pcm) != frames * 2):
                raise ValueError()
    except (ValueError, EOFError, wave.Error):
        raise ValueError('Use a valid mono WAV recording under five minutes.') from None
    if frames <= 120 * 16000:
        return [audio]
    import numpy as np
    samples = np.frombuffer(pcm, dtype='<i2').astype(np.float32)
    windows = samples[:frames // 320 * 320].reshape(-1, 320)
    quiet = np.sqrt(np.mean(windows * windows, axis=1)) < 100
    pauses, start = [], None
    for index, silent in enumerate(quiet):
        if silent and start is None:
            start = index
        if not silent and start is not None:
            if index - start >= 15:  # At least 300 ms quiet; never cut continuous speech to meet a timer.
                pauses.append((start + index) // 2 * 320)
            start = None
    cuts = [0]
    while frames - cuts[-1] > 80 * 16000:
        candidates = [point for point in pauses if cuts[-1] + 40 * 16000 <= point <= cuts[-1] + 80 * 16000
                      and frames - point >= 15 * 16000]
        if not candidates:
            break
        cuts.append(min(candidates, key=lambda point: abs(point - cuts[-1] - 60 * 16000)))
    if len(cuts) == 1:
        return [audio]
    cuts.append(frames)
    clips = []
    for first, last in zip(cuts, cuts[1:]):
        output = io.BytesIO()
        with wave.open(output, 'wb') as clip:
            clip.setnchannels(1)
            clip.setsampwidth(2)
            clip.setframerate(16000)
            clip.writeframes(pcm[first * 2:last * 2])
        clips.append(output.getvalue())
    return clips


def transcribe_cohere(audio, kind):
    if kind != 'audio/wav':
        raise ValueError('Cohere needs WAV audio. Record again using the Cohere option.')
    clips = cohere_clips(audio)
    key = load_cohere_key()
    if not key:
        raise ValueError('Add your Cohere API key in Speech details before recording.')
    if not TRANSCRIBE_LOCK.acquire(blocking=False):
        raise ValueError('A recording is already being transcribed. Wait for it to finish.')
    try:
        if len(clips) == 1:
            text = cohere_request(clips[0], key)
        else:
            pool = ThreadPoolExecutor(max_workers=2)
            try:
                # map retains audio order even when the second request finishes first.
                text = ' '.join(pool.map(lambda clip: cohere_request(clip, key), clips)).strip()
            finally:
                pool.shutdown(wait=True, cancel_futures=True)
        return transcript_result(text, COHERE_MODEL)
    finally:
        TRANSCRIBE_LOCK.release()


def cohere_request(audio, key):
    """One request, never retried. An empty section is allowed; the combined transcript must contain speech."""
    boundary = 'relay-' + secrets.token_hex(24)
    fields = [('model', COHERE_MODEL), ('language', 'ar')]
    body = b''.join((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n').encode()
                    for name, value in fields)
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="dictation.wav"\r\n'
             'Content-Type: audio/wav\r\n\r\n').encode() + audio + f'\r\n--{boundary}--\r\n'.encode()
    request = urllib.request.Request('https://api.cohere.com/v2/audio/transcriptions', data=body,
                headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'multipart/form-data; boundary=' + boundary,
                         'Accept': 'application/json'}, method='POST')
    try:
        with urllib.request.build_opener(NoCohereRedirect()).open(request, timeout=120) as response:
            raw = response.read(128001)
        if len(raw) > 128000:
            raise ValueError()
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError()
    except urllib.error.HTTPError as error:
        messages = {401: 'Cohere rejected the API key. Replace it in Speech details.',
                    403: 'This Cohere key does not have access to Arabic transcription.',
                    498: 'Cohere rejected the API key. Replace it in Speech details.',
                    429: 'Cohere reached an account or rate limit. Retry manually later.'}
        raise ValueError(messages.get(error.code, 'Cohere could not transcribe this recording. Retry manually later.')) from None
    except (OSError, ValueError):
        raise ValueError('Cohere could not complete transcription. Check your connection and retry manually.') from None
    text = result.get('text')
    if not isinstance(text, str) or len(text) > 12000:
        raise ValueError('Cohere returned an invalid transcript. Retry manually.')
    return text.strip()


def transcribe(audio, content_type, vocabulary='', provider='whisper'):
    if provider not in ('whisper', 'cohere'):
        raise ValueError('Choose Local Whisper or Cohere for microphone transcription.')
    formats = {'audio/webm': ('webm', lambda b: b.startswith(b'\x1aE\xdf\xa3')),
               'audio/wav': ('wav', lambda b: b.startswith(b'RIFF') and b[8:12] == b'WAVE'),
               'audio/mp4': ('mp4', lambda b: b[4:8] == b'ftyp')}
    kind = content_type.split(';', 1)[0].strip().lower()
    if kind not in formats or not 0 < len(audio) <= MAX_AUDIO or not formats[kind][1](audio):
        raise ValueError('Record a valid WebM, WAV, or MP4 clip under 24 MB.')
    if not isinstance(vocabulary, str) or len(vocabulary) > 3000:
        raise ValueError('Keep the vocabulary hints under 3,000 characters.')
    keywords = [word.strip() for word in vocabulary.replace('\n', ',').split(',') if word.strip()]
    if len(keywords) > 40 or any(len(word) > 80 or any(c in word for c in '<>\r\n') for word in keywords):
        raise ValueError('Use up to 40 short vocabulary hints, separated by commas.')
    # Explicit hints have priority. Keep Whisper's context short to avoid over-biasing it.
    keywords = list(dict.fromkeys(keywords + store.personal_vocabulary()['words']))[:40]
    if provider == 'cohere':
        return transcribe_cohere(audio, kind)
    folder = speech_model_path()
    if folder is None:
        raise ValueError('Choose a Whisper model folder in Speech details, then click Refresh.')
    if not TRANSCRIBE_LOCK.acquire(blocking=False):
        raise ValueError('A recording is already being transcribed. Wait for it to finish.')
    try:
        result = run_worker(folder, audio, keywords)
    finally:
        TRANSCRIBE_LOCK.release()
    response = transcript_result(result.get('text'), models.model_label(folder))
    response['device'] = result.get('device', 'GPU')
    if result.get('fallback'):
        response['notice'] = 'The GPU was unavailable, so this recording ran on the CPU.'
    return response


def run_worker(folder, audio, keywords, check=False):
    """One worker launch per recording; a crash (no error code) gets one automatic relaunch, on the CPU if CUDA crashed."""
    device = speech_device()
    env = os.environ.copy()
    env['HF_HUB_OFFLINE'] = '1'
    env['HF_HUB_DISABLE_TELEMETRY'] = '1'
    for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'ANTHROPIC_API_KEY'):
        env.pop(key, None)
    header = {'keywords': keywords, **({'check': True} if check else {})}
    for attempt in (1, 2):
        # A CUDA driver fault can kill the worker before Python can fall back; the relaunch avoids the GPU.
        launched = device if attempt == 1 else 'cpu'
        command = paths.self_command('--speech-worker', str(os.getpid()), str(folder), launched)
        with locate.external_runtime():
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       cwd=paths.DATA, env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            stdout, stderr = process.communicate(json.dumps(header).encode() + b'\n' + audio, timeout=300)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise ValueError('Local transcription took too long. Your recording can be saved or retried manually.') from None
        try:
            result = json.loads(stdout)
        except (ValueError, UnicodeDecodeError):
            result = {}
        if not isinstance(result, dict):
            result = {}
        if not process.returncode and not result.get('error'):
            if launched != device:
                result['fallback'] = True
            return result
        if result.get('error'):
            messages = {'AUDIO_TOO_LONG': 'Use a recording under five minutes.',
                        'INVALID_AUDIO': 'This recording could not be decoded. Record again or type your transcript.'}
            raise ValueError(messages.get(result['error'], 'Local speech failed on this recording. Record again; if it keeps failing, turn on Always use CPU in Settings.'))
        logging.getLogger(__name__).error('Speech worker stopped (exit %s, attempt %s): %s', process.returncode, attempt,
                                          (stderr or b'')[-2000:].decode(errors='replace'))
    raise ValueError('The speech engine stopped. Record again to restart it.')


def silent_wav(seconds=2):
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(16000)
        clip.writeframes(b'\0\0' * 16000 * seconds)
    return buffer.getvalue()


def check():
    """The post-download test: two seconds of silence through the real worker. Reports the device that worked."""
    folder = speech_model_path()
    if folder is None:
        raise ValueError('The speech model is missing. Download it again.')
    if not TRANSCRIBE_LOCK.acquire(blocking=False):
        raise ValueError('A recording is being transcribed. Try again when it finishes.')
    try:
        result = run_worker(folder, silent_wav(), [], check=True)
    finally:
        TRANSCRIBE_LOCK.release()
    return {'device': result.get('device', 'GPU'), 'fallback': bool(result.get('fallback'))}
