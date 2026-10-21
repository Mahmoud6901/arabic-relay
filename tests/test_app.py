"""Checks the approval boundary and duplicate/uncertain-send handling. No T3 writes."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from arabic_relay import paths, store, t3


class ApprovalBoundary(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original = paths.DATA
        paths.DATA = Path(self.temp.name)
        self.body = {'english': 'Review the login page.', 'targets': [{'projectId': 'project-a', 'threadId': 'thread-a'}]}

    def tearDown(self):
        paths.DATA = self.original
        self.temp.cleanup()

    def approve(self):
        def fake(name, args):
            if name == 'orchestrator_capabilities':
                return {'runtimeMode': 'full-access'}
            return {'thread': {'projectId': 'project-a', 'archived': False, 'parentThreadId': None,
                               'runtimeMode': 'full-access', 'title': 'coordinator'}}
        with patch.object(t3, 'tool', fake):
            return t3.prepare_send(self.body)['requestId']

    def test_unapproved_and_revoked_cannot_send(self):
        calls = []
        with self.assertRaises(ValueError):
            t3.send('missing', lambda *x: calls.append(x))
        key = self.approve()
        with store.database() as db:
            db.execute('DELETE FROM approvals WHERE id=?', (key,))
        with self.assertRaises(ValueError):
            t3.send(key, lambda *x: calls.append(x))
        self.assertFalse(calls)

    def test_frozen_prompt_and_exactly_once_per_recipient(self):
        key = self.approve()
        self.body['english'] = 'Changed after approval.'
        calls = []
        def fake(name, args):
            calls.append(args)
            return {'delivery': 'queued', 'messageId': 'receipt-a', 'runId': 'run-a'}
        first = t3.send(key, fake)
        second = t3.send(key, fake)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['message'], 'Review the login page.')
        self.assertEqual(calls[0]['mode'], 'queue')
        self.assertEqual(first, second)
        self.assertEqual(first['deliveries'][0]['state'], 'sent')

    def test_uncertain_transport_is_never_replayed(self):
        key = self.approve()
        calls = []
        def broken(name, args):
            calls.append(args)
            raise TimeoutError('Transport timeout')
        self.assertEqual(t3.send(key, broken)['deliveries'][0]['state'], 'unknown')
        t3.send(key, broken)
        self.assertEqual(len(calls), 1)

    def test_multiple_projects_preserve_per_session_results(self):
        self.body['targets'].append({'projectId': 'project-b', 'threadId': 'thread-b'})
        def validate(name, args):
            if name == 'orchestrator_capabilities':
                return {'runtimeMode': 'full-access'}
            return {'thread': {'projectId': 'project-a' if args['threadId'] == 'thread-a' else 'project-b',
                               'archived': False, 'parentThreadId': None,
                               'runtimeMode': 'full-access', 'title': args['threadId']}}
        with patch.object(t3, 'tool', validate):
            key = t3.prepare_send(self.body)['requestId']
        calls = []
        def deliver(name, args):
            calls.append(args)
            if args['threadId'] == 'thread-a':
                raise TimeoutError('Uncertain first delivery')
            return {'delivery': 'started', 'messageId': 'receipt-b'}
        result = t3.send(key, deliver)
        self.assertEqual([r['state'] for r in result['deliveries']], ['unknown', 'sent'])
        self.assertEqual(t3.send(key, deliver), result)
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0]['clientRequestId'], calls[1]['clientRequestId'])

    def test_wrong_project_and_duplicate_targets_rejected(self):
        self.body['targets'].append(self.body['targets'][0].copy())
        with self.assertRaises(ValueError):
            t3.normalize_payload(self.body)
        self.body['targets'].pop()
        def fake(name, args):
            if name == 'orchestrator_capabilities':
                return {'runtimeMode': 'full-access'}
            return {'thread': {'projectId': 'another-project', 'archived': False, 'parentThreadId': None}}
        with patch.object(t3, 'tool', fake), self.assertRaises(ValueError):
            t3.prepare_send(self.body)

    def test_expired_approval_and_windows_credential_roundtrip(self):
        key = self.approve()
        with store.database() as db:
            db.execute('UPDATE approvals SET created=? WHERE id=?', (time.time() - 3600, key))
        with self.assertRaises(ValueError):
            t3.send(key)
        secret = b'{"test":"not a real token"}'
        self.assertEqual(store.protect(store.protect(secret), True), secret)


if __name__ == '__main__':
    unittest.main()
