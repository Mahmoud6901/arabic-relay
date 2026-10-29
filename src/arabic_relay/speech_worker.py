"""One recording, local cached Whisper only. Audio stays in memory. Started as `--speech-worker <pid> <folder> <cuda|cpu>`."""
import ctypes
from ctypes import wintypes
import io
import json
import os
import sys
import threading


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


def run_model(samples, folder, device, keywords, check=False):
    """Transcribe on the requested device; if CUDA fails for any reason, retry once on the CPU."""
    from faster_whisper import BatchedInferencePipeline, WhisperModel
    hints = ', '.join(keywords) or None

    def run(device):
        model = WhisperModel(folder, device=device, compute_type='float16' if device == 'cuda' else 'int8',
                             local_files_only=True)
        options = dict(task='transcribe', language=None, multilingual=True, beam_size=5,
                       vad_filter=not check, condition_on_previous_text=False,
                       initial_prompt=hints, hotwords=hints)
        if device == 'cuda' and not check and len(samples) >= 45 * 16000:
            try:
                segments, _ = BatchedInferencePipeline(model=model).transcribe(samples, batch_size=2, **options)
                return ''.join(segment.text for segment in segments).strip()
            except RuntimeError as error:
                if 'out of memory' not in str(error).lower():
                    raise
                # Retry serially on this GPU before considering the much slower CPU fallback.
        segments, _ = model.transcribe(samples, **options)
        return ''.join(segment.text for segment in segments).strip()

    if device == 'cuda':
        try:
            return {'text': run('cuda'), 'device': 'GPU', 'fallback': False}
        except Exception as error:
            print('GPU failed, using the CPU: ' + type(error).__name__ + ': ' + str(error)[:500], file=sys.stderr, flush=True)
    return {'text': run('cpu'), 'device': 'CPU', 'fallback': device == 'cuda'}


def main(args):
    parent, folder, device = int(args[0]), args[1], args[2]
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
        handle = os.add_dll_directory(str(runtime)) if runtime else None
    header = sys.stdin.buffer.readline(32769)
    if len(header) > 32768 or not header.endswith(b'\n'):
        raise ValueError('INVALID_AUDIO')
    request = json.loads(header)
    audio = sys.stdin.buffer.read(24 * 1024 * 1024 + 1)
    if not audio or len(audio) > 24 * 1024 * 1024:
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
    result = run_model(samples, folder, device, request['keywords'], check=request.get('check') is True)
    print(json.dumps({**result, 'seconds': len(samples) / 16000}, ensure_ascii=True), flush=True)


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
        # Never expose raw runtime errors or paths/credentials in the interface.
        code = str(error) if str(error) in ('AUDIO_TOO_LONG', 'INVALID_AUDIO') else 'LOCAL_SPEECH_FAILED'
        print(json.dumps({'error': code}), flush=True)
        sys.exit(1)
