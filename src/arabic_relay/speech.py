"""The three speech choices: local Whisper (via speech_worker.py), Cohere, and Wispr Flow."""
import io
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import wave
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import aliases, engine_host, incremental, locate, models, paths, store


TRANSCRIBE_LOCK = threading.Lock()
PREPARE_LOCK = threading.Lock()
PREPARED = None


def worker_environment():
    env = os.environ.copy()
    env['HF_HUB_OFFLINE'] = '1'
    env['HF_HUB_DISABLE_TELEMETRY'] = '1'
    for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'ANTHROPIC_API_KEY'):
        env.pop(key, None)
    return env


engine_host.HOST.environment = worker_environment


def cancel_preparation(request_id=None):
    """Discard only an unclaimed worker. A transcription already in progress owns its process."""
    global PREPARED
    with PREPARE_LOCK:
        if PREPARED is None or (request_id is not None and PREPARED[0] != request_id):
            return {'prepared': False}
        _, _, process, timer = PREPARED
        PREPARED = None
        timer.cancel()
    if process.poll() is None:
        process.kill()
    process.communicate()
    return {'prepared': False}


def prepare_worker(request_id):
    """Load weights while the microphone records; no audio is collected or sent here."""
    global PREPARED
    if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{20,100}', request_id):
        raise ValueError('Invalid recording request.')
    folder, device = speech_model_path(), speech_device()
    if folder is None:
        return {'prepared': False}
    if engine_enabled():
        # The reusable worker loads and warms while the microphone records; a loaded model is simply reused.
        # Never replace the model another window's recording is using.
        if not TRANSCRIBE_LOCK.locked() or engine_host.HOST.key == (str(folder), device):
            engine_host.HOST.ensure(folder, device)
        return {'prepared': True, 'engine': engine_host.HOST.status()}
    with PREPARE_LOCK:
        if TRANSCRIBE_LOCK.locked():
            return {'prepared': False}
        # Another window may already have a recording underway; never allocate a second model.
        if PREPARED is not None:
            return {'prepared': PREPARED[0] == request_id}
        command = paths.self_command('--speech-worker', str(os.getpid()), str(folder), device, '--preload')
        with locate.external_runtime():
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       cwd=paths.DATA, env=worker_environment(),
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        timer = threading.Timer(360, cancel_preparation, args=(request_id,))
        timer.daemon = True
        PREPARED = (request_id, (str(folder), device), process, timer)
        timer.start()
    return {'prepared': True}


def take_prepared_worker(folder, device):
    global PREPARED
    with PREPARE_LOCK:
        if PREPARED is None:
            return None
        _, key, process, timer = PREPARED
        PREPARED = None
        timer.cancel()
    if key == (str(folder), device) and process.poll() is None:
        return process
    if process.poll() is None:
        process.kill()
    process.communicate()
    return None

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


def gpu_backoff():
    """Bounded failure memory for Auto device choice, keyed by device and driver."""
    try:
        saved = json.loads(store.get_setting('gpu_backoff') or 'null')
    except ValueError:
        return None
    if not isinstance(saved, dict) or not isinstance(saved.get('until'), (int, float)):
        return None
    gpu = models.gpu_info() or {}
    if saved.get('driver') != gpu.get('driver') or saved.get('name') != gpu.get('name'):
        return None                                  # a different device or driver starts clean
    return saved if saved['until'] > time.time() else None


def record_gpu_failure(reason, key=None):
    """A device or driver fault suspends Auto GPU use for a while; a transient OOM never does."""
    if reason not in ('device', 'load'):
        return
    gpu = models.gpu_info() or {}
    previous = gpu_backoff() or {}
    count = int(previous.get('count', 0)) + 1
    minutes = 30 if count == 1 else 120 if count == 2 else 1440
    store.set_setting('gpu_backoff', json.dumps({'name': gpu.get('name'), 'driver': gpu.get('driver'), 'count': count,
                                                 'until': int(time.time() + minutes * 60)}))
    logging.warning('Auto GPU use suspended for %d minutes after a device failure', minutes)


def clear_gpu_backoff():
    store.set_setting('gpu_backoff', '')
    return {'gpuBackoff': None}


