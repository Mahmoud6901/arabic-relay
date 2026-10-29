"""Direct connections: real hook encryption, session identity, and at-most-once sends. No AI calls."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import queue
import threading
import uuid
import unittest
from unittest.mock import Mock, patch

from arabic_relay import direct_sessions as direct, paths, store, t3

SID = '11111111-2222-3333-4444-555555555555'
TARGET = {'projectId': direct.PREFIX + 'claude', 'threadId': direct.PREFIX + 'claude:' + SID}


class DirectSessions(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = self.root / 'relay'
        self.data.mkdir()
        for ctx in (patch.object(paths, 'DATA', self.data),
                    patch.object(direct, 'CACHE', None),
                    patch.dict(os.environ, {'APPDATA': str(self.root / 'roaming'), 'CLAUDE_CONFIG_DIR': str(self.root / 'claude')})):
            ctx.start()
            self.addCleanup(ctx.stop)

    def metadata(self, **overrides):
        folder = self.root / 'roaming/Claude/claude-code-sessions/account/org'
        folder.mkdir(parents=True, exist_ok=True)
        row = {'sessionId': 'local_' + SID, 'cliSessionId': SID, 'title': 'Existing desktop task', 'cwd': 'C:/work'}
        row.update(overrides)
        (folder / ('local_' + SID + '.json')).write_text(json.dumps(row), encoding='utf-8')

    def test_codex_discovery_paginates_and_excludes_child_agents(self):
        client = Mock()
        client.call.side_effect = [
            {'data': [{'id': SID, 'name': 'Existing task', 'cwd': 'C:/work'}, {'id': SID, 'parentThreadId': 'parent'}], 'nextCursor': 'next'},
            {'data': [], 'nextCursor': None}]
        with patch.object(direct, 'CodexReader') as reader, patch.object(direct, 'run_codex', return_value=Mock(stdout='{"status":"running"}')):
            reader.return_value.__enter__.return_value = client
            rows, _ = direct.codex_sessions()
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]['disabled'])
        self.assertEqual(client.call.call_args_list[1].args[1]['cursor'], 'next')
        self.assertTrue(client.call.call_args_list[0].args[1]['useStateDbOnly'])

    def test_claude_metadata_and_live_inbox_join_by_cli_id(self):
        self.metadata()
        with patch.object(direct, 'registrations', return_value={}):
            rows, _ = direct.claude_sessions()
            self.assertTrue(rows[0]['disabled'])
        with patch.object(direct, 'registrations', return_value={SID: {'token': 'private', 'cwd': 'C:/work'}}):
            rows, _ = direct.claude_sessions()
            self.assertEqual(rows[0]['sessionId'], SID)
            self.assertEqual(rows[0]['desktopId'], 'local_' + SID)
            self.assertFalse(rows[0]['disabled'])
            self.assertNotIn('private', json.dumps(rows))
        self.metadata(isArchived=True)
        with patch.object(direct, 'registrations', return_value={SID: {'cwd': 'C:/work'}}):
            self.assertEqual(direct.claude_sessions()[0], [])

    def test_settings_merge_is_idempotent_and_preserves_other_hooks(self):
        config = direct.settings_path()
        config.parent.mkdir()
        existing = {'model': 'opus', 'hooks': {'SessionStart': [{'hooks': [{'type': 'command', 'command': 'other-hook'}]}]}}
        config.write_text(json.dumps(existing), encoding='utf-8')
        direct.connect_claude()
        direct.connect_claude()
        changed = json.loads(config.read_text())
        self.assertEqual(changed['model'], 'opus')
        self.assertEqual(changed['hooks']['SessionStart'][0], existing['hooks']['SessionStart'][0])
        self.assertEqual(len(changed['hooks']['SessionStart']), 2)
        self.assertTrue(direct.hook_installed())
        self.assertTrue(list(config.parent.glob('settings.before-arabic-relay-*.json')))

    def test_hook_runs_and_stores_only_encrypted_session_credentials(self):
        direct.connect_claude()
        folder = direct.registration_folder()
        transcript = self.root / 'transcript.jsonl'
        event = {'session_id': SID, 'hook_event_name': 'SessionStart', 'transcript_path': str(transcript), 'cwd': str(self.root)}
        env = {**os.environ, 'CLAUDE_CODE_MESSAGING_SOCKET': r'\\.\pipe\relay-test-only', 'CLAUDE_CODE_MESSAGING_TOKEN': 'synthetic-not-a-real-token'}
        command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-File', str(folder.parent / 'relay-claude-hook.ps1'),
                   '-RegistrationDirectory', str(folder)]
        result = subprocess.run(command, input=json.dumps(event), capture_output=True, text=True, env=env, timeout=12,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.assertEqual(result.stdout, '')
        encrypted = (folder / (SID + '.bin')).read_bytes()
        self.assertNotIn(b'synthetic-not-a-real-token', encrypted)
        with patch.object(direct, 'pipe_available', return_value=True):
            entry = direct.registrations()[SID]
            self.assertEqual(entry['token'], env['CLAUDE_CODE_MESSAGING_TOKEN'])
            self.assertEqual(entry['transcript'], str(transcript))
        event['hook_event_name'] = 'SessionEnd'
        subprocess.run(command, input=json.dumps(event), capture_output=True, text=True, env=env, timeout=12,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.assertFalse((folder / (SID + '.bin')).exists())

    def test_validation_rejects_mismatched_provider_and_unavailable_session(self):
        data = {'projects': [{'id': TARGET['projectId'], 'threads': [{'id': TARGET['threadId'], 'title': 'Task', 'disabled': True}]}]}
        with patch.object(direct, 'catalog', return_value=data):
            with self.assertRaisesRegex(ValueError, 'unavailable'):
                direct.validate(TARGET)
            with self.assertRaisesRegex(ValueError, 'Invalid'):
                direct.validate({**TARGET, 'projectId': direct.PREFIX + 'codex'})
            with self.assertRaisesRegex(ValueError, 'Invalid'):
                direct.validate({**TARGET, 'projectId': 'claude'})

    @unittest.skipUnless(os.name == 'nt', 'Windows named-pipe transport')
    def test_real_local_pipe_preserves_unicode_and_line_framing(self):
        name = 'arabic-relay-test-' + uuid.uuid4().hex
        script = '''$pipe = [IO.Pipes.NamedPipeServerStream]::new($env:RELAY_TEST_PIPE, [IO.Pipes.PipeDirection]::InOut)
[Console]::Out.WriteLine('ready')
$pipe.WaitForConnection()
$reader = [IO.StreamReader]::new($pipe, [Text.Encoding]::UTF8)
$frames = @($reader.ReadLine(), $reader.ReadLine())
[Console]::Out.WriteLine([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($frames -join "`n"))))
$reader.Dispose()
$pipe.Dispose()'''
        process = subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**os.environ, 'RELAY_TEST_PIPE': name}, creationflags=subprocess.CREATE_NO_WINDOW)
        ready = queue.Queue()
        reader = threading.Thread(target=lambda: ready.put(process.stdout.readline()), daemon=True)
        reader.start()
        try:
            self.assertEqual(ready.get(timeout=8).strip(), 'ready')
            frames = [{'type': 'auth', 'token': 'synthetic'}, {'type': 'user', 'message': {'content': 'Arabic العربية + English'}}]
            payload = '\n'.join(json.dumps(frame, ensure_ascii=False) for frame in frames)
            direct.write_pipe('\\\\.\\pipe\\' + name, (payload + '\n').encode('utf-8'))
            stdout, stderr = process.communicate(timeout=8)
            import base64
            self.assertEqual(process.returncode, 0, stderr)
            self.assertEqual(base64.b64decode(stdout.strip()).decode('utf-8'), payload)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
            reader.join(timeout=1)

    def test_claude_send_authenticates_and_verifies_exact_message_id(self):
        transcript = self.root / 'transcript.jsonl'
        entry = {'token': 'synthetic-token', 'socket': r'\\.\pipe\test', 'transcript': str(transcript)}
        def write(address, payload):
            frames = [json.loads(line) for line in payload.splitlines()]
            self.assertEqual(frames[0], {'type': 'auth', 'token': 'synthetic-token'})
            self.assertEqual(frames[1]['message']['content'], 'Exact prompt.')
            transcript.write_text(json.dumps({'type': 'user', 'uuid': frames[1]['uuid']}), encoding='utf-8')
        with patch.object(direct, 'validate', return_value={'provider': 'claude', 'sessionId': SID}), \
                patch.object(direct, 'registrations', return_value={SID: entry}), patch.object(direct, 'write_pipe', side_effect=write) as pipe:
            result = direct.send(TARGET, 'Exact prompt.')
        self.assertEqual(result['delivery'], 'direct_submit')
        pipe.assert_called_once()

    def test_codex_uses_exact_id_queue_not_a_new_or_resumed_agent(self):
        with patch.object(direct, 'validate', return_value={'provider': 'codex', 'sessionId': SID}), \
                patch.object(direct, 'run_codex', return_value=Mock(returncode=0)) as command:
            self.assertEqual(direct.send(TARGET, 'Literal $prompt `text`')['delivery'], 'direct_submit')
        self.assertEqual(command.call_args.args[0], ['queue', '--thread', SID, '--message', 'Literal $prompt `text`'])

    def test_retry_never_replays_an_unknown_direct_send(self):
        body = {'requestId': 'direct-session-at-most-once-001', 'english': 'Do the requested work.', 'targets': [TARGET]}
        with patch.object(direct, 'validate', return_value={'provider': 'claude', 'sessionId': SID, 'title': 'Task'}), \
                patch.object(direct, 'send', side_effect=ValueError('Confirmation missing')) as send, patch.object(t3, 'tool') as t3tool:
            first = t3.send_prompt(body)
            second = t3.send_prompt(body)
            self.assertEqual(first, second)
            self.assertEqual(first['deliveries'][0]['state'], 'unknown')
            send.assert_called_once()
            t3tool.assert_not_called()

    def test_direct_acceptance_needs_no_t3_and_records_one_history_entry(self):
        body = {'requestId': 'direct-session-accepted-001', 'english': 'Do the requested work.', 'targets': [TARGET]}
        with patch.object(direct, 'validate', return_value={'provider': 'claude', 'sessionId': SID, 'title': 'Task'}), \
                patch.object(direct, 'send', return_value={'delivery': 'direct_submit', 'messageId': 'confirmed'}) as send, \
                patch.object(t3, 'tool') as t3tool:
            first = t3.send_prompt(body)
            self.assertEqual(first['deliveries'][0]['state'], 'submitted')
            self.assertEqual(t3.send_prompt(body), first)
            send.assert_called_once()
            t3tool.assert_not_called()
        from arabic_relay import history
        self.assertEqual(len(history.entries()['entries']), 1)

    def test_provider_failure_keeps_other_provider_visible(self):
        with patch.object(direct, 'codex_sessions', side_effect=ValueError('offline')), \
                patch.object(direct, 'claude_sessions', return_value=([{'provider': 'claude', 'sessionId': SID, 'title': 'Live',
                    'cwd': '', 'disabled': False, 'status': 'ready'}], 'Connected')):
            data = direct.catalog(force=True)
        self.assertEqual(len(data['projects'][1]['threads']), 1)
        self.assertTrue(data['apps'][1]['ready'])


if __name__ == '__main__':
    unittest.main()
