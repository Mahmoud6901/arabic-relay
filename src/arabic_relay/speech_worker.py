"""Local cached Whisper only. Audio stays in memory. Started as `--speech-worker <pid> <folder> <cuda|cpu>`.

Without `--serve` the worker handles one recording and exits (the full-clip baseline). With `--serve` it owns one
loaded model and answers framed requests until stdin closes; see `read_frame` for the protocol.
"""
import ctypes
from ctypes import wintypes
import io
import json
import os
import struct
import sys
import threading
import time

MAGIC = b'RLY1'
MAX_HEADER = 65536
MAX_PAYLOAD = 24 * 1024 * 1024
ERRORS = ('AUDIO_TOO_LONG', 'INVALID_AUDIO', 'MULTILINGUAL_REQUIRED', 'RETRY_TOO_LONG', 'INVALID_REQUEST')


def watch_parent(pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    def wait():
        kernel.WaitForSingleObject(handle, 0xFFFFFFFF)
        os._exit(90)
    threading.Thread(target=wait, daemon=True).start()


def compute_type(device):
    return 'float16' if device == 'cuda' else 'int8'


def load_model(folder, device):
    from faster_whisper import WhisperModel
    return WhisperModel(folder, device=device, compute_type=compute_type(device), local_files_only=True)


def out_of_memory(error):
    return 'out of memory' in str(error).lower()


def segment_rows(segments):
    rows = []
    for segment in segments:
        rows.append({'start_s': segment.start, 'end_s': segment.end, 'text': segment.text,
                     **{key: getattr(segment, key, None) for key in ('avg_logprob', 'no_speech_prob', 'compression_ratio')},
                     'words': [{'start_s': word.start, 'end_s': word.end, 'text': word.word,
                                'probability': word.probability} for word in (segment.words or [])]})
    return rows


OVERRIDES = {'beam_size': (1, 10), 'language': None, 'multilingual': None, 'vad_filter': None,
             'vad_parameters': None, 'condition_on_previous_text': None}


def checked_overrides(overrides):
    """Measurement-only decoding choices, strictly bounded; anything unknown is refused."""
    if overrides in (None, {}):
        return {}
    if not isinstance(overrides, dict) or not set(overrides) <= set(OVERRIDES):
        raise ValueError('INVALID_REQUEST')
    checked = {}
    for key, value in overrides.items():
        if key == 'beam_size':
            if type(value) is not int or not 1 <= value <= 10:
                raise ValueError('INVALID_REQUEST')
        elif key == 'language':
            if value is not None and (not isinstance(value, str) or not 2 <= len(value) <= 3):
                raise ValueError('INVALID_REQUEST')
        elif key in ('multilingual', 'vad_filter', 'condition_on_previous_text'):
            if type(value) is not bool:
                raise ValueError('INVALID_REQUEST')
        elif key == 'vad_parameters':
            if not isinstance(value, dict) or not set(value) <= {'speech_pad_ms', 'min_silence_duration_ms'}                     or any(type(v) is not int or not 0 <= v <= 5000 for v in value.values()):
                raise ValueError('INVALID_REQUEST')
        checked[key] = value
    return checked


def decode(model, samples, device, keywords, check=False, inline_mode=False, retry=False, window=False, overrides=None):
    """One decode with the recognition settings the baseline uses for this mode.

    `window` is an incremental-dictation window: serial decoding with word timestamps (needed to join windows),
    otherwise the same options as a whole recording in the same writing mode.
    """
    from faster_whisper import BatchedInferencePipeline
    hints = ', '.join(keywords) or None
    options = dict(task='transcribe', language=None, multilingual=True, beam_size=5,
                   vad_filter=not check, condition_on_previous_text=False,
                   initial_prompt=hints, hotwords=hints)
    options.update(checked_overrides(overrides))
    if inline_mode or window:
        if inline_mode and not model.model.is_multilingual:
            raise ValueError('MULTILINGUAL_REQUIRED')
        options.update(word_timestamps=True)
        if inline_mode:
            options.update(vad_filter=not retry, condition_on_previous_text=retry, initial_prompt=None)
        segments, info = model.transcribe(samples, **options)
        rows = segment_rows(segments)
        config = {'runtime': 'faster-whisper 1.2.1', 'task': 'transcribe', 'multilingual': True, 'language': None,
                  'detectedLanguage': getattr(info, 'language', None), 'path': 'window' if window else 'serial',
                  'wordTimestamps': True, 'sampleRate': 16000, 'beamSize': 5, 'vad': options['vad_filter'],
                  'conditionOnPreviousText': options['condition_on_previous_text'],
                  'device': device, 'computeType': compute_type(device)}
        return {'text': ''.join(row['text'] for row in rows), 'segments': rows,
                'effectiveAsrConfig': {**config, 'beamSize': options['beam_size'], 'language': options['language'],
                                       'vad': options['vad_filter'], 'hints': hints},
                'effectiveOptions': {key: options[key] for key in ('beam_size', 'language', 'multilingual', 'vad_filter',
                                                                  'condition_on_previous_text', 'hotwords')}}
    # The batched pipeline needs VAD (or explicit clip timestamps); without VAD, decode serially.
    if device == 'cuda' and not check and options['vad_filter'] and len(samples) >= 45 * 16000:
        try:
            segments, _ = BatchedInferencePipeline(model=model).transcribe(samples, batch_size=2, **options)
            return {'text': ''.join(segment.text for segment in segments).strip(),
                    'effectiveOptions': {key: options[key] for key in ('beam_size', 'language', 'vad_filter', 'hotwords')}}
        except RuntimeError as error:
            if not out_of_memory(error):
                raise
            # Retry serially on this GPU before considering the much slower CPU fallback.
    segments, _ = model.transcribe(samples, **options)
    return {'text': ''.join(segment.text for segment in segments).strip(),
            'effectiveOptions': {key: options[key] for key in ('beam_size', 'language', 'vad_filter', 'hotwords')}}


def run_model(samples, folder, device, keywords, check=False, inline_mode=False, retry=False, loaded_model=None):
    """Transcribe on the requested device; if CUDA fails for any reason, retry once on the CPU."""
    def run(device):
        model = loaded_model[1] if loaded_model and loaded_model[0] == device else load_model(folder, device)
        value = decode(model, samples, device, keywords, check, inline_mode, retry)
        return value if inline_mode else value['text']

    if device == 'cuda':
        try:
            value = run('cuda')
            return {**(value if inline_mode else {'text': value}), 'device': 'GPU', 'fallback': False}
        except Exception as error:
            print('GPU failed, using the CPU: ' + type(error).__name__ + ': ' + str(error)[:500], file=sys.stderr, flush=True)
    value = run('cpu')
    return {**(value if inline_mode else {'text': value}), 'device': 'CPU', 'fallback': device == 'cuda'}


def decode_audio_bytes(audio):
    """Encoded WebM/WAV/MP4 to 16 kHz mono float samples, bounded before samples accumulate."""
    if not audio or len(audio) > MAX_PAYLOAD:
        raise ValueError('INVALID_AUDIO')
    import av
    from faster_whisper.audio import decode_audio
    # Bound decoding before accumulating samples, including duration-less WebM.
    with av.open(io.BytesIO(audio)) as container:
        seconds = 0
        for frame in container.decode(audio=0):
            seconds += frame.samples / frame.sample_rate
            if seconds > 301:
                raise ValueError('AUDIO_TOO_LONG')
    samples = decode_audio(io.BytesIO(audio), sampling_rate=16000)
    if not 0 < len(samples) <= 301 * 16000:
        raise ValueError('INVALID_AUDIO')
    return samples


def pcm_samples(payload):
    """16 kHz mono signed 16-bit little-endian PCM to float samples."""
    import numpy as np
    if not payload or len(payload) % 2 or len(payload) > 301 * 16000 * 2:
        raise ValueError('INVALID_AUDIO')
    return np.frombuffer(payload, dtype='<i2').astype(np.float32) / 32768.0


def setup_runtime(parent, device):
    watch_parent(parent)
    if os.getppid() != parent:
        watch_parent(os.getppid())  # Also stop if the Windows venv launcher exits.
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    os.environ['DO_NOT_TRACK'] = '1'
    if not ctypes.windll.kernel32.SetDefaultDllDirectories(0x1000):
        raise ctypes.WinError()
    if device == 'cuda':
        from .models import gpu_runtime_dir
        runtime = gpu_runtime_dir()
        return os.add_dll_directory(str(runtime)) if runtime else None
    return None


def main(args):
    parent, folder, device = int(args[0]), args[1], args[2]
    handle = setup_runtime(parent, device)  # noqa: F841 - the DLL directory stays registered while referenced
    if '--serve' in args[3:]:
        return serve(folder, device)
    loaded_model = None
    if '--preload' in args[3:]:
        try:
            loaded_model = (device, load_model(folder, device))
        except Exception:
            pass  # The normal transcription path retains its GPU/CPU error handling.
    header = sys.stdin.buffer.readline(32769)
    if len(header) > 32768 or not header.endswith(b'\n'):
        raise ValueError('INVALID_AUDIO')
    request = json.loads(header)
    audio = sys.stdin.buffer.read(MAX_PAYLOAD + 1)
    samples = decode_audio_bytes(audio)
    if request.get('retry') and len(samples) > 15 * 16000:
        raise ValueError('RETRY_TOO_LONG')
    result = run_model(samples, folder, device, request['keywords'], check=request.get('check') is True,
                       inline_mode=request.get('inline') is True, retry=request.get('retry') is True,
                       loaded_model=loaded_model)
    print(json.dumps({**result, 'seconds': len(samples) / 16000}, ensure_ascii=True), flush=True)


# --- Reusable worker -----------------------------------------------------------------------------------------
# Frames in both directions: b'RLY1', a 4-byte big-endian header length, a UTF-8 JSON header, then exactly
# header['bytes'] payload bytes (0 when absent). EOF never delimits a request; a closed stdin ends the worker.

def read_exact(stream, size):
    data = bytearray()
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            return None
        data += chunk
    return bytes(data)


def read_frame(stream, limit=MAX_PAYLOAD):
    start = read_exact(stream, 8)
    if start is None:
        return None
    if start[:4] != MAGIC:
        raise ValueError('INVALID_REQUEST')
    length = struct.unpack('>I', start[4:])[0]
    if not 2 <= length <= MAX_HEADER:
        raise ValueError('INVALID_REQUEST')
    raw = read_exact(stream, length)
    if raw is None:
        return None
    header = json.loads(raw)
    size = header.get('bytes', 0) if isinstance(header, dict) else -1
    if type(size) is not int or not 0 <= size <= limit:
        raise ValueError('INVALID_REQUEST')
    payload = read_exact(stream, size) if size else b''
    if payload is None:
        return None
    return header, payload


def frame(header, payload=b''):
    raw = json.dumps({**header, 'bytes': len(payload)}, ensure_ascii=True).encode()
    return MAGIC + struct.pack('>I', len(raw)) + raw + payload


class Engine:
    """The single model owner in a serving worker. A device failure moves it to the CPU for its lifetime."""

    def __init__(self, folder, device, send):
        self.folder, self.requested, self.device, self.model = folder, device, device, None
        self.send = send
        self.fallback_reason = None

    def load(self):
        self.send({'event': 'state', 'state': 'loading', 'device': self.device})
        started = time.monotonic()
        try:
            self.model = load_model(self.folder, self.device)
        except Exception as error:
            if self.device != 'cuda':
                raise
            print('GPU load failed, using the CPU: ' + type(error).__name__ + ': ' + str(error)[:500], file=sys.stderr, flush=True)
            self.device, self.fallback_reason = 'cpu', 'oom' if out_of_memory(error) else 'device'
            self.model = load_model(self.folder, 'cpu')
        load_ms = round((time.monotonic() - started) * 1000)
        self.send({'event': 'state', 'state': 'loaded', 'device': self.device, 'loadMs': load_ms,
                   'fallbackReason': self.fallback_reason})
        return load_ms

    def warm(self):
        """One short synthetic decode so the first real recording does not pay for kernel setup."""
        import numpy as np
        self.send({'event': 'state', 'state': 'warming', 'device': self.device})
        started = time.monotonic()
        noise = (np.random.default_rng(7).standard_normal(16000) * 0.01).astype(np.float32)
        segments, _ = self.model.transcribe(noise, task='transcribe', language=None, multilingual=True, beam_size=5,
                                            vad_filter=False, condition_on_previous_text=False, without_timestamps=True)
        list(segments)
        return round((time.monotonic() - started) * 1000)

    def transcribe(self, samples, keywords, check=False, inline_mode=False, retry=False, window=False, overrides=None):
        device, reason = self.device, None
        try:
            value = decode(self.model, samples, device, keywords, check, inline_mode, retry, window, overrides)
        except ValueError:
            raise
        except Exception as error:
            if device != 'cuda':
                raise
            print('GPU failed: ' + type(error).__name__ + ': ' + str(error)[:500], file=sys.stderr, flush=True)
            if out_of_memory(error):
                # Memory pressure is transient: keep the GPU model, run this request on a temporary CPU copy.
                reason = 'oom'
                value = decode(load_model(self.folder, 'cpu'), samples, 'cpu', keywords, check, inline_mode, retry, window, overrides)
            else:
                # A device or driver fault: release the GPU model and stay on the CPU for this worker's lifetime.
                reason = 'device'
                self.model = None
                self.model = load_model(self.folder, 'cpu')
                self.device, self.fallback_reason = 'cpu', 'device'
                self.send({'event': 'state', 'state': 'ready', 'device': 'cpu', 'fallbackReason': 'device'})
                value = decode(self.model, samples, 'cpu', keywords, check, inline_mode, retry, window, overrides)
            device = 'cpu'
        fallback = self.requested == 'cuda' and device == 'cpu'
        return {**value, 'device': 'GPU' if device == 'cuda' else 'CPU', 'fallback': fallback,
                'fallbackReason': reason or (self.fallback_reason if fallback else None)}


def process_memory():
    """Private bytes and working set of this process, from Windows."""
    class Counters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD), ('PeakWorkingSetSize', ctypes.c_size_t),
                    ('WorkingSetSize', ctypes.c_size_t), ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
                    ('QuotaPagedPoolUsage', ctypes.c_size_t), ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
                    ('QuotaNonPagedPoolUsage', ctypes.c_size_t), ('PagefileUsage', ctypes.c_size_t),
                    ('PeakPagefileUsage', ctypes.c_size_t), ('PrivateUsage', ctypes.c_size_t)]
    try:
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        psapi = ctypes.WinDLL('psapi')
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        kernel = ctypes.WinDLL('kernel32')
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return {}
    except (OSError, AttributeError):
        return {}
    return {'privateBytes': counters.PrivateUsage, 'workingSet': counters.WorkingSetSize,
            'peakWorkingSet': counters.PeakWorkingSetSize}