def speech_device():
    if store.get_setting('speech_device') == 'cpu' or models.gpu_info() is None or models.gpu_runtime_dir() is None:
        return 'cpu'
    return 'cpu' if gpu_backoff() else 'cuda'


def engine_settings():
    return {'idleSeconds': int(store.get_setting('engine_idle_seconds', 300) or 0),
            'prewarm': bool(store.get_setting('engine_prewarm', 0)), 'gpuBackoff': gpu_backoff()}


def set_engine_settings(idle_seconds=None, prewarm=None, retry_gpu=False):
    if idle_seconds is not None:
        if type(idle_seconds) is not int or idle_seconds not in (0, 300, 900, 3600):
            raise ValueError('Choose how long the speech model stays loaded.')
        store.set_setting('engine_idle_seconds', idle_seconds)
        engine_host.HOST.watch(idle_seconds)
    if prewarm is not None:
        if type(prewarm) is not bool:
            raise ValueError('Choose whether to warm the speech model at launch.')
        store.set_setting('engine_prewarm', int(prewarm))
    if retry_gpu:
        clear_gpu_backoff()
    return engine_settings()


def prewarm():
    """Optional: load and warm while the window opens. Never blocks the interface."""
    settings = engine_settings()
    if not settings['prewarm'] or not incremental_enabled():
        return
    folder = speech_model_path()
    if folder is None:
        return
    threading.Thread(target=lambda: _prewarm(str(folder)), daemon=True, name='relay-prewarm').start()


def _prewarm(folder):
    try:
        engine_host.HOST.watch(engine_settings()['idleSeconds'])
        engine_host.HOST.ensure(folder, speech_device())
        engine_host.HOST.wait_ready(600)
    except Exception:
        logging.info('Speech model prewarm did not finish; the first recording loads it')


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
            'modelOwned': model is not None and models.app_owned(model), 'missingModel': missing_model(),
            'incrementalDictation': incremental_enabled(), 'engine': engine_host.HOST.status(),
            'engineSettings': engine_settings()}


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
        return apply_aliases(transcript_result(text, COHERE_MODEL))
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


def parse_keywords(vocabulary):
    if not isinstance(vocabulary, str) or len(vocabulary) > 3000:
        raise ValueError('Keep the vocabulary hints under 3,000 characters.')
    keywords = [word.strip() for word in vocabulary.replace('\n', ',').split(',') if word.strip()]
    if len(keywords) > 40 or any(len(word) > 80 or any(c in word for c in '<>\r\n') for word in keywords):
        raise ValueError('Use up to 40 short vocabulary hints, separated by commas.')
    # Explicit hints have priority. Keep Whisper's context short to avoid over-biasing it.
    return list(dict.fromkeys(keywords + store.personal_vocabulary()['words']))[:40]


def transcribe(audio, content_type, vocabulary='', provider='whisper', inline_mode=False, retry=False, retry_seconds=30, cancel_event=None):
    if retry and (not inline_mode or not 0 < retry_seconds <= 30):
        raise ValueError('The recognition retry budget has expired. Edit the source manually.')
    if provider not in ('whisper', 'cohere'):
        raise ValueError('Choose Local Whisper or Cohere for microphone transcription.')
    formats = {'audio/webm': ('webm', lambda b: b.startswith(b'\x1aE\xdf\xa3')),
               'audio/wav': ('wav', lambda b: b.startswith(b'RIFF') and b[8:12] == b'WAVE'),
               'audio/mp4': ('mp4', lambda b: b[4:8] == b'ftyp')}
    kind = content_type.split(';', 1)[0].strip().lower()
    if kind not in formats or not 0 < len(audio) <= MAX_AUDIO or not formats[kind][1](audio):
        raise ValueError('Record a valid WebM, WAV, or MP4 clip under 24 MB.')
    keywords = parse_keywords(vocabulary)
    if provider == 'cohere':
        return transcribe_cohere(audio, kind)
    folder = speech_model_path()
    if folder is None:
        raise ValueError('Choose a Whisper model folder in Speech details, then click Refresh.')
    if not TRANSCRIBE_LOCK.acquire(blocking=False):
        raise ValueError('A recording is already being transcribed. Wait for it to finish.')
    started = time.monotonic()
    try:
        if engine_enabled():
            header = {'format': 'encoded', 'keywords': keywords, **({'inline': True, 'retry': retry} if inline_mode else {})}
            result = engine_request((str(folder), speech_device()), header, audio,
                                    timeout=retry_seconds if retry else 300, cancel_event=cancel_event)
        else:
            result = run_worker(folder, audio, keywords, **({'inline_mode': True, 'retry': retry, 'retry_seconds': retry_seconds,
                                                            'cancel_event': cancel_event} if inline_mode else {}))
    finally:
        TRANSCRIBE_LOCK.release()
    response = whisper_response(result, folder, inline_mode)
    response['timings'] = {'transcribeMs': round((time.monotonic() - started) * 1000),
                           'path': 'reusable' if engine_enabled() else 'one-shot'}
    logging.info('Speech timing: full clip %d ms (%s)', response['timings']['transcribeMs'], response['timings']['path'])
    return response


