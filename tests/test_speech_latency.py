"""Long dictation preserves every sample and processes independent sections concurrently."""
import io
import threading
import unittest
import wave
from unittest.mock import Mock, patch

from arabic_relay import speech, speech_worker


def wav(pcm):
    out = io.BytesIO()
    with wave.open(out, 'wb') as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(16000)
        clip.writeframes(pcm)
    return out.getvalue()


def pcm(audio):
    with wave.open(io.BytesIO(audio), 'rb') as clip:
        return clip.readframes(clip.getnframes())


class LongDictation(unittest.TestCase):
    def long_audio(self):
        return wav((b'\x00\x10' * (59 * 16000) + b'\0\0' * 16000) * 3)

    def test_split_only_at_pauses_and_preserve_every_sample(self):
        original = self.long_audio()
        clips = speech.cohere_clips(original)
        self.assertEqual(len(clips), 3)
        self.assertEqual(b''.join(map(pcm, clips)), pcm(original))
        for clip in clips[:-1]:
            self.assertEqual(pcm(clip)[-320:], b'\0' * 320)

    def test_short_or_uninterrupted_speech_is_not_cut(self):
        for original in (wav(b'\x00\x10' * 16000), wav(b'\x00\x10' * (180 * 16000))):
            self.assertEqual(speech.cohere_clips(original), [original])

    def test_cohere_runs_two_sections_at_once_and_keeps_audio_order(self):
        original = self.long_audio()
        clips = speech.cohere_clips(original)
        rendezvous = threading.Barrier(2, timeout=2)
        lock = threading.Lock()
        active = peak = calls = 0
        def request(audio, key):
            nonlocal active, peak, calls
            with lock:
                index = calls
                calls += 1
                active += 1
                peak = max(peak, active)
            if index < 2:
                rendezvous.wait()
            with lock:
                active -= 1
            return str(clips.index(audio))
        with patch.object(speech, 'load_cohere_key', return_value='synthetic'), \
                patch.object(speech, 'cohere_request', side_effect=request):
            result = speech.transcribe_cohere(original, 'audio/wav')
        self.assertEqual(result['text'], '0 1 2')
        self.assertEqual(peak, 2)
        self.assertEqual(calls, 3)
        self.assertFalse(speech.TRANSCRIBE_LOCK.locked())

    def test_failed_section_never_returns_partial_transcript_or_retries(self):
        calls = []
        def request(audio, key):
            calls.append(audio)
            raise ValueError('Cohere reached an account or rate limit.')
        with patch.object(speech, 'load_cohere_key', return_value='synthetic'), \
                patch.object(speech, 'cohere_request', side_effect=request), \
                self.assertRaisesRegex(ValueError, 'rate limit'):
            speech.transcribe_cohere(self.long_audio(), 'audio/wav')
        self.assertEqual(len(calls), len(set(calls)))
        self.assertFalse(speech.TRANSCRIBE_LOCK.locked())

    def test_empty_section_does_not_discard_other_speech(self):
        with patch.object(speech, 'load_cohere_key', return_value='synthetic'), \
                patch.object(speech, 'cohere_request', side_effect=['first', '', 'last']):
            result = speech.transcribe_cohere(self.long_audio(), 'audio/wav')
        self.assertEqual(result['text'].split(), ['first', 'last'])

    def test_cpu_and_short_gpu_recordings_keep_serial_decoding(self):
        for device, seconds in [('cpu', 60), ('cuda', 6)]:
            model = Mock(transcribe=Mock(return_value=(iter([Mock(text=' original')]), None)))
            with patch('faster_whisper.WhisperModel', return_value=model), \
                    patch('faster_whisper.BatchedInferencePipeline') as batched:
                result = speech_worker.run_model([0] * (seconds * 16000), 'folder', device, [])
            self.assertEqual(result['text'], 'original')
            batched.assert_not_called()

    def test_long_gpu_dictation_uses_batched_decoder_with_mixed_language_hints(self):
        model = Mock()
        batch = Mock(transcribe=Mock(return_value=(iter([Mock(text=' عربي'), Mock(text=' React')]), None)))
        with patch('faster_whisper.WhisperModel', return_value=model), \
                patch('faster_whisper.BatchedInferencePipeline', return_value=batch):
            result = speech_worker.run_model([0] * (60 * 16000), 'folder', 'cuda', ['React'])
        self.assertEqual(result['text'], 'عربي React')
        model.transcribe.assert_not_called()
        options = batch.transcribe.call_args.kwargs
        self.assertTrue(options['multilingual'])
        self.assertEqual(options['hotwords'], 'React')
        self.assertEqual(options['beam_size'], 5)
        self.assertEqual(options['batch_size'], 2)

    def test_batch_memory_pressure_retries_serially_on_same_gpu(self):
        model = Mock(transcribe=Mock(return_value=(iter([Mock(text=' preserved')]), None)))
        batch = Mock(transcribe=Mock(side_effect=RuntimeError('CUDA out of memory')))
        with patch('faster_whisper.WhisperModel', return_value=model) as load, \
                patch('faster_whisper.BatchedInferencePipeline', return_value=batch):
            result = speech_worker.run_model([0] * (60 * 16000), 'folder', 'cuda', [])
        load.assert_called_once()
        self.assertEqual(result, {'text': 'preserved', 'device': 'GPU', 'fallback': False})
        batch.transcribe.assert_called_once()


if __name__ == '__main__':
    unittest.main()
