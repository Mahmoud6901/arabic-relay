"""Scores an existing benchmark run again with approved vocabulary rules applied offline.

Keeps three things separate, as the plan requires: raw recognition, deterministic approved replacements, and any
optional AI correction (never part of this script).

    python scripts/eval_report.py --run .local/eval/d-baseline-gpu.json --rules .local/eval/rules.json \
        --out .local/eval/d-report.md
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))

from arabic_relay import aliases  # noqa: E402
from speech_benchmark import percentile, score  # noqa: E402


def summarize(run, rules, clips, by=lambda row, clip: (row['config'], row['mode'])):
    """Aggregate per group (configuration and mode by default), before and after approved replacements."""
    groups = {}
    for row in run['rows']:
        clip = clips.get(row.get('clip')) if 'text' in row else None
        if clip is None:
            continue
        corrected = aliases.apply(row['text'], rules)['text'] if rules else row['text']
        bucket = groups.setdefault(by(row, clip),
                                   {'raw': [], 'approved': [], 'waits': [], 'fallbacks': 0})
        bucket['raw'].append(score(clip, row['text']))
        bucket['approved'].append(score(clip, corrected))
        bucket['waits'].append(row['postStopMs'])
        bucket['fallbacks'] += 1 if row.get('fallbackReason') else 0
    summary = []
    for key, bucket in sorted(groups.items()):
        line = {'key': key, 'config': key[-2], 'mode': key[-1], 'runs': len(bucket['waits']),
                'postStopMedianMs': round(percentile(bucket['waits'], .5)),
                'postStopP95Ms': round(percentile(bucket['waits'], .95)), 'fallbacks': bucket['fallbacks']}
        for label in ('raw', 'approved'):
            scores = bucket[label]
            total = lambda key: sum(item[key] for item in scores)
            line[label] = {'wer': round((total('substitutions') + total('deletions') + total('insertions'))
                                        / max(1, total('words')), 4),
                           'deletions': total('deletions'), 'insertions': total('insertions'),
                           'terms': f"{total('termsKept')}/{total('terms')}",
                           'numbers': f"{total('numbersKept')}/{total('numbers')}",
                           'negations': f"{total('negationsKept')}/{total('negations')}",
                           'extraRepeats': total('extraRepeats')}
        summary.append(line)
    return summary


def render_kinds(summary):
    lines = ['', '## Per category (raw recognition, then with approved rules)', '',
             '| category | config | mode | runs | WER raw | WER +rules | deletions | insertions | terms raw | terms +rules | numbers | negations | extra repeats |',
             '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for line in summary:
        raw, approved = line['raw'], line['approved']
        lines.append(f"| {line['key'][0]} | {line['config']} | {line['mode']} | {line['runs']} | {raw['wer']:.3f} | "
                     f"{approved['wer']:.3f} | {raw['deletions']} | {raw['insertions']} | {raw['terms']} | {approved['terms']} | "
                     f"{raw['numbers']} | {raw['negations']} | {raw['extraRepeats']} |")
    return '\n'.join(lines) + '\n'


def render(summary, meta, rule_count):
    lines = ['# Speech comparison', '',
             f"- corpus: {'synthetic Windows TTS' if meta.get('synthetic') else meta.get('corpus')} ({meta.get('split', '')})",
             f"- device: {meta.get('device')}, model: {meta.get('model')}, gpu: {meta.get('gpu')}",
             f'- approved rules applied offline: {rule_count}',
             '',
             '| config | mode | runs | post-Stop median | post-Stop p95 | fallbacks | WER raw | WER +rules | terms raw | terms +rules | numbers | negations |',
             '| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |']
    for line in summary:
        lines.append(f"| {line['config']} | {line['mode']} | {line['runs']} | {line['postStopMedianMs']} ms | "
                     f"{line['postStopP95Ms']} ms | {line['fallbacks']} | {line['raw']['wer']:.3f} | "
                     f"{line['approved']['wer']:.3f} | {line['raw']['terms']} | {line['approved']['terms']} | "
                     f"{line['raw']['numbers']} | {line['raw']['negations']} |")
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--run', required=True, help='one run file, or several separated by commas')
    parser.add_argument('--rules', default='')
    parser.add_argument('--manifest', default=str(ROOT / '.local' / 'corpus' / 'manifest.json'))
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    runs = [json.loads(Path(path).read_text(encoding='utf-8')) for path in args.run.split(',')]
    run = {'meta': runs[0].get('meta', {}), 'rows': [row for item in runs for row in item['rows']]}
    manifest = json.loads(Path(args.manifest).read_text(encoding='utf-8'))
    clips = {clip['id']: clip for clip in manifest['clips']}
    rules = [{'id': index + 1, 'heard': rule['heard'], 'canonical': rule['canonical'], 'enabled': 1}
             for index, rule in enumerate(json.loads(Path(args.rules).read_text(encoding='utf-8')))] if args.rules else []
    report = render(summarize(run, rules, clips), run.get('meta', {}), len(rules))
    report += render_kinds(summarize(run, rules, clips, by=lambda row, clip: (clip['kind'], row['config'], row['mode'])))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(report, encoding='utf-8')
    print(report)


if __name__ == '__main__':
    main()