def approved_rules():
    """The enabled approved rules, read once: a recording uses the rules that were in force when it started."""
    return [row for row in aliases.rows() if row['enabled']]


def apply_aliases(response, rules=None):
    """Approved vocabulary rules replace finalized speech text; the recognition output stays in rawText."""
    try:
        applied = aliases.apply(response.get('text') or '', rules)
    except ValueError:
        return response                 # over-long text is validated elsewhere; never fail a transcript here
    response['text'] = applied['text']
    if applied['replacements']:
        response['replacements'] = applied['replacements']
        if response.get('originalText') is not None:
            response['originalText'] = response.get('rawText', '')
    if applied['conflicts']:
        response['aliasConflicts'] = applied['conflicts']
    return response


def whisper_response(result, folder, inline_mode, rules=None):
    response = transcript_result(result.get('text'), models.model_label(folder))
    apply_aliases(response, rules)
    response['device'] = result.get('device', 'GPU')
    if inline_mode:
        response.update(segments=result.get('segments', []), seconds=result.get('seconds'),
                        effectiveAsrConfig=result.get('effectiveAsrConfig'), originalText=result.get('text', ''))
        response['issues'] = [{'code': 'asr_uncertain', 'message': 'A phrase may have been recognized incorrectly. Replay or correct the source.'}
                              for row in response['segments'] if (row.get('avg_logprob') is not None and row['avg_logprob'] < -1)
                              or (row.get('compression_ratio') is not None and row['compression_ratio'] > 2.4)]
    if result.get('fallback'):
        response['notice'] = 'The GPU was unavailable, so this recording ran on the CPU.'
    return response


WORKER_MESSAGES = {'AUDIO_TOO_LONG': 'Use a recording under five minutes.',
                   'MULTILINGUAL_REQUIRED': 'Inline dictation needs a multilingual Whisper model. Choose large-v3 or large-v3-turbo.',
                   'RETRY_TOO_LONG': 'Retry a recording under 15 seconds, or edit the source manually.',
                   'INVALID_AUDIO': 'This recording could not be decoded. Record again or type your transcript.'}
WORKER_FAILED = 'Local speech failed on this recording. Record again; if it keeps failing, turn on Always use CPU in Settings.'


# --- Reusable speech engine and processing during speech ---------------------------------------------------

def engine_enabled():
    """The reusable worker serves local Whisper when processing during speech is enabled."""
    return incremental_enabled()


def incremental_enabled():
    return bool(store.get_setting('incremental_dictation', 0))


def set_incremental(enabled):
    if type(enabled) is not bool:
        raise ValueError('Choose whether to process speech while you talk.')
    store.set_setting('incremental_dictation', int(enabled))
    if not enabled and not TRANSCRIBE_LOCK.locked():
        engine_host.HOST.retire()
    return {'incrementalDictation': enabled}


