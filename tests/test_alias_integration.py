"""Approved rules reach both modes: final transcripts, prompt preparation and typed text, with the original kept."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from arabic_relay import aliases, paths, speech, store, translate


class Transcripts(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        patcher = patch.object(paths, 'DATA', Path(temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        aliases.add('ريدزاين', 'redesign')
        aliases.add('اللوك فلو', 'login flow')

    def test_whisper_transcript_gets_rules_and_keeps_the_recognition_output(self):
        response = speech.whisper_response({'text': 'عايز أعمل ريدزاين ومش أغير اللوك فلو', 'segments': []}, Path('model'), False)
        self.assertEqual(response['text'], 'عايز أعمل redesign ومش أغير login flow')
        self.assertEqual(response['rawText'], 'عايز أعمل ريدزاين ومش أغير اللوك فلو')
        self.assertEqual([r['canonical'] for r in response['replacements']], ['redesign', 'login flow'])

    def test_cohere_transcript_gets_rules_too(self):
        with patch.object(speech, 'load_cohere_key', return_value='synthetic'), \
                patch.object(speech, 'cohere_request', return_value='ريدزاين جديد'):
            response = speech.transcribe_cohere(speech.silent_wav(1), 'audio/wav')
        self.assertEqual(response['text'], 'redesign جديد')
        self.assertEqual(response['rawText'], 'ريدزاين جديد')

    def test_inline_keeps_raw_segments_and_sets_original_text(self):
        result = {'text': 'ريدزاين', 'segments': [{'text': ' ريدزاين', 'words': [{'text': ' ريدزاين'}]}],
                  'effectiveAsrConfig': {'path': 'incremental'}}
        response = speech.whisper_response(result, Path('model'), True)
        self.assertEqual(response['text'], 'redesign')
        self.assertEqual(response['originalText'], 'ريدزاين')
        self.assertEqual(response['segments'], result['segments'])

    def test_prompt_preparation_replaces_before_the_model_and_reports_what_changed(self):
        captured = {}
        def fake(source, instructions, notices=None, **runtime):
            captured['text'] = source['dictation']
            return 'Redesign the page.'
        with patch.object(translate, 'codex_text', fake):
            result = translate.translate('اعمل ريدزاين للصفحة')
        self.assertEqual(captured['text'], 'اعمل redesign للصفحة')
        self.assertEqual(result['english'], 'Redesign the page.')
        self.assertEqual(result['originalSource'], 'اعمل ريدزاين للصفحة')
        self.assertEqual(result['replacements'][0]['canonical'], 'redesign')

    def test_a_conflicting_pair_changes_nothing_and_is_reported(self):
        aliases.add('داتا بيز', 'database')
        aliases.add('بيز سيرفر', 'base server')
        result = aliases.apply('داتا بيز سيرفر')
        self.assertEqual(result['text'], 'داتا بيز سيرفر')
        self.assertEqual(len(result['conflicts']), 1)
        self.assertEqual(result['replacements'], [])

    def test_rules_do_not_apply_to_generated_english_or_teach_themselves(self):
        with patch.object(translate, 'codex_text', return_value='Use the redesign.'):
            result = translate.translate('ريدزاين')
        # The reviewed source is the corrected text, so the same correction is not re-learned as a new proposal.
        self.assertEqual([p['heard'] for p in aliases.listing()['proposals']], [])
        self.assertEqual(result['english'], 'Use the redesign.')


if __name__ == '__main__':
    unittest.main()
