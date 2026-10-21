"""What the user can choose for English prompts. Never starts the real codex or claude."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
from arabic_relay import locate, paths, store, translators

FIXTURE = Path(__file__).parent / 'fixtures' / 'codex_models.json'  # trimmed real `codex debug models` output, 2026-10-20
SOL = {'id': 'gpt-6.1-sol', 'name': 'GPT-6.1-Sol', 'efforts': ['low', 'medium', 'high', 'xhigh', 'max']}


class Translators(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        for patcher in (patch.object(paths, 'DATA', Path(temp.name)),
                        patch.object(translators.subprocess, 'run', side_effect=AssertionError('real tool started'))):
            patcher.start()
            self.addCleanup(patcher.stop)
        translators.CACHE.clear()
        self.addCleanup(translators.CACHE.clear)

    def test_parses_real_model_list(self):
        models = translators.parse_codex_models(FIXTURE.read_text(encoding='utf-8'))
        self.assertEqual(next(m for m in models if m['id'] == 'gpt-6.1-sol'), SOL)
        self.assertNotIn('gpt-reserve', [m['id'] for m in models])
        self.assertTrue(all('ultra' not in m['efforts'] for m in models))

    def test_model_list_failure_falls_back(self):
        for text in ('', 'not json', '{"models": 5}', '{"models": [{"slug": 3}]}', '[]'):
            self.assertEqual(translators.parse_codex_models(text), [], text)
        for outcome in (Mock(returncode=1, stdout=FIXTURE.read_text(encoding='utf-8')),
                        subprocess.TimeoutExpired('codex', 10), FileNotFoundError()):
            kwargs = {'side_effect': outcome} if isinstance(outcome, BaseException) else {'return_value': outcome}
            with patch.object(translators.subprocess, 'run', **kwargs):
                self.assertEqual(translators.codex_models(Path('codex.exe')), [])

    def test_model_list_command(self):
        done = Mock(returncode=0, stdout=FIXTURE.read_text(encoding='utf-8'))
        with patch.object(translators.subprocess, 'run', return_value=done) as run:
            self.assertIn(SOL, translators.codex_models(Path('codex.exe')))
        self.assertEqual(run.call_args.args[0], ['codex.exe', 'debug', 'models'])
        self.assertEqual(run.call_args.kwargs['timeout'], 10)
        self.assertNotIn('OPENAI_API_KEY', run.call_args.kwargs['env'])

    def test_login_status(self):
        for outcome, expected in ((Mock(returncode=0, stdout='Logged in using ChatGPT\n', stderr=''), True),
                                  (Mock(returncode=1, stdout='', stderr='Not logged in\n'), False),
                                  (OSError('gone'), None)):
            kwargs = {'side_effect': outcome} if isinstance(outcome, BaseException) else {'return_value': outcome}
            with patch.object(translators.subprocess, 'run', **kwargs) as run:
                self.assertEqual(translators.codex_signed_in(Path('codex.exe')), expected)
            self.assertEqual(run.call_args.args[0], ['codex.exe', 'login', 'status'])

    def test_catalog_marks_missing_tools(self):
        with patch.object(locate, 'tool_path', side_effect=lambda kind: Path('codex.exe') if kind == 'codex' else None), \
                patch.object(translators, 'codex_models', return_value=[SOL]), patch.object(translators, 'codex_signed_in', return_value=True):
            catalog = translators.catalog()
        self.assertEqual(catalog['codex'], {'installed': True, 'path': 'codex.exe', 'signedIn': True, 'models': [SOL]})
        self.assertEqual(catalog['claude']['installed'], False)
        self.assertEqual([m['id'] for m in catalog['claude']['models']], ['fable', 'opus', 'sonnet', 'haiku'])
        self.assertEqual(catalog['efforts'], ['low', 'medium', 'high', 'xhigh', 'max'])
        self.assertIsNone(catalog['claude']['signedIn'])
        store.set_setting('claude_signed_out', '1')
        translators.CACHE.clear()
        with patch.object(locate, 'tool_path', return_value=Path('claude.exe')), \
                patch.object(translators, 'codex_models', return_value=[]), patch.object(translators, 'codex_signed_in', return_value=None):
            self.assertIs(translators.catalog()['claude']['signedIn'], False)

    def test_catalog_is_cached_until_refresh(self):
        with patch.object(locate, 'tool_path', return_value=Path('codex.exe')), \
                patch.object(translators, 'codex_models', return_value=[SOL]) as models, \
                patch.object(translators, 'codex_signed_in', return_value=True):
            translators.catalog()
            translators.catalog()
            self.assertEqual(models.call_count, 1)
            translators.catalog(refresh=True)
            self.assertEqual(models.call_count, 2)

    def test_missing_codex_never_runs(self):
        with patch.object(locate, 'tool_path', return_value=None):
            catalog = translators.catalog()
        self.assertEqual((catalog['codex']['models'], catalog['codex']['signedIn']), ([], None))

    def test_label_never_runs_codex(self):
        # /api/status shows the label on every launch; it must not wait on `codex` (the slug is shown until Settings loads names).
        with patch.object(translators, 'codex_models', side_effect=AssertionError('label ran codex')), \
                patch.object(translators, 'codex_signed_in', side_effect=AssertionError('label ran codex')):
            self.assertEqual(translators.label({'tool': 'codex', 'model': 'gpt-6.1-sol', 'effort': 'low'}), 'Codex · gpt-6.1-sol · low')

    def test_refresh_keeps_the_old_list_until_the_new_one_is_ready(self):
        seen = []
        with patch.object(locate, 'tool_path', return_value=Path('codex.exe')), \
                patch.object(translators, 'codex_signed_in', return_value=True), \
                patch.object(translators, 'codex_models', side_effect=lambda exe: (seen.append(dict(translators.CACHE)), [SOL])[1]):
            translators.catalog()
            translators.catalog(refresh=True)
        self.assertEqual(seen[0], {})  # the very first fill
        self.assertEqual(seen[1]['codex']['models'], [SOL])  # a request during the refresh still sees the old list

    def test_label(self):
        with patch.object(locate, 'tool_path', return_value=Path('codex.exe')), \
                patch.object(translators, 'codex_models', return_value=[SOL]), patch.object(translators, 'codex_signed_in', return_value=True):
            translators.catalog()  # Settings loads the names once
            self.assertEqual(translators.label({'tool': 'codex', 'model': 'gpt-6.1-sol', 'effort': 'low'}), 'Codex · GPT-6.1-Sol · low')
            self.assertEqual(translators.label({'tool': 'claude', 'model': '', 'effort': 'high'}), 'Claude Code · Default · high')
            self.assertEqual(translators.label({'tool': 'claude', 'model': 'opus', 'effort': 'max'}), 'Claude Code · Opus · max')
            self.assertEqual(translators.label({'tool': 'codex', 'model': 'my-model', 'effort': 'low'}), 'Codex · my-model · low')


if __name__ == '__main__':
    unittest.main()
