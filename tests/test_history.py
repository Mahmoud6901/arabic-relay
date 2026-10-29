"""History retention, legacy import, and real send integration. No external messages."""
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from arabic_relay import history, paths, store, t3


class SentHistory(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        patcher = patch.object(paths, 'DATA', Path(folder.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def snapshot(self, key, state='sent', created=1):
        payload = {'english': f'English prompt {key}', 'targets': [{'threadId': key, 'title': 'Coordinator'}]}
        with store.database() as db:
            db.execute('INSERT INTO approvals VALUES (?,?,?)', (key, json.dumps(payload), created))
            if state:
                db.execute('INSERT INTO deliveries VALUES (?,?,?,?)', (key, key, state, '{}'))

    def test_imports_twenty_latest_accepted_actions_only_once(self):
        for i in range(25):
            self.snapshot(str(i), 'submitted' if i == 24 else 'sent', i)
        self.snapshot('failed', 'unknown', 100)
        self.snapshot('draft', None, 101)
        items = history.entries()['entries']
        self.assertEqual([item['id'] for item in items], [str(i) for i in range(24, 4, -1)])
        self.assertEqual(set(items[0]), {'id', 'english', 'sentAt', 'destinations'})
        self.assertEqual(items[0]['destinations'], ['Coordinator'])
        history.delete('24')
        self.assertEqual(len(history.entries()['entries']), 19)
        self.assertNotIn('24', [item['id'] for item in history.entries()['entries']])
        self.assertEqual(history.clear(), {'entries': []})
        self.assertEqual(history.entries(), {'entries': []})

    def test_reading_unsent_drafts_never_adds_history(self):
        self.snapshot('draft', None)
        self.snapshot('uncertain', 'unknown')
        self.assertEqual(history.entries(), {'entries': []})

    def transport(self, name, args):
        if name == 'orchestrator_capabilities':
            return {'runtimeMode': 'full-access'}
        if name == 't3_thread_read':
            return {'thread': {'projectId': 'project', 'archived': False, 'parentThreadId': None,
                               'runtimeMode': 'full-access', 'title': args['threadId']}}
        if args['threadId'] == 'failed':
            raise TimeoutError('Unknown transport outcome')
        return {'delivery': 'queued', 'messageId': 'mock-only'}

    def body(self, i=0):
        return {'requestId': f'history-request-{i:020d}', 'english': f'Prompt {i}',
                'targets': [{'projectId': 'project', 'threadId': 'accepted'},
                            {'projectId': 'project', 'threadId': 'failed'}]}

    def test_partial_send_is_one_entry_and_retries_cannot_resurrect_deletion(self):
        body = self.body()
        with patch.object(t3, 'tool', side_effect=self.transport) as transport:
            result = t3.send_prompt(body)
            self.assertEqual([d['state'] for d in result['deliveries']], ['sent', 'unknown'])
            self.assertEqual(t3.send_prompt(body), result)
            entries = history.entries()['entries']
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]['english'], body['english'])
            history.delete(entries[0]['id'])
            t3.send_prompt(body)
            self.assertEqual(history.entries(), {'entries': []})
        self.assertEqual(sum(call.args[0] == 't3_thread_send' for call in transport.call_args_list), 2)

    def test_failed_send_is_not_saved_and_new_sends_prune_to_twenty(self):
        body = self.body()
        body['targets'] = [body['targets'][1]]
        with patch.object(t3, 'tool', side_effect=self.transport):
            t3.send_prompt(body)
            self.assertEqual(history.entries(), {'entries': []})
            for i in range(1, 23):
                t3.send_prompt(self.body(i))
        items = history.entries()['entries']
        self.assertEqual(len(items), 20)
        self.assertEqual(items[0]['english'], 'Prompt 22')
        self.assertEqual(items[-1]['english'], 'Prompt 3')

    def test_history_failure_does_not_hide_a_confirmed_send(self):
        with patch.object(t3, 'tool', side_effect=self.transport), \
                patch.object(history, 'record', side_effect=sqlite3.OperationalError('disk full')):
            result = t3.send_prompt(self.body())
        self.assertEqual(result['deliveries'][0]['state'], 'sent')
        self.assertIn('history could not be saved', result['historyError'])

    def test_delete_requires_an_entry(self):
        for value in (None, '', [], 'x' * 129):
            with self.assertRaises(ValueError):
                history.delete(value)


if __name__ == '__main__':
    unittest.main()
