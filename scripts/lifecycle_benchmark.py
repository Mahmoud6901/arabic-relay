"""Reusable-worker lifecycle measurements: cold start, warm reuse, memory over repeated jobs, cancellation latency,
and reload after release. No audio is recorded; it decodes corpus clips only.

    python scripts/lifecycle_benchmark.py --corpus .local/corpus --clip heldout/h01 --long heldout/long-pauses-1 \
        --device cuda --repeat 20 --out .local/eval/lifecycle-gpu.json
"""
import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))
os.environ.setdefault('ARABIC_RELAY_DATA_DIR', tempfile.mkdtemp(prefix='relay-life-'))

from arabic_relay import engine_host, models, speech  # noqa: E402
from speech_benchmark import percentile  # noqa: E402


def gpu_used_mb():
    try:
        out = subprocess.run(['nvidia-smi', '--query-gpu=memory.used', '--format=csv,noheader,nounits'],
                             capture_output=True, text=True, timeout=10, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return int(out.stdout.split()[0])
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None


def cold_start(host, folder, device):
    started = time.monotonic()
    host.ensure(folder, device)
    host.wait_ready(900)
    status = host.status()
    return {'readyMs': round((time.monotonic() - started) * 1000), 'loadMs': status['loadMs'], 'warmMs': status['warmMs'],
            'device': status['device'], 'privateMb': status['privateMb'], 'gpuUsedMb': gpu_used_mb()}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--corpus', default=str(ROOT / '.local' / 'corpus'))
    parser.add_argument('--clip', default='heldout/h01')
    parser.add_argument('--long', default='heldout/long-pauses-1')
    parser.add_argument('--device', choices=('cuda', 'cpu'), default='cuda')
    parser.add_argument('--model', default=str(Path(os.environ['LOCALAPPDATA']) / 'Arabic Relay' / 'models' / 'large-v3'))
    parser.add_argument('--repeat', type=int, default=20)
    parser.add_argument('--cancel-after', type=float, default=1.0)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    corpus, folder = Path(args.corpus), str(Path(args.model))
    short = (corpus / (args.clip + '.wav')).read_bytes()
    long = (corpus / (args.long + '.wav')).read_bytes()
    host = engine_host.HOST
    report = {'meta': {'device': args.device, 'model': models.model_label(Path(folder)), 'gpu': (models.gpu_info() or {}).get('name'),
                       'clip': args.clip, 'long': args.long, 'repeat': args.repeat, 'gpuIdleMb': gpu_used_mb(),
                       'started': time.strftime('%Y-%m-%d %H:%M:%S')}}
    try:
        host.stop()
        report['coldStart'] = cold_start(host, folder, args.device)
        loads = host.loads
        # Warm reuse: the same worker, the same clip, again and again; memory after every request.
        rows = []
        for index in range(args.repeat):
            clip = long if index % 5 == 4 else short          # every fifth job is long, to exercise larger buffers
            started = time.monotonic()
            result = host.request({'format': 'encoded', 'keywords': []}, clip, timeout=300)
            status = host.status()
            rows.append({'job': index + 1, 'long': clip is long, 'ms': round((time.monotonic() - started) * 1000),
                         'inferenceMs': result.get('inferenceMs'), 'privateMb': status['privateMb'], 'gpuUsedMb': gpu_used_mb()})
            print(json.dumps(rows[-1]), flush=True)
        report['reuse'] = {'loadsDuringJobs': host.loads - loads, 'rows': rows}
        shorts = [r['ms'] for r in rows if not r['long']]
        memory = [r['privateMb'] for r in rows if r['privateMb']]
        report['reuse'].update(shortMedianMs=round(statistics.median(shorts)), shortP95Ms=round(percentile(shorts, .95)),
                               privateMbFirst=memory[0] if memory else None, privateMbLast=memory[-1] if memory else None,
                               privateMbMax=max(memory) if memory else None,
                               growthAfterFirst=round(memory[-1] / memory[0] - 1, 3) if len(memory) > 1 else None)
        # Cancellation: stop a long decode part-way; the worker is killed and the next job pays a cold start.
        cancel = threading.Event()
        outcome = {}

        def run():
            try:
                host.request({'format': 'encoded', 'keywords': []}, long, timeout=300, cancel_event=cancel)
                outcome['code'] = 'finished'
            except engine_host.EngineError as error:
                outcome['code'] = error.code
            outcome['at'] = time.monotonic()
        worker = threading.Thread(target=run)
        worker.start()
        time.sleep(args.cancel_after)
        requested = time.monotonic()
        cancel.set()
        worker.join(60)
        report['cancel'] = {'code': outcome.get('code'), 'latencyMs': round((outcome.get('at', requested) - requested) * 1000),
                            'workerAlive': host.alive()}
        report['reloadAfterCancel'] = cold_start(host, folder, args.device)
        # Release (as idle release does) and reload: the latency the next recording pays after an idle period.
        host.retire()
        report['releasedGpuUsedMb'] = gpu_used_mb()
        report['reloadAfterRelease'] = cold_start(host, folder, args.device)
    finally:
        host.stop()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=1), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'reuse'}, indent=1))
    print(json.dumps({k: v for k, v in report['reuse'].items() if k != 'rows'}))


if __name__ == '__main__':
    main()
