"""Recognition experiments on identical audio: one loaded model, one candidate decoder configuration at a time.

Raw ASR only: no aliases, no AI spelling correction. Every row records the effective configuration. Candidates
that the installed faster-whisper cannot honour are rejected up front (see CANDIDATES).

    python scripts/decoder_experiments.py --corpus .local/corpus --split dev --device cuda --out .local/eval/c3.json
"""
import argparse
import io
import json
import os
from pathlib import Path
import statistics
import sys
import time
import wave

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))

from arabic_relay import speech_worker  # noqa: E402
from speech_benchmark import percentile, score  # noqa: E402

# faster-whisper 1.2.1: with multilingual=True the language is re-detected for every 30-second segment and any
# `language` argument is overwritten, so an Arabic hint is only meaningful with multilingual=False.
CANDIDATES = {
    'baseline': {},
    'beam3': {'beam_size': 3},
    'beam1': {'beam_size': 1},
    'arabic-hint': {'language': 'ar', 'multilingual': False},
    'no-vad': {'vad_filter': False},
    'vad-padded': {'vad_parameters': {'speech_pad_ms': 600, 'min_silence_duration_ms': 1000}},
    'hints-off': {'hints': []},
    'hints-approved': {'hints': 'approved'},
}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--corpus', default=str(ROOT / '.local' / 'corpus'))
    parser.add_argument('--split', default='dev')
    parser.add_argument('--device', choices=('cuda', 'cpu'), default='cuda')
    parser.add_argument('--model', default=str(Path(os.environ['LOCALAPPDATA']) / 'Arabic Relay' / 'models' / 'large-v3'))
    parser.add_argument('--candidates', default=','.join(CANDIDATES))
    parser.add_argument('--hints', default='', help='comma-separated vocabulary hints for hints-approved')
    parser.add_argument('--modes', default='craft')
    parser.add_argument('--clips', default='', help='comma-separated clip ids (default: the whole split)')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    corpus = Path(args.corpus)
    manifest = json.loads((corpus / 'manifest.json').read_text(encoding='utf-8'))
    wanted = set(args.clips.split(',')) if args.clips else None
    clips = [c for c in manifest['clips'] if c['split'] == args.split and (not wanted or c['id'] in wanted)]
    if args.device == 'cuda':
        # The worker registers the CUDA libraries this way; a plain script must do the same before encoding.
        import ctypes
        from arabic_relay.models import gpu_runtime_dir
        ctypes.windll.kernel32.SetDefaultDllDirectories(0x1000)
        runtime = gpu_runtime_dir()
        handle = os.add_dll_directory(str(runtime)) if runtime else None  # noqa: F841
    model = speech_worker.load_model(args.model, args.device)
    approved = [h.strip() for h in args.hints.split(',') if h.strip()]
    rows, abandoned = [], set()      # a candidate that takes >30 s for a 6 s clip is reported, not retried
    for clip in clips:
        with wave.open(io.BytesIO((corpus / (clip['id'] + '.wav')).read_bytes())) as w:
            import numpy as np
            samples = np.frombuffer(w.readframes(w.getnframes()), dtype='<i2').astype(np.float32) / 32768.0
        for name in args.candidates.split(','):
            if name in abandoned:
                continue
            overrides = dict(CANDIDATES[name])
            hints = overrides.pop('hints', None)
            keywords = approved if hints == 'approved' else []
            for mode in args.modes.split(','):
                started = time.monotonic()
                try:
                    result = speech_worker.decode(model, samples, args.device, keywords, inline_mode=mode == 'inline',
                                                  overrides=overrides)
                except Exception as error:      # an unsupported combination is evidence too, never a silent skip
                    rows.append({'clip': clip['id'], 'kind': clip['kind'], 'candidate': name, 'mode': mode,
                                 'error': f'{type(error).__name__}: {str(error)[:200]}', 'keywords': keywords,
                                 'score': score(clip, '')})
                    print(f"{name:14} {mode:6} {clip['id']:24} FAILED {rows[-1]['error'][:90]}", flush=True)
                    continue
                elapsed = time.monotonic() - started
                if elapsed > 30 and clip['seconds'] < 10:
                    abandoned.add(name)
                    print(f"{name:14} {mode:6} {clip['id']:24} ABANDONED after {elapsed:.1f} s on a short clip", flush=True)
                rows.append({'clip': clip['id'], 'kind': clip['kind'], 'candidate': name, 'mode': mode,
                             'decodeMs': round(elapsed * 1000), 'text': result['text'], 'keywords': keywords,
                             'effective': result.get('effectiveOptions'), 'score': score(clip, result['text'])})
                print(f"{name:14} {mode:6} {clip['id']:24} {rows[-1]['decodeMs']:6} ms WER {rows[-1]['score']['wer']:.2f} "
                      f"terms {rows[-1]['score']['termsKept']}/{rows[-1]['score']['terms']}", flush=True)
    summary = []
    for name in args.candidates.split(','):
        for mode in args.modes.split(','):
            items = [r for r in rows if r['candidate'] == name and r['mode'] == mode and 'error' not in r]
            failures = sum(1 for r in rows if r['candidate'] == name and r['mode'] == mode and 'error' in r)
            total = lambda key: sum(r['score'][key] for r in items)
            summary.append({'candidate': name, 'mode': mode, 'clips': len(items),
                            'wer': round((total('substitutions') + total('deletions') + total('insertions')) / max(1, total('words')), 4),
                            'deletions': total('deletions'), 'insertions': total('insertions'),
                            'termRecall': f"{total('termsKept')}/{total('terms')}", 'numbers': f"{total('numbersKept')}/{total('numbers')}",
                            'negations': f"{total('negationsKept')}/{total('negations')}",
                            'decodeMedianMs': round(statistics.median(r['decodeMs'] for r in items)),
                            'decodeP95Ms': round(percentile([r['decodeMs'] for r in items], .95)),
                            'unsupported': failures})
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({'meta': {'synthetic': manifest.get('synthetic'), 'split': args.split,
                                                   'device': args.device, 'model': args.model, 'candidates': CANDIDATES},
                                          'summary': summary, 'rows': rows}, ensure_ascii=False, indent=1), encoding='utf-8')
    for line in summary:
        print(json.dumps(line, ensure_ascii=False))


if __name__ == '__main__':
    main()
