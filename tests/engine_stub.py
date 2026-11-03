"""A stand-in serving worker for host tests: the real framing and serve loop around a fake model.

Usage: python engine_stub.py <normal|crash|slow|fail-warm|noisy>
"""
import os
import sys
import time

from arabic_relay import speech_worker


class StubEngine:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.device = 'cpu'
        self.fallback_reason = None
        self.send = None

    def load(self):
        self.send({'event': 'state', 'state': 'loading', 'device': 'cpu'})
        self.send({'event': 'state', 'state': 'loaded', 'device': 'cpu', 'loadMs': 5})
        return 5

    def warm(self):
        self.send({'event': 'state', 'state': 'warming', 'device': 'cpu'})
        if self.behaviour == 'fail-warm':
            raise RuntimeError('warm-up failed')
        return 1

    def transcribe(self, samples, keywords, **options):
        if self.behaviour == 'crash':
            os._exit(3)
        if self.behaviour == 'slow':
            time.sleep(30)
        if self.behaviour == 'noisy':
            print('library chatter that must not reach the protocol stream')
        return {'text': f'{len(samples)} samples {",".join(keywords)}', 'device': 'CPU', 'fallback': False,
                'window': options.get('window', False)}


if __name__ == '__main__':
    speech_worker.serve('folder', 'cpu', engine=StubEngine(sys.argv[1]))
