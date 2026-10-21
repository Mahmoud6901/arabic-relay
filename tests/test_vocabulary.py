"""Personal vocabulary learns only reviewed source and is reused without T3 writes."""
import json
from contextlib import closing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from arabic_relay import paths, speech, store, t3, translate


class VocabularyLearning(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        local = patch.object(paths, 'DATA', Path(temp.name))
        local.start()
        self.addCleanup(local.stop)

    def test_setting_round_trip(self):
        self.assertIsNone(store.get_setting('missing'))
        self.assertEqual(store.get_setting('missing', 'fallback'), 'fallback')
        store.set_setting('prompt_model', 'codex')
        self.assertEqual(store.get_setting('prompt_model'), 'codex')

    def test_legacy_vocabulary_moves_without_losing_counts_settings_or_reappearing(self):
        with store.database() as db:
            db.execute('CREATE TABLE vocabulary (word TEXT PRIMARY KEY, uses INTEGER NOT NULL, updated REAL NOT NULL)')
            db.execute('CREATE TABLE corrections (heard TEXT PRIMARY KEY, spelling TEXT NOT NULL, updated REAL NOT NULL)')
            db.execute('INSERT INTO vocabulary VALUES (?, ?, ?)', ('React', 7, 123.5))
            db.execute('INSERT INTO corrections VALUES (?, ?, ?)', ('ريأكت', 'React', 123.5))
            db.execute("INSERT INTO settings VALUES ('self_learning', 0)")
        expected = {'words': ['React'], 'corrections': [{'heard': 'ريأكت', 'spelling': 'React'}]}
        self.assertEqual(store.personal_vocabulary(), expected)
        self.assertEqual(store.personal_vocabulary(), expected)
        self.assertFalse(store.self_learning_enabled())
        with closing(store.sqlite3.connect(paths.DATA / 'vocabulary.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT * FROM vocabulary').fetchall(), [('React', 7, 123.5)])
            self.assertEqual({row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")},
                             {'vocabulary', 'corrections'})
        with store.database() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name IN ('vocabulary', 'corrections')").fetchone())
        store.forget_vocabulary()
        self.assertEqual(store.personal_vocabulary(), {'words': [], 'corrections': []})

    def test_prepare_learns_reviewed_source_not_generated_translation(self):
        original = 'الديدلاين بعد الميتنج وخلي React زي ما هو'
        reviewed = 'الـ deadline بعد الـ meeting وخلي React زي ما هو'
        with patch.object(translate, 'codex_text', return_value='Use an invented Kubernetes requirement.'), patch.object(t3, 'tool') as t3_tool:
            result = translate.translate(reviewed, original)
        memory = result['vocabulary']
        self.assertEqual(set(memory['words']), {'deadline', 'meeting', 'React'})
        self.assertIn({'heard': 'الديدلاين', 'spelling': 'deadline'}, memory['corrections'])
        self.assertIn({'heard': 'الميتنج', 'spelling': 'meeting'}, memory['corrections'])
        self.assertEqual(store.personal_vocabulary(), memory)  # New connection, persistent storage.
        t3_tool.assert_not_called()

    def test_failed_preparation_and_spelling_restore_do_not_learn(self):
        with patch.object(translate, 'codex_text', side_effect=ValueError('Unavailable')):
            with self.assertRaises(ValueError):
                translate.translate('راجع React')
        with patch.object(translate, 'codex_text', return_value='راجع React'):
            translate.restore_english('راجع React')
        self.assertEqual(store.personal_vocabulary(), {'words': [], 'corrections': []})

    def test_saved_words_reach_whisper_and_corrections_reach_codex(self):
        store.learn_vocabulary('deadline في React', 'ديدلاين في React')
        process = Mock(returncode=0)
        process.communicate.return_value = (json.dumps({'text': 'راجع React'}).encode(), b'')
        with patch.object(speech, 'speech_model_path', return_value=Path('model')), \
                patch.object(speech.subprocess, 'Popen', return_value=process), \
                patch.object(translate, 'codex_text', return_value='راجع React') as codex:
            speech.transcribe(b'\x1aE\xdf\xa3fake', 'audio/webm', 'ExplicitName')
            codex.assert_not_called()
            translate.restore_english('راجع React', 'ExplicitName')
        keywords = json.loads(process.communicate.call_args.args[0].split(b'\n', 1)[0])['keywords']
        self.assertEqual(keywords[0], 'ExplicitName')
        self.assertIn('deadline', keywords)
        content = codex.call_args.args[0]
        self.assertIn('deadline', content['learned_words'])
        self.assertIn({'heard': 'ديدلاين', 'spelling': 'deadline'}, content['reviewed_spelling_examples'])

    def test_only_short_corrections_are_saved_and_memory_is_bounded(self):
        store.learn_vocabulary('Rewrite the entire sentence with React', 'عايزك تعدل النص كله عشان يكون كويس')
        self.assertEqual(store.personal_vocabulary()['corrections'], [])
        store.learn_vocabulary(' '.join('Term' + str(i) for i in range(350)))
        self.assertEqual(len(store.personal_vocabulary()['words']), 300)
        store.forget_vocabulary()
        self.assertEqual(store.personal_vocabulary(), {'words': [], 'corrections': []})

    def test_storage_failure_preserves_prepared_draft(self):
        with patch.object(translate, 'codex_text', return_value='Keep React.'), \
                patch.object(store, 'learn_vocabulary', side_effect=store.sqlite3.OperationalError('locked')):
            result = translate.translate('خلي React')
        self.assertEqual(result['english'], 'Keep React.')
        self.assertIn('learningError', result)

    def test_learning_can_pause_and_resume_without_erasing_memory(self):
        self.assertTrue(store.self_learning_enabled())
        store.learn_vocabulary('deadline في React', 'ديدلاين في React')
        before = store.personal_vocabulary()
        with store.database(vocabulary=True) as db:
            before_rows = [tuple(row) for row in db.execute('SELECT * FROM vocabulary ORDER BY word')]
        self.assertEqual(store.set_self_learning(False), {'selfLearning': False})
        self.assertFalse(store.self_learning_enabled())  # Persisted across database connections.
        with patch.object(translate, 'codex_text', return_value='Use Kubernetes.'):
            result = translate.translate('meeting في React', 'ميتنج في React')
        self.assertFalse(result['selfLearning'])
        self.assertEqual(result['english'], 'Use Kubernetes.')
        self.assertEqual(store.personal_vocabulary(), before)
        with store.database(vocabulary=True) as db:
            self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM vocabulary ORDER BY word')], before_rows)
        store.set_self_learning(True)
        store.learn_vocabulary('meeting في React', 'ميتنج في React')
        self.assertIn('meeting', store.personal_vocabulary()['words'])
        self.assertIn({'heard': 'ميتنج', 'spelling': 'meeting'}, store.personal_vocabulary()['corrections'])

    def test_disabled_learning_keeps_saved_and_explicit_hints_available(self):
        store.learn_vocabulary('deadline في React', 'ديدلاين في React')
        store.set_self_learning(False)
        process = Mock(returncode=0)
        process.communicate.return_value = (json.dumps({'text': 'راجع React'}).encode(), b'')
        with patch.object(speech, 'speech_model_path', return_value=Path('model')), \
                patch.object(speech.subprocess, 'Popen', return_value=process), \
                patch.object(translate, 'codex_text', return_value='راجع React'):
            speech.transcribe(b'\x1aE\xdf\xa3fake', 'audio/webm', 'ExplicitName')
        keywords = json.loads(process.communicate.call_args.args[0].split(b'\n', 1)[0])['keywords']
        self.assertEqual(keywords[0], 'ExplicitName')
        self.assertIn('deadline', keywords)
        store.forget_vocabulary()
        self.assertFalse(store.self_learning_enabled())

    def test_learning_setting_requires_a_boolean(self):
        for value in [None, 0, 1, 'false', []]:
            with self.assertRaises(ValueError):
                store.set_self_learning(value)
        self.assertTrue(store.self_learning_enabled())


if __name__ == '__main__':
    unittest.main()
