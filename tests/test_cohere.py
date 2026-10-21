"""Cohere boundary checks with synthetic audio and mocked HTTP; no API calls."""
import io
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
import wave
from unittest.mock import Mock, patch
from arabic_relay import server, paths, speech, t3, translate


def wav():
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(16000)
        clip.writeframes(b'\0\0' * 160)
    return stream.getvalue()


class CohereBoundary(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        local = patch.object(paths, 'DATA', Path(temporary.name))
        local.start()
        self.addCleanup(local.stop)

    def test_key_is_encrypted_and_removable(self):
        self.assertEqual(speech.save_cohere_key('synthetic-test-key'), {'configured': True})
        self.assertNotIn(b'synthetic-test-key', (paths.DATA / 'cohere.bin').read_bytes())
        self.assertEqual(speech.load_cohere_key(), 'synthetic-test-key')
        self.assertEqual(speech.save_cohere_key(''), {'configured': False})
        self.assertEqual(speech.load_cohere_key(), '')
        for key in [None, 'bad\nkey', 'bad key']:
            with self.assertRaises(ValueError):
                speech.save_cohere_key(key)

    def test_exact_model_language_and_wav_reach_cohere_once(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = json.dumps({'text': 'ضيف dark mode'}).encode()
        opener = Mock()
        opener.open.return_value = response
        with patch.object(speech, 'load_cohere_key', return_value='synthetic-key'), \
                patch.object(server.urllib.request, 'build_opener', return_value=opener), \
                patch.object(speech.subprocess, 'Popen') as worker, patch.object(t3, 'tool') as t3_tool, \
                patch.object(speech, 'speech_model_path', side_effect=AssertionError('No local model needed')), \
                patch.object(translate, 'restore_english', side_effect=AssertionError('No automatic Codex call')) as restore:
            result = speech.transcribe(wav(), 'audio/wav', 'dark mode', provider='cohere')
        opener.open.assert_called_once()
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.cohere.com/v2/audio/transcriptions')
        self.assertEqual(request.get_header('Authorization'), 'Bearer synthetic-key')
        self.assertIn(speech.COHERE_MODEL.encode(), request.data)
        self.assertIn(b'name="language"\r\n\r\nar\r\n', request.data)
        self.assertIn(wav(), request.data)
        self.assertEqual(result['text'], 'ضيف dark mode')
        self.assertEqual(result['model'], speech.COHERE_MODEL)
        self.assertNotIn('spellingError', result)
        restore.assert_not_called()
        worker.assert_not_called()
        t3_tool.assert_not_called()
        self.assertFalse(speech.TRANSCRIBE_LOCK.locked())

    def test_missing_key_invalid_audio_and_unknown_provider_never_upload(self):
        with patch.object(server.urllib.request, 'build_opener') as request, \
                patch.object(speech.subprocess, 'Popen') as worker:
            for audio, mime, provider in [(wav(), 'audio/wav', 'cohere'),
                                         (b'RIFFbad!WAVE', 'audio/wav', 'cohere'),
                                         (b'\x1aE\xdf\xa3fake', 'audio/webm', 'cohere'),
                                         (wav(), 'audio/wav', 'unknown')]:
                with self.assertRaises(ValueError):
                    speech.transcribe(audio, mime, provider=provider)
        request.assert_not_called()
        worker.assert_not_called()

    def test_http_errors_are_redacted_and_never_retried(self):
        for error in [urllib.error.HTTPError('url', 401, 'private key detail', {}, None),
                      urllib.error.HTTPError('url', 429, 'private key detail', {}, None),
                      urllib.error.URLError('private key detail')]:
            opener = Mock()
            opener.open.side_effect = error
            with patch.object(speech, 'load_cohere_key', return_value='synthetic-key'), \
                    patch.object(server.urllib.request, 'build_opener', return_value=opener), \
                    patch.object(speech.subprocess, 'Popen') as worker, self.assertRaises(ValueError) as caught:
                speech.transcribe(wav(), 'audio/wav', provider='cohere')
            self.assertNotIn('private key detail', str(caught.exception))
            opener.open.assert_called_once()
            worker.assert_not_called()
            self.assertFalse(speech.TRANSCRIBE_LOCK.locked())

    def test_redirect_cannot_forward_credentials(self):
        self.assertIsNone(speech.NoCohereRedirect().redirect_request(None, None, 307, '', {}, 'https://elsewhere.test'))


if __name__ == '__main__':
    unittest.main()