def engine_request(key, header, payload, timeout=300, cancel_event=None):
    """One request through the reusable worker. A crash on the GPU is retried once on the CPU, as before."""
    folder, device = key
    for attempt in (1, 2):
        launched = device if attempt == 1 else 'cpu'
        engine_host.HOST.ensure(folder, launched)
        try:
            result = engine_host.HOST.request(header, payload, timeout=timeout, cancel_event=cancel_event)
        except engine_host.EngineError as error:
            if error.code == 'CANCELLED':
                raise ValueError('Transcription cancelled. Your source is preserved.') from None
            if error.code == 'TIMEOUT':
                raise ValueError('Local transcription took too long. Your recording can be saved or retried manually.') from None
            if error.code in ('ENGINE_STOPPED', 'NOT_READY') and launched == 'cuda' and not header.get('retry'):
                logging.error('Speech engine stopped on the GPU; retrying this recording on the CPU')
                continue
            raise ValueError(WORKER_MESSAGES.get(error.code, WORKER_FAILED)) from None
        note_fallback(result)
        if launched != device:
            result['fallback'] = True
            # The GPU worker stopped and the same audio then decoded on the CPU: the GPU, not the recording, failed.
            record_gpu_failure('device')
        return result
    raise ValueError('The speech engine stopped. Record again to restart it.')


LAST_DEVICE_REPORT = None


def note_fallback(result):
    """A worker that moved to the CPU after a device fault reports it with every result; count it once per worker.
    A transient OOM is never recorded."""
    global LAST_DEVICE_REPORT
    if result.get('fallbackReason') != 'device':
        return
    worker = engine_host.HOST.loads
    if worker != LAST_DEVICE_REPORT:
        LAST_DEVICE_REPORT = worker
        record_gpu_failure('device')


def window_transcriber(key):
    def transcribe_window(header, payload, cancel_event):
        if cancel_event.is_set():
            raise engine_host.EngineError('CANCELLED')
        engine_host.HOST.ensure(*key)
        # A window is short and bounded; a cancelled job discards its result instead of killing the model.
        result = engine_host.HOST.request(header, payload, timeout=90)
        note_fallback(result)
        return result
    return transcribe_window


def release_stream(job):
    incremental.REGISTRY.release(job)


def _stream_released(job):
    engine_host.HOST.release_hold()
    TRANSCRIBE_LOCK.release()


def expire_streams():
    incremental.REGISTRY.expire()


incremental.REGISTRY.on_release = _stream_released


def stream_start(job_id, inline_mode=False, vocabulary=''):
    """Begin processing during speech for one recording; {'accepted': False} keeps the full-clip path."""
    if type(inline_mode) is not bool:
        raise ValueError('Invalid recording request.')
    expire_streams()
    if not incremental_enabled():
        return {'accepted': False, 'reason': 'disabled'}
    folder = speech_model_path()
    if folder is None:
        return {'accepted': False, 'reason': 'no_model'}
    keywords = parse_keywords(vocabulary)
    if not TRANSCRIBE_LOCK.acquire(blocking=False):
        return {'accepted': False, 'reason': 'busy'}
    try:
        key = (str(folder), speech_device())
        engine_host.HOST.watch(engine_settings()['idleSeconds'])
        engine_host.HOST.hold()
        engine_host.HOST.ensure(*key)
        rules = approved_rules()     # snapshotted with the model, device and hints for this recording
        job = incremental.REGISTRY.start(job_id, lambda: incremental.Job(job_id, inline_mode, keywords, key, window_transcriber(key),
                                                                          rules=rules))
    except BaseException:
        engine_host.HOST.release_hold()
        TRANSCRIBE_LOCK.release()
        raise
    if job is None:
        engine_host.HOST.release_hold()
        TRANSCRIBE_LOCK.release()
        return {'accepted': False, 'reason': 'busy'}
    return {'accepted': True, 'engine': engine_host.HOST.status()}


def stream_chunk(job_id, seq, payload):
    expire_streams()
    job = incremental.REGISTRY.get(job_id)
    try:
        return job.append(seq, payload)
    except ValueError:
        # A gap, repeat or oversized chunk ends this job; the page falls back to the full recording.
        release_stream(job)
        raise


