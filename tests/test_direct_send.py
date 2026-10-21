"""Direct send keeps recipient validation and deduplicates repeated requests. No live messages."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from arabic_relay import paths, t3


class DirectSend(unittest.TestCase):
    def test_click_sends_current_text_once_without_prior_approval(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(paths, 'DATA', Path(folder)):
            body = {'requestId': 'direct-send-regression-001', 'english': 'Redesign the application.',
                    'targets': [{'projectId': 'p1', 'threadId': 't1'}]}
            def transport(name, args):
                if name == 'orchestrator_capabilities':
                    return {'runtimeMode': 'full-access'}
                if name == 't3_thread_read':
                    return {'thread': {'projectId': 'p1', 'title': 'coordinator', 'archived': False,
                                       'parentThreadId': None, 'runtimeMode': 'full-access'}}
                return {'delivery': 'queued', 'messageId': 'mock-receipt'}
            with patch.object(t3, 'tool', side_effect=transport) as mock:
                first = t3.send_prompt(body)
                self.assertEqual(first, t3.send_prompt(body))
                sends = [call for call in mock.call_args_list if call.args[0] == 't3_thread_send']
                self.assertEqual(len(sends), 1)
                self.assertEqual(sends[0].args[1]['message'], body['english'])
                with self.assertRaisesRegex(ValueError, 'different'):
                    t3.send_prompt({**body, 'english': 'Changed text.'})


if __name__ == '__main__':
    unittest.main()
