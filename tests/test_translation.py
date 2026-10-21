"""Subscription translation: no API key, tools, or T3 writes."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
from arabic_relay import server, locate, paths, store, t3, translate, translators


def DRAFT(text):
    return {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': text}}


def ERROR(message):
    return {'type': 'error', 'message': message}


class TranslationBoundary(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.local = patch.object(paths, 'DATA', Path(temp.name))
        self.local.start()
        self.addCleanup(self.local.stop)
        cache = patch.dict(translators.CACHE, {'codex': {'models': []}, 'claude': {'models': []}})  # never run codex for labels
        cache.start()
        self.addCleanup(cache.stop)

    def test_general_spelling_restore_keeps_arabic_and_existing_english(self):
        source = 'الديدلاين بعد الميتنج وخلي React وsrc/server.ts زي ما هم'
        expected = 'الـ deadline بعد الـ meeting وخلي React وsrc/server.ts زي ما هم'
        with patch.object(translate, 'codex_text', return_value=expected) as codex:
            result = translate.restore_english(source)
        self.assertEqual(result['text'], expected)
        self.assertEqual(result['rawText'], source)
        self.assertIn('ANY English word', codex.call_args.args[1])

    def test_existing_english_changes_and_reordering_are_rejected(self):
        for output in ('خلي react وTypeScript', 'خلي TypeScript وReact', 'خلي React'):
            with patch.object(translate, 'codex_text', return_value=output):
                with self.assertRaises(ValueError):
                    translate.restore_english('خلي React وTypeScript')
        with patch.object(translate, 'codex_text', return_value='React'):
            with self.assertRaises(ValueError):
                translate.restore_english('React وبعدها React')

    def test_chatgpt_login_is_used_and_mixed_text_preserved(self):
        process = Mock(returncode=0)
        process.communicate.return_value = (json.dumps({'type': 'item.completed', 'item': {
            'type': 'agent_message', 'text': 'Add dark mode to React.'}}), '')
        with patch.object(locate, 'translator_path', return_value='codex.exe'), \
                patch.dict(server.os.environ, {'OPENAI_API_KEY': 'fake-secret', 'CODEX_API_KEY': 'fake-secret'}), \
                patch.object(translate.subprocess, 'Popen', return_value=process) as launch, \
                patch.object(t3, 'tool') as t3_tool:
            self.assertEqual(translate.translate('ضيف dark mode في React')['english'], 'Add dark mode to React.')
        command = launch.call_args.args[0]
        self.assertIn('--ignore-user-config', command)
        self.assertIn('--ephemeral', command)
        self.assertIn('read-only', command)
        self.assertIn('features.shell_tool=false', command)
        self.assertNotIn('OPENAI_API_KEY', launch.call_args.kwargs['env'])
        self.assertNotIn('CODEX_API_KEY', launch.call_args.kwargs['env'])
        self.assertIn('dark mode', process.communicate.call_args.args[0])
        t3_tool.assert_not_called()

    def test_selected_model_routes_to_codex_or_claude(self):
        translate.set_prompt_choice('codex', 'gpt-5.6-sol', 'low')
        with patch.object(locate, 'tool_path', return_value=Path('codex.exe')), \
                patch.object(translate.subprocess, 'Popen', return_value=self.codex_run(DRAFT('Hi.'))) as launch:
            translate.codex_text({'dictation': 'x'}, 'translate')
        self.assertIn('gpt-5.6-sol', launch.call_args.args[0])

        translate.set_prompt_choice('claude', 'sonnet', 'low')
        with patch.object(locate, 'tool_path', return_value=Path('claude.exe')), \
                patch.dict(server.os.environ, {'ANTHROPIC_API_KEY': 'fake-secret'}), \
                patch.object(translate.subprocess, 'Popen', return_value=self.claude_run('Add dark mode.')) as launch:
            self.assertEqual(translate.translate('ضيف dark mode')['english'], 'Add dark mode.')
        command = launch.call_args.args[0]
        self.assertEqual(command[command.index('--model') + 1], 'sonnet')
        self.assertEqual(command[command.index('--tools') + 1], '')
        self.assertNotIn('ANTHROPIC_API_KEY', launch.call_args.kwargs['env'])

        with patch.object(locate, 'tool_path', return_value=Path('claude.exe')), \
                patch.object(translate.subprocess, 'Popen', return_value=self.claude_run('fake-secret', error=True)):
            with self.assertRaises(ValueError) as caught:
                translate.translate('نص')
        self.assertNotIn('fake-secret', str(caught.exception))

    def codex_run(self, *events, code=0):
        process = Mock(returncode=code)
        process.communicate.return_value = ('\n'.join(json.dumps(event) for event in events), '')
        return process

    def claude_run(self, result, error=False, code=0):
        process = Mock(returncode=code)
        process.communicate.return_value = (json.dumps({'type': 'result', 'is_error': error, 'result': result}), '')
        return process

    def test_migrates_old_prompt_model(self):
        for old, expected in (('codex:gpt-6.1-sol', {'tool': 'codex', 'model': 'gpt-6.1-sol', 'effort': 'low'}),
                              ('codex', {'tool': 'codex', 'model': '', 'effort': 'low'}),
                              ('claude:opus', {'tool': 'claude', 'model': 'opus', 'effort': 'low'})):
            store.set_setting('prompt_choice', '')
            store.set_setting('prompt_model', old)
            self.assertEqual(translate.prompt_choice(), expected)
            self.assertEqual(json.loads(store.get_setting('prompt_choice')), expected)

    def test_default_choice_follows_installed_tool(self):
        with patch.object(locate, 'tool_path', side_effect=lambda kind: Path('claude.exe') if kind == 'claude' else None):
            self.assertEqual(translate.prompt_choice(), {'tool': 'claude', 'model': '', 'effort': 'low'})

    def test_custom_model_text_is_validated(self):
        for bad in ('--dangerously-bypass-approvals', 'gpt 6', 'gpt"6', 'a' * 81, '-m', None):
            with self.assertRaises(ValueError):
                translate.set_prompt_choice('codex', bad, 'low')
        for bad_effort in ('ultra', 'LOW', ''):
            with self.assertRaises(ValueError):
                translate.set_prompt_choice('codex', '', bad_effort)
        with self.assertRaises(ValueError):
            translate.set_prompt_choice('gemini', '', 'low')
        with patch.object(translators, 'label', return_value='Codex · my-model:v2 · high'):
            self.assertEqual(translate.set_prompt_choice('codex', 'my-model:v2', 'high'),
                             {'promptChoice': {'tool': 'codex', 'model': 'my-model:v2', 'effort': 'high'},
                              'promptLabel': 'Codex · my-model:v2 · high'})

    def test_codex_flags(self):
        command = translate.build_command('codex.exe', {'tool': 'codex', 'model': 'gpt-6.1-sol', 'effort': 'high'}, 'x')
        self.assertIn('model_reasoning_effort="high"', command)
        self.assertEqual(command[command.index('-m') + 1], 'gpt-6.1-sol')
        self.assertEqual(command[-1], '-')
        self.assertIn('--ignore-user-config', command)
        self.assertNotIn('-m', translate.build_command('codex.exe', {'tool': 'codex', 'model': '', 'effort': 'low'}, 'x'))

    def test_claude_flags(self):
        command = translate.build_command('claude.exe', {'tool': 'claude', 'model': 'opus', 'effort': 'max'}, 'sys')
        self.assertEqual(command[command.index('--effort') + 1], 'max')
        self.assertEqual(command[command.index('--model') + 1], 'opus')
        self.assertEqual(command[command.index('--tools') + 1], '')
        self.assertEqual(command[command.index('--setting-sources') + 1], '')
        self.assertEqual(command[command.index('--system-prompt') + 1], 'sys')
        self.assertNotIn('--model', translate.build_command('claude.exe', {'tool': 'claude', 'model': '', 'effort': 'low'}, 'sys'))

    def test_failure_kind(self):
        self.assertEqual(translate.failure_kind("The 'gpt-nonexistent-9' model is not supported when using Codex with a ChatGPT account."), 'model')
        self.assertEqual(translate.failure_kind("There's an issue with the selected model (claude-nonexistent-9). It may not exist or you may not have access to it."), 'model')
        self.assertEqual(translate.failure_kind("You've hit your usage limit. Try again later."), 'usage')
        self.assertEqual(translate.failure_kind('401 Unauthorized: please run codex login'), 'login')
        self.assertEqual(translate.failure_kind('something else'), 'other')

    def test_removed_model_falls_back_once(self):
        gone = ERROR("{\"type\":\"error\",\"status\":400,\"error\":{\"type\":\"invalid_request_error\",\"message\":\"The 'gpt-old' model is not supported when using Codex with a ChatGPT account.\"}}")
        translate.set_prompt_choice('codex', 'gpt-old', 'low')
        with patch.object(locate, 'tool_path', return_value=Path('codex.exe')), \
                patch.object(translate.subprocess, 'Popen', side_effect=[self.codex_run(gone, code=1), self.codex_run(DRAFT('Done.'))]) as launch:
            result = translate.translate('نص')
        self.assertEqual(result['english'], 'Done.')
        self.assertEqual(result['notice'], 'gpt-old is no longer available, so Arabic Relay switched to the default model.')
        self.assertNotIn('-m', launch.call_args.args[0])
        self.assertEqual(translate.prompt_choice()['model'], '')
        translate.set_prompt_choice('codex', 'gpt-old', 'low')
        with patch.object(locate, 'tool_path', return_value=Path('codex.exe')), \
                patch.object(translate.subprocess, 'Popen', side_effect=[self.codex_run(gone, code=1), self.codex_run(gone, code=1)]) as launch:
            with self.assertRaisesRegex(ValueError, r"^Codex could not return a draft\. Check its sign-in and plan limits, or type English yourself\.$"):
                translate.translate('نص')
        self.assertEqual(launch.call_count, 2)
        self.assertFalse(translate.TRANSLATE_LOCK.locked())

    def test_usage_and_login_messages(self):
        cases = (('codex', "You've hit your usage limit.", 'Your Codex usage limit was reached. Switch to Claude Code in Settings or try later.'),
                 ('codex', '401 Unauthorized', "Codex isn't signed in. Open a terminal and run codex login."),
                 ('claude', 'Rate limit reached', 'Your Claude Code usage limit was reached. Switch to Codex in Settings or try later.'),
                 ('claude', 'Not logged in · Please run /login', "Claude Code isn't signed in. Open a terminal and run claude, then type /login."))
        for tool, text, message in cases:
            translate.set_prompt_choice(tool, '', 'low')
            run = self.codex_run(ERROR(text), code=1) if tool == 'codex' else self.claude_run(text, error=True, code=1)
            with patch.object(locate, 'tool_path', return_value=Path(tool + '.exe')), patch.object(translate.subprocess, 'Popen', return_value=run):
                with self.assertRaises(ValueError) as caught:
                    translate.translate('نص')
            self.assertEqual(str(caught.exception), message)
        self.assertEqual(str(store.get_setting('claude_signed_out')), '1')
        with patch.object(locate, 'tool_path', return_value=Path('claude.exe')), \
                patch.object(translate.subprocess, 'Popen', return_value=self.claude_run('Fine.')):
            translate.translate('نص')
        self.assertNotEqual(str(store.get_setting('claude_signed_out')), '1')

    def test_stderr_noise_never_resets_the_saved_model(self):
        # Codex prints this warning for any custom model; only the tool's own error events may say "model removed".
        translate.set_prompt_choice('codex', 'gpt-custom', 'low')
        process = Mock(returncode=1)
        process.communicate.return_value = ('', 'WARN Model metadata for `gpt-custom` not found. Defaulting to fallback metadata.\n401 Unauthorized')
        with patch.object(locate, 'tool_path', return_value=Path('codex.exe')), \
                patch.object(translate.subprocess, 'Popen', return_value=process) as launch:
            with self.assertRaisesRegex(ValueError, r"^Codex isn't signed in\."):
                translate.translate('نص')
        launch.assert_called_once()
        self.assertEqual(translate.prompt_choice()['model'], 'gpt-custom')

    def test_metadata_warning_item_is_not_a_failure(self):
        warning = {'type': 'item.completed', 'item': {'type': 'error', 'message': 'Model metadata for `x` not found.'}}
        with patch.object(locate, 'tool_path', return_value=Path('codex.exe')), \
                patch.object(translate.subprocess, 'Popen', return_value=self.codex_run(warning, DRAFT('Ok.'))):
            self.assertEqual(translate.translate('نص')['english'], 'Ok.')

    def test_neither_tool_installed(self):
        with patch.object(locate, 'tool_path', return_value=None), patch.object(translate.subprocess, 'Popen') as launch:
            with self.assertRaisesRegex(ValueError, r'^Install Codex CLI or Claude Code to prepare English prompts\. You can still type English yourself\.$'):
                translate.translate('نص')
        launch.assert_not_called()

    def test_chosen_tool_missing_other_installed(self):
        translate.set_prompt_choice('claude', '', 'low')
        with patch.object(locate, 'tool_path', side_effect=lambda kind: Path('codex.exe') if kind == 'codex' else None):
            with self.assertRaisesRegex(ValueError, r"^Claude Code isn't installed\. Choose Codex in Settings, or type English yourself\.$"):
                translate.translate('نص')

    def test_timeout_message(self):
        process = Mock()
        process.communicate.side_effect = [subprocess.TimeoutExpired('codex', 90), ('', '')]
        with patch.object(locate, 'tool_path', return_value=Path('codex.exe')), patch.object(translate.subprocess, 'Popen', return_value=process):
            with self.assertRaisesRegex(ValueError, r'^Translation took too long\. Try a lower effort\.$'):
                translate.translate('نص')
        self.assertEqual(process.communicate.call_args_list[0].kwargs['timeout'], 90)

    def test_failed_turn_never_returns_partial_draft_or_raw_errors(self):
        process = Mock(returncode=1)
        process.communicate.return_value = (json.dumps({'type': 'turn.failed', 'error': 'fake-secret'}), '')
        with patch.object(locate, 'translator_path', return_value='codex.exe'), \
                patch.object(translate.subprocess, 'Popen', return_value=process):
            with self.assertRaises(ValueError) as caught:
                translate.translate('نص')
        self.assertNotIn('fake-secret', str(caught.exception))
        self.assertFalse(translate.TRANSLATE_LOCK.locked())

    def test_timeout_terminates_cli_and_releases_lock(self):
        process = Mock()
        process.communicate.side_effect = [subprocess.TimeoutExpired('codex', 90), ('', '')]
        with patch.object(locate, 'translator_path', return_value='codex.exe'), \
                patch.object(translate.subprocess, 'Popen', return_value=process):
            with self.assertRaises(ValueError):
                translate.translate('نص')
        process.kill.assert_called_once()
        self.assertFalse(translate.TRANSLATE_LOCK.locked())


if __name__ == '__main__':
    unittest.main()
