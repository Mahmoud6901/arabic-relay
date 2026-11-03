"""The parent side of the reusable speech worker: one process, one loaded model, one inference at a time.

States: stopped → starting → loading → loaded → warming → ready ⇄ running; failed when preparation fails.
Readiness comes from the worker's own acknowledgement after a real warm-up decode, never from a process launch.
"""
import collections
import itertools
import json
import logging
import os
import subprocess
import threading
import time

from . import locate, paths, speech_worker

READY_STATES = ('ready', 'running')
RESULT_LIMIT = 8 * 1024 * 1024


class EngineError(Exception):
    """`code` is a worker error code or one of ENGINE_STOPPED, TIMEOUT, CANCELLED, BUSY, NOT_READY."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


class EngineHost:
    def __init__(self, environment=None):
        self.environment = environment or (lambda: os.environ.copy())
        self.lock = threading.RLock()          # fields below
        self.request_lock = threading.Lock()   # one inference request at a time
        self.process = None
        self.key = None                        # (folder, requested device)
        self.state = 'stopped'
        self.info = {}
        self.pending = {}
        self.ready = threading.Event()
        self.generation = 0
        self.loads = 0
        self.requests = 0
        self.last_used = time.monotonic()
        self.started_at = None
        self.ready_at = None
        self.stderr_tail = collections.deque(maxlen=40)
        self.ids = itertools.count(1)
        self.memory = []                       # (requests, private bytes) after each request, newest last
        self.baseline_memory = None            # private bytes once the model is ready
        self.idle_seconds = 0                  # 0 keeps the model loaded until the app quits
        self.holds = 0                         # recordings that keep the model loaded (idle release waits)
        self.watchdog = None

    # --- lifecycle -------------------------------------------------------------------------------------------

    def alive(self):
        return self.process is not None and self.process.poll() is None

    def ensure(self, folder, device):
        """Start the worker for this model/device unless it already runs. A different key retires the old one
        first, waiting for its in-flight request, so two large models are never loaded at once."""
        key = (str(folder), device)
        with self.lock:
            if self.alive() and self.key == key and self.state != 'failed':
                return self.status()
        if self.alive():
            self.retire()
        with self.lock:
            if self.alive() and self.key == key:
                return self.status()
            command = paths.self_command('--speech-worker', str(os.getpid()), key[0], device, '--serve')
            with locate.external_runtime():
                process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           cwd=paths.DATA, env=self.environment(),
                                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.generation += 1
            self.process, self.key, self.state, self.info = process, key, 'starting', {}
            self.ready.clear()
            self.loads += 1
            self.started_at, self.ready_at = time.monotonic(), None
            self.last_used = time.monotonic()
            generation = self.generation
            threading.Thread(target=self.read_events, args=(process, generation), daemon=True).start()
            threading.Thread(target=self.drain_stderr, args=(process,), daemon=True).start()
            logging.info('Speech engine starting (load %d, %s)', self.loads, 'GPU' if device == 'cuda' else 'CPU')
            return self.status()

    def retire(self, timeout=30):
        """Wait (bounded) for the in-flight request, then stop the worker."""
        acquired = self.request_lock.acquire(timeout=timeout)
        try:
            self.stop()
        finally:
            if acquired:
                self.request_lock.release()

    def stop(self, kill=False):
        with self.lock:
            process = self.process
            self.process = None
            self.state = 'stopped'
            self.ready.clear()
            self.generation += 1
            self.fail_pending('ENGINE_STOPPED')
        if process is None:
            return
        if not kill and process.poll() is None:
            try:
                process.stdin.write(speech_worker.frame({'v': 1, 'op': 'shutdown', 'id': 'shutdown'}))
                process.stdin.flush()
                process.stdin.close()
                process.wait(timeout=5)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                stream.close()
            except (OSError, ValueError, AttributeError):
                pass

    def fail_pending(self, code):
        for slot in self.pending.values():
            slot['error'] = code
            slot['done'].set()
        self.pending.clear()

    # --- worker output ---------------------------------------------------------------------------------------

    def read_events(self, process, generation):
        try:
            while True:
                message = speech_worker.read_frame(process.stdout, limit=RESULT_LIMIT)
                if message is None:
                    break
                event, payload = message
                if event.get('event') == 'result':
                    event = {**event, 'result': json.loads(payload)}
                self.handle_event(event, generation)
        except (ValueError, OSError, UnicodeDecodeError) as error:
            logging.error('Speech engine protocol error: %s', type(error).__name__)
            if process.poll() is None:
                process.kill()
        with self.lock:
            if generation == self.generation:
                if self.state != 'failed':
                    self.state = 'stopped'
                self.ready.set()  # wake waiters; they check the state
                self.fail_pending('ENGINE_STOPPED')
                code = process.poll()
                logging.error('Speech engine stopped (exit %s): %s', code, ' | '.join(self.stderr_tail)[-1500:])

    def handle_event(self, event, generation):
        with self.lock:
            if generation != self.generation:
                return
            kind = event.get('event')
            if kind == 'state':
                state = event.get('state')
                if state in ('loading', 'loaded', 'warming', 'ready', 'running', 'failed'):
                    if state != 'running':
                        self.state = state
                    for key in ('device', 'loadMs', 'warmMs', 'fallbackReason', 'memory', 'code'):
                        if key in event:
                            self.info[key] = event[key]
                    if state == 'ready' and self.ready_at is None:
                        self.ready_at = time.monotonic()
                        self.baseline_memory = (self.info.get('memory') or {}).get('privateBytes')
                        logging.info('Speech engine ready: load %s ms, warm-up %s ms, %s', self.info.get('loadMs'),
                                     self.info.get('warmMs'), self.info.get('device'))
                    if state in ('ready', 'failed'):
                        self.ready.set()
            elif kind in ('result', 'error', 'pong', 'bye'):
                slot = self.pending.pop(event.get('id'), None)
                if slot is not None:
                    slot['event'] = event
                    slot['done'].set()
                if 'memory' in event and isinstance(event['memory'], dict):
                    self.info['memory'] = event['memory']

    def drain_stderr(self, process):
        try:
            for line in iter(process.stderr.readline, b''):
                self.stderr_tail.append(line.decode(errors='replace').strip()[:300])
        except (OSError, ValueError):
            pass

    # --- requests --------------------------------------------------------------------------------------------

    def wait_ready(self, timeout=180, cancel_event=None):
        deadline = time.monotonic() + timeout
        while True:
            with self.lock:
                if self.state in READY_STATES:
                    return True
                if self.state in ('failed', 'stopped'):
                    raise EngineError('NOT_READY')
            if cancel_event is not None and cancel_event.is_set():
                raise EngineError('CANCELLED')
            if time.monotonic() >= deadline:
                raise EngineError('TIMEOUT')
            self.ready.wait(.1)

    def request(self, header, payload=b'', timeout=300, cancel_event=None, wait=True):
        """Send one request and wait for its result. A timeout or cancellation kills the worker: a decode
        cannot be interrupted safely in-process, and the next recording restarts it."""
        if not self.request_lock.acquire(blocking=wait, timeout=timeout if wait else -1):
            raise EngineError('BUSY')
        try:
            self.wait_ready(timeout, cancel_event)
            request_id = f'r{next(self.ids)}'
            slot = {'done': threading.Event()}
            with self.lock:
                process = self.process
                if process is None or process.poll() is not None:
                    raise EngineError('ENGINE_STOPPED')
                self.pending[request_id] = slot
                self.last_used = time.monotonic()
            try:
                process.stdin.write(speech_worker.frame({'v': 1, 'op': 'transcribe', 'id': request_id, **header}, payload))
                process.stdin.flush()
            except (OSError, ValueError):
                with self.lock:
                    self.pending.pop(request_id, None)
                raise EngineError('ENGINE_STOPPED') from None
            deadline = time.monotonic() + timeout
            while not slot['done'].wait(.05):
                if (cancel_event is not None and cancel_event.is_set()) or time.monotonic() >= deadline:
                    code = 'CANCELLED' if cancel_event is not None and cancel_event.is_set() else 'TIMEOUT'
                    logging.info('Speech engine request %s; stopping the worker', code.lower())
                    self.stop(kill=True)
                    raise EngineError(code)
            with self.lock:
                self.last_used = time.monotonic()
                self.requests += 1
            if 'error' in slot:
                raise EngineError(slot['error'])
            event = slot['event']
            if event.get('event') == 'error':
                raise EngineError(event.get('code') or 'LOCAL_SPEECH_FAILED')
            result = event.get('result')
            if not isinstance(result, dict):
                raise EngineError('LOCAL_SPEECH_FAILED')
            memory = event.get('memory') or {}
            if memory.get('privateBytes'):
                with self.lock:
                    self.memory = (self.memory + [(self.requests, memory['privateBytes'])])[-50:]
            return result
        finally:
            self.request_lock.release()

    def busy(self):
        return self.request_lock.locked()

    # --- idle release and memory -----------------------------------------------------------------------------

    def hold(self):
        with self.lock:
            self.holds += 1

    def release_hold(self):
        with self.lock:
            self.holds = max(0, self.holds - 1)
            self.last_used = time.monotonic()

    def growth(self):
        """Private bytes above the memory measured once the model was ready, or None."""
        with self.lock:
            memory = (self.info.get('memory') or {}).get('privateBytes')
            if not memory or not self.baseline_memory:
                return None
            return memory / self.baseline_memory - 1

    def watch(self, idle_seconds=None):
        """Release the model after `idle_seconds` without work; never while a recording or request is active."""
        with self.lock:
            if idle_seconds is not None:
                self.idle_seconds = max(0, int(idle_seconds))
            if self.watchdog is None or not self.watchdog.is_alive():
                self.watchdog = threading.Thread(target=self._watch, daemon=True, name='relay-engine-idle')
                self.watchdog.start()
        return self.idle_seconds

    def _watch(self):
        while True:
            time.sleep(5)
            with self.lock:
                idle = time.monotonic() - self.last_used
                ready = self.state in READY_STATES
                limit = self.idle_seconds
                holds = self.holds
            if not ready or limit <= 0 or holds or self.request_lock.locked():
                continue
            grown = self.growth()
            if grown is not None and grown > .25 and self.requests >= 4:
                logging.info('Speech engine recycled after memory growth of %d%%', round(grown * 100))
                self.retire()
                continue
            if idle >= limit:
                logging.info('Speech engine released after %d s idle', round(idle))
                self.retire()

    def status(self):
        with self.lock:
            memory = self.info.get('memory') or {}
            return {'state': self.state if self.process is not None or self.state == 'failed' else 'stopped',
                    'device': ('GPU' if self.info.get('device') == 'cuda' else 'CPU') if self.info.get('device') else None,
                    'loads': self.loads, 'requests': self.requests, 'loadMs': self.info.get('loadMs'),
                    'warmMs': self.info.get('warmMs'), 'fallbackReason': self.info.get('fallbackReason'),
                    'privateMb': round(memory['privateBytes'] / 1e6) if memory.get('privateBytes') else None,
                    'idleSeconds': round(time.monotonic() - self.last_used)}


HOST = EngineHost()