def clean_overrides(value):
    return checked_overrides(value)


def handle_request(engine, header, payload):
    if header.get('format') == 'pcm16':
        samples = pcm_samples(payload)
    elif header.get('format') == 'encoded':
        samples = decode_audio_bytes(payload)
    else:
        raise ValueError('INVALID_REQUEST')
    keywords = header.get('keywords', [])
    if (not isinstance(keywords, list) or len(keywords) > 40
            or any(not isinstance(word, str) or len(word) > 80 for word in keywords)):
        raise ValueError('INVALID_REQUEST')
    if header.get('retry') is True and len(samples) > 15 * 16000:
        raise ValueError('RETRY_TOO_LONG')
    window = header.get('window') is True
    if window and len(samples) > 16 * 16000:
        raise ValueError('INVALID_REQUEST')
    started = time.monotonic()
    result = engine.transcribe(samples, keywords, check=header.get('check') is True, inline_mode=header.get('inline') is True,
                               retry=header.get('retry') is True, window=window,
                               overrides=clean_overrides(header.get('overrides')))
    return {**result, 'seconds': len(samples) / 16000, 'inferenceMs': round((time.monotonic() - started) * 1000)}


def serve(folder, device, stdin=None, stdout=None, engine=None):
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    sys.stdout = sys.stderr  # Library prints must never corrupt the protocol stream.
    lock = threading.Lock()

    def send(header, payload=b''):
        with lock:
            stdout.write(frame({'v': 1, **header}, payload))
            stdout.flush()

    engine = engine or Engine(folder, device, send)
    engine.send = send
    try:
        load_ms = engine.load()
        warm_ms = engine.warm()
    except Exception as error:
        print('Model preparation failed: ' + type(error).__name__ + ': ' + str(error)[:500], file=sys.stderr, flush=True)
        send({'event': 'state', 'state': 'failed', 'code': 'LOCAL_SPEECH_FAILED'})
        return
    send({'event': 'state', 'state': 'ready', 'device': engine.device, 'loadMs': load_ms, 'warmMs': warm_ms,
          'fallbackReason': engine.fallback_reason, 'memory': process_memory()})
    while True:
        try:
            message = read_frame(stdin)
        except (ValueError, UnicodeDecodeError):
            send({'event': 'error', 'id': None, 'code': 'INVALID_REQUEST'})
            return  # A corrupt stream cannot be resynchronised safely.
        if message is None:
            return
        header, payload = message
        request_id = header.get('id')
        if header.get('v') != 1 or not isinstance(request_id, str) or not 0 < len(request_id) <= 100:
            send({'event': 'error', 'id': request_id if isinstance(request_id, str) else None, 'code': 'INVALID_REQUEST'})
            continue
        op = header.get('op')
        if op == 'shutdown':
            send({'event': 'bye', 'id': request_id})
            return
        if op == 'ping':
            send({'event': 'pong', 'id': request_id, 'device': engine.device, 'memory': process_memory()})
            continue
        if op != 'transcribe':
            send({'event': 'error', 'id': request_id, 'code': 'INVALID_REQUEST'})
            continue
        send({'event': 'state', 'state': 'running', 'id': request_id})
        try:
            result = handle_request(engine, header, payload)
            # The result travels as the payload: word timings for five minutes can exceed the header limit.
            send({'event': 'result', 'id': request_id, 'memory': process_memory()},
                 json.dumps(result, ensure_ascii=True).encode())
        except Exception as error:
            print(type(error).__name__ + ': ' + str(error)[:800], file=sys.stderr, flush=True)
            code = str(error) if str(error) in ERRORS else 'LOCAL_SPEECH_FAILED'
            send({'event': 'error', 'id': request_id, 'code': code})
        finally:
            payload = None


def cli(args):
    if args == ['--probe']:  # --self-test: the engine and its DLLs load; no model, no audio
        import av  # noqa: F401
        import ctranslate2
        import faster_whisper  # noqa: F401
        print(json.dumps({'probe': True, 'ctranslate2': ctranslate2.__version__}), flush=True)
        return
    try:
        main(args)
    except Exception as error:
        print(type(error).__name__ + ': ' + str(error)[:800], file=sys.stderr, flush=True)
        if '--serve' in args:
            sys.exit(1)  # The host reports a stopped engine; the protocol stream must stay framed.
        # Never expose raw runtime errors or paths/credentials in the interface.
        code = str(error) if str(error) in ERRORS else 'LOCAL_SPEECH_FAILED'
        print(json.dumps({'error': code}), flush=True)
        sys.exit(1)
