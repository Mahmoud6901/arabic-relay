"""Real child processes: a completed model turn must not wait for CLI teardown."""
import json
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

from arabic_relay import translate


class PromptLatency(unittest.TestCase):
    def run_child(self, script, **kwargs):
        launch = subprocess.Popen
        def child(*args, **options):
            process = launch(*args, **options)
            self.addCleanup(process.wait, timeout=6)
            return process
        with patch.object(translate, 'build_command', return_value=[sys.executable, '-u', '-c', script]), \
                patch.object(translate.subprocess, 'Popen', side_effect=child):
            return translate.run_tool(sys.executable, {'tool': 'codex', 'model': '', 'effort': 'medium'},
                                      ('rules', 'test source'), **kwargs)

    def test_completed_turn_does_not_wait_for_shutdown(self):
        script = "import sys,time,json; sys.stdin.read(); " + "; ".join(
            'print(' + repr(json.dumps(event)) + ',flush=True)' for event in (
                {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Review the login page.'}},
                {'type': 'turn.completed', 'usage': {}})) + '; time.sleep(2)'
        started = time.monotonic()
        result = self.run_child(script)
        self.assertEqual(result, ('Review the login page.', None))
        self.assertLess(time.monotonic() - started, 1, 'Ready prompt waited for CLI teardown')

    def test_partial_message_waits_for_terminal_outcome(self):
        draft = json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Partial'}})
        failure = json.dumps({'type': 'turn.failed', 'error': {'message': 'test failure'}})
        script = f'import sys,time; sys.stdin.read(); print({draft!r},flush=True); time.sleep(.3); print({failure!r},flush=True); sys.exit(1)'
        started = time.monotonic()
        result = self.run_child(script)
        self.assertIsNone(result[0])
        self.assertIn('test failure', result[1][0])
        self.assertGreater(time.monotonic() - started, .3)

    def test_cancel_and_deadline_still_kill_child(self):
        script = 'import sys,time; sys.stdin.read(); time.sleep(10)'
        with self.assertRaisesRegex(ValueError, 'وقت طويل'):
            self.run_child(script, deadline=time.monotonic() + .3)
        event = threading.Event()
        timer = threading.Timer(.3, event.set)
        timer.start()
        try:
            with self.assertRaisesRegex(ValueError, 'اتلغى التجهيز'):
                self.run_child(script, cancel_event=event)
        finally:
            timer.cancel()

    def test_stderr_is_drained_and_normal_exit_remains_supported(self):
        draft = json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Complete'}})
        script = f'import sys; sys.stdin.read(); sys.stderr.write("x"*200000); print({draft!r},flush=True)'
        self.assertEqual(self.run_child(script), ('Complete', None))


if __name__ == '__main__':
    unittest.main()
