"""Repeatable speech comparison on a corpus with references: accuracy and post-Stop wait per configuration.

Configurations (identical audio, model, device and vocabulary; attributes gains to one change at a time):
  oneshot      the original path: one worker per recording, preloaded while the recording plays out
  warm         the reusable worker, whole recording decoded after Stop
  incremental  the reusable worker, continuous passes during the recording (fed in real time, a quarter second per
               chunk, as from the page), the confirmed words final at Stop; fallback to the whole recording

    python scripts/speech_benchmark.py --corpus .local/corpus --split dev --configs oneshot,warm,incremental \
        --modes craft,inline --device cuda --out .local/eval/run.json

Results contain transcripts of the corpus only. Keep them out of git.
"""
import argparse
import io
import json
import os
from pathlib import Path
import platform
import re
import statistics
import sys
import tempfile
import threading
import time
import wave

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))
os.environ.setdefault('ARABIC_RELAY_DATA_DIR', tempfile.mkdtemp(prefix='relay-bench-'))

from arabic_relay import engine_host, incremental, models, speech  # noqa: E402


# --- Scoring ---------------------------------------------------------------------------------------------------

def tokens(text):
    return [t for t in (incremental.normalize(word) for word in re.split(r'\s+', text or '')) if t]


def edit_counts(reference, hypothesis):
    """(substitutions, deletions, insertions) by Levenshtein alignment of tokens."""
    n, m = len(reference), len(hypothesis)
    table = [[(0, 0, 0, 0)] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        table[i][0] = (i, 0, i, 0)
    for j in range(1, m + 1):
        table[0][j] = (j, 0, 0, j)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if reference[i - 1] == hypothesis[j - 1]:
                table[i][j] = table[i - 1][j - 1]
                continue
            sub, dele, ins = table[i - 1][j - 1], table[i - 1][j], table[i][j - 1]
            table[i][j] = min((sub[0] + 1, sub[1] + 1, sub[2], sub[3]), (dele[0] + 1, dele[1], dele[2] + 1, dele[3]),
                              (ins[0] + 1, ins[1], ins[2], ins[3] + 1))
    return table[n][m][1:]


def present(alternatives, text):
    folded = ' '.join(tokens(text))
    for option in alternatives:
        if re.search('[A-Za-z0-9]', option):
            if re.search(r'(?<![A-Za-z0-9])' + re.escape(option) + r'(?![A-Za-z0-9])', text or '', re.I):
                return True
        elif ' '.join(tokens(option)) in folded:
            return True
    return False


def repeated_ngrams(text, n=2):
    words = tokens(text)
    grams = [tuple(words[i:i + n]) for i in range(len(words) - n + 1)]
    return sum(1 for a, b in zip(grams, grams[n:]) if a == b)


def score(clip, text):
    reference, hypothesis = tokens(clip['reference']), tokens(text)
    sub, dele, ins = edit_counts(reference, hypothesis)
    return {'words': len(reference), 'substitutions': sub, 'deletions': dele, 'insertions': ins,
            'wer': (sub + dele + ins) / max(1, len(reference)),
            'terms': len(clip['terms']), 'termsKept': sum(present([t], text) for t in clip['terms']),
            'numbers': len(clip['numbers']), 'numbersKept': sum(present(n, text) for n in clip['numbers']),
            'negations': len(clip['negations']), 'negationsKept': sum(present(n, text) for n in clip['negations']),
            'extraRepeats': max(0, repeated_ngrams(text) - repeated_ngrams(clip['reference']))}


# --- Runs ------------------------------------------------------------------------------------------------------

def wav_pcm(data):
    with wave.open(io.BytesIO(data)) as clip:
        return clip.readframes(clip.getnframes())


def released_after(data, seconds):
    """The clip cut `seconds` after its last audible sound: Stop pressed that long after the last word."""
    import numpy as np
    samples = np.frombuffer(wav_pcm(data), dtype='<i2')
    energy = incremental.frame_energy(samples, 400)
    loud = np.nonzero(energy >= max(incremental.PAUSE_RATIO * incremental.speech_level(samples), 30.0))[0]
    end = min(len(samples), ((loud[-1] + 1) * 400 if len(loud) else 0) + int(seconds * incremental.RATE))
    out = io.BytesIO()
    with wave.open(out, 'wb') as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(incremental.RATE)
        clip.writeframes(samples[:end].tobytes())
    return out.getvalue(), end / incremental.RATE


def run_oneshot(folder, device, clip, data, inline_mode):
    rid = 'bench-' + os.urandom(12).hex()
    started = time.monotonic()
    speech.prepare_worker(rid)                       # the current app preloads while the user speaks
    time.sleep(min(clip['seconds'], 20))             # model loading is long finished by 20 s
    stopped = time.monotonic()
    result = speech.run_worker(folder, data, [], **({'inline_mode': True} if inline_mode else {}))
    return result, time.monotonic() - stopped, {'recordingSeconds': stopped - started}


def run_warm(folder, device, clip, data, inline_mode):
    # The user speaks for the length of the clip first. The idle GPU clocks down meanwhile (as in real use), so the
    # decode after Stop must not run straight after the previous configuration's decode on a hot GPU.
    time.sleep(clip['seconds'])
    stopped = time.monotonic()
    result = speech.engine_request((str(folder), device), {'format': 'encoded', 'keywords': [],
                                                          **({'inline': True} if inline_mode else {})}, data)
    return result, time.monotonic() - stopped, {}


def run_incremental(folder, device, clip, data, inline_mode, pace=1.0):
    pcm = wav_pcm(data)
    key = (str(folder), device)
    job = incremental.Job('bench-' + os.urandom(12).hex(), inline_mode, [], key, speech.window_transcriber(key))
    started, seq = time.monotonic(), -1
    try:
        step = incremental.RATE // 4                 # the page uploads a quarter second at a time
        for index, start in enumerate(range(0, len(pcm), 2 * step)):
            # Real time: each chunk is available a quarter second after the previous one, as from a microphone.
            due = started + (index + 1) * step / incremental.RATE / pace
            time.sleep(max(0, due - time.monotonic()))
            seq = index
            job.append(seq, pcm[start:start + 2 * step])
        stopped = time.monotonic()
        result, reason = job.finish(len(pcm) // 2, seq, timeout=90)
        if result is None:
            result = speech.engine_request(key, {'format': 'pcm16', 'keywords': [], **({'inline': True} if inline_mode else {})}, pcm)
        return result, time.monotonic() - stopped, {'passes': len(job.windows), 'fallbackReason': reason,
                                                    'finalDecode': job.final_decode if reason is None else 'whole',
                                                    'tailSeconds': round(job.tail_seconds, 2),
                                                    'passInferenceMs': job.inference_ms}
    finally:
        job.cancel()


RUNNERS = {'oneshot': run_oneshot, 'warm': run_warm, 'incremental': run_incremental}


def percentile(values, q):
    values = sorted(values)
    if not values:
        return None
    index = (len(values) - 1) * q
    low, high = int(index), min(int(index) + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (index - low)


def summarize(rows):
    groups = {}
    for row in rows:
        groups.setdefault((row['config'], row['mode']), []).append(row)
    summary = []
    for (config, mode), items in groups.items():
        waits = [r['postStopMs'] for r in items]
        total = lambda key: sum(r['score'][key] for r in items)
        summary.append({'config': config, 'mode': mode, 'runs': len(items),
                        'postStopMedianMs': round(statistics.median(waits)), 'postStopP95Ms': round(percentile(waits, .95)),
                        'wer': round((total('substitutions') + total('deletions') + total('insertions')) / max(1, total('words')), 4),
                        'deletions': total('deletions'), 'insertions': total('insertions'),
                        'termRecall': f"{total('termsKept')}/{total('terms')}", 'numbers': f"{total('numbersKept')}/{total('numbers')}",
                        'negations': f"{total('negationsKept')}/{total('negations')}", 'extraRepeats': total('extraRepeats'),
                        'fallbacks': sum(1 for r in items if r.get('fallbackReason')),
                        'deviceFallbacks': sum(1 for r in items if r.get('deviceFallback'))})
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--corpus', default=str(ROOT / '.local' / 'corpus'))
    parser.add_argument('--split', default='dev')
    parser.add_argument('--configs', default='oneshot,warm,incremental')
    parser.add_argument('--modes', default='craft,inline')
    parser.add_argument('--device', choices=('cuda', 'cpu'), default='cuda')
    parser.add_argument('--model', default=str(Path(os.environ['LOCALAPPDATA']) / 'Arabic Relay' / 'models' / 'large-v3'))
    parser.add_argument('--clips', default='', help='comma-separated clip ids (default: the whole split)')
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--release-after', type=float, default=None,
                        help='cut each clip this many seconds after its last audible sound (when Stop is pressed)')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    corpus = Path(args.corpus)
    manifest = json.loads((corpus / 'manifest.json').read_text(encoding='utf-8'))
    clips = [c for c in manifest['clips'] if c['split'] == args.split and (not args.clips or c['id'] in args.clips.split(','))]
    folder = Path(args.model)
    speech.speech_model_path = lambda: folder
    speech.speech_device = lambda: args.device
    rows, out = [], Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    meta = {'corpus': str(corpus), 'synthetic': manifest.get('synthetic'), 'split': args.split, 'device': args.device,
            'model': models.model_label(folder), 'releaseAfter': args.release_after, 'gpu': (models.gpu_info() or {}).get('name'), 'python': platform.python_version(),
            'streaming': {'firstPass': incremental.FIRST_PASS, 'step': incremental.STEP, 'trimAt': incremental.TRIM_AT,
                          'keep': incremental.KEEP, 'tailMargin': incremental.TAIL_MARGIN}, 'started': time.strftime('%Y-%m-%d %H:%M:%S')}
    configs = args.configs.split(',')
    # The original path needs its own model process: run it as a block with the reusable worker stopped, and
    # interleave the reusable-worker configurations per clip so drift affects them alike.
    blocks = [block for block in (['oneshot'], [c for c in configs if c != 'oneshot']) if set(block) & set(configs)]
    try:
        for block in blocks:
            if block == ['oneshot']:
                engine_host.HOST.stop()                  # never two large models at once
            else:
                started = time.monotonic()
                engine_host.HOST.ensure(folder, args.device)
                engine_host.HOST.wait_ready(600)
                meta['coldStartMs'] = round((time.monotonic() - started) * 1000)
                meta['engine'] = engine_host.HOST.status()
            for repeat in range(args.repeat):
                for clip in clips:
                    data = (corpus / (clip['id'] + '.wav')).read_bytes()
                    if args.release_after is not None:
                        data, seconds = released_after(data, args.release_after)
                        clip = {**clip, 'seconds': seconds}
                    for config in block:
                        for mode in args.modes.split(','):
                            result, wait, extra = RUNNERS[config](folder, args.device, clip, data, mode == 'inline')
                            row = {'clip': clip['id'], 'kind': clip['kind'], 'seconds': round(clip['seconds'], 2), 'config': config,
                                   'mode': mode, 'repeat': repeat, 'postStopMs': round(wait * 1000), 'text': result.get('text', ''),
                                   'device': result.get('device'), 'deviceFallback': bool(result.get('fallback')),
                                   'score': score(clip, result.get('text', '')), **extra}
                            rows.append(row)
                            print(f"{config:11} {mode:6} {clip['id']:24} {row['postStopMs']:6} ms  WER {row['score']['wer']:.2f}"
                                  f"  terms {row['score']['termsKept']}/{row['score']['terms']}"
                                  + (f"  passes {extra['passes']} final={extra['finalDecode']}" if 'passes' in extra else '')
                                  + (f"  fallback={extra.get('fallbackReason')}" if extra.get('fallbackReason') else ''), flush=True)
                            out.write_text(json.dumps({'meta': meta, 'rows': rows}, ensure_ascii=False, indent=1), encoding='utf-8')
    finally:
        engine_host.HOST.stop()
    summary = summarize(rows)
    out.write_text(json.dumps({'meta': meta, 'summary': summary, 'rows': rows}, ensure_ascii=False, indent=1), encoding='utf-8')
    for line in summary:
        print(json.dumps(line, ensure_ascii=False))


if __name__ == '__main__':
    main()