def stream_finish(job_id, last_seq, total_samples):
    if type(last_seq) is not int or type(total_samples) is not int:
        raise ValueError('Invalid recording request.')
    job = incremental.REGISTRY.get(job_id)
    stopped = time.monotonic()
    try:
        result, reason = job.finish(total_samples, last_seq, timeout=90)
        if result is None:
            if job.cancelled.is_set():
                raise ValueError('This recording was cancelled.')
            pcm = bytes(job.pcm)
            if not pcm:
                raise ValueError('No speech was detected. Record again or type your transcript.')
            logging.info('Incremental dictation: whole-recording fallback (%s)', reason.split(':')[0])
            # The authoritative recording, decoded whole with the baseline settings for this writing mode.
            result = engine_request(job.key, {'format': 'pcm16', 'keywords': job.keywords,
                                              **({'inline': True} if job.inline else {})}, pcm, cancel_event=job.cancelled)
        response = whisper_response(result, Path(job.key[0]), job.inline, job.rules)
        done = time.monotonic()
        times = job.times
        response['incremental'] = {'used': reason is None, 'windows': len(job.windows),
                                   'fallbackReason': reason.split(':')[0] if reason else None}
        response['timings'] = {'path': 'incremental' if reason is None else 'incremental-fallback',
                               'stopToFinalMs': round((done - stopped) * 1000),
                               'firstPartialMs': round((times['firstPartial'] - times['start']) * 1000) if 'firstPartial' in times else None,
                               'windowInferenceMs': job.inference_ms[-20:]}
        logging.info('Speech timing: stop to transcript %d ms (%s, %d windows)', response['timings']['stopToFinalMs'],
                     response['timings']['path'], len(job.windows))
        return response
    finally:
        release_stream(job)


def stream_cancel(job_id):
    try:
        job = incremental.REGISTRY.get(job_id)
    except ValueError:
        return {'cancelled': True}
    release_stream(job)
    return {'cancelled': True}


def shutdown_engine():
    """App quit: discard an unfinished recording job and stop the reusable worker."""
    job = incremental.REGISTRY.job
    if job is not None:
        release_stream(job)
    engine_host.HOST.stop()


def run_worker(folder, audio, keywords, check=False, inline_mode=False, retry=False, retry_seconds=30, cancel_event=None):
    """One worker launch per recording; a crash (no error code) gets one automatic relaunch, on the CPU if CUDA crashed."""
    device = speech_device()
    env = worker_environment()
    header = {'keywords': keywords, **({'check': True} if check else {})}
    if inline_mode:
        header.update(inline=True, retry=retry)
    for attempt in ((1,) if retry else (1, 2)):
        # A CUDA driver fault can kill the worker before Python can fall back; the relaunch avoids the GPU.
        launched = device if attempt == 1 else 'cpu'
        command = paths.self_command('--speech-worker', str(os.getpid()), str(folder), launched)
        process = take_prepared_worker(folder, launched) if attempt == 1 and not check and not retry else None
        if process is None:
            with locate.external_runtime():
                process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           cwd=paths.DATA, env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            pending = json.dumps(header).encode() + b'\n' + audio
            if cancel_event is None:
                stdout, stderr = process.communicate(pending, timeout=retry_seconds if retry else 300)
            else:
                deadline = time.monotonic() + (retry_seconds if retry else 300)
                while True:
                    if cancel_event.is_set():
                        process.kill()
                        process.communicate()
                        raise ValueError('Transcription cancelled. Your source is preserved.')
                    if time.monotonic() >= deadline:
                        raise subprocess.TimeoutExpired(command, retry_seconds if retry else 300)
                    try:
                        stdout, stderr = process.communicate(pending, timeout=.25)
                        break
                    except subprocess.TimeoutExpired:
                        pending = None
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
                        'MULTILINGUAL_REQUIRED': 'Inline dictation needs a multilingual Whisper model. Choose large-v3 or large-v3-turbo.',
                        'RETRY_TOO_LONG': 'Retry a recording under 15 seconds, or edit the source manually.',
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
        if engine_enabled():
            result = engine_request((str(folder), speech_device()), {'format': 'encoded', 'keywords': [], 'check': True}, silent_wav())
        else:
            result = run_worker(folder, silent_wav(), [], check=True)
    finally:
        TRANSCRIBE_LOCK.release()
    return {'device': result.get('device', 'GPU'), 'fallback': bool(result.get('fallback'))}
