"""Loading the speech model belongs during recording, not after Stop."""
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from arabic_relay import speech, speech_worker

class RecordingPreload(unittest.TestCase):
    def tearDown(self):
        speech.cancel_preparation()

    def test_transcription_reuses_prepared_process_without_second_launch(self):
        proc=Mock(returncode=0)
        proc.poll.return_value=None
        proc.communicate.return_value=(json.dumps({'text':'same transcript','device':'GPU'}).encode(),b'')
        with patch.object(speech,'speech_model_path',return_value=Path('model')), patch.object(speech,'speech_device',return_value='cuda'), patch.object(speech,'engine_enabled',return_value=False), patch.object(speech.subprocess,'Popen',return_value=proc) as launch:
            speech.prepare_worker('recording-test-1234567890')
            result=speech.run_worker(Path('model'),b'audio',[])
            self.assertEqual(result['text'],'same transcript')
            self.assertEqual(launch.call_count,1)
            self.assertIn('--preload',launch.call_args.args[0])
            self.assertEqual(proc.communicate.call_args.args[0].split(b'\n',1)[1],b'audio')

    def test_cancel_releases_model_without_transcribing_or_cancelling_new_recording(self):
        proc=Mock();proc.poll.return_value=None
        with patch.object(speech,'speech_model_path',return_value=Path('model')), patch.object(speech,'speech_device',return_value='cpu'), patch.object(speech.subprocess,'Popen',return_value=proc):
            speech.prepare_worker('recording-test-1234567890')
            speech.cancel_preparation('different-recording-123456')
            proc.kill.assert_not_called()
            speech.cancel_preparation('recording-test-1234567890')
            proc.kill.assert_called_once()
            proc.communicate.assert_called_once_with()

    def test_changed_model_discards_preload_and_uses_normal_path(self):
        proc=Mock();proc.poll.return_value=None
        with patch.object(speech,'speech_model_path',return_value=Path('old-model')), patch.object(speech,'speech_device',return_value='cpu'), patch.object(speech.subprocess,'Popen',return_value=proc):
            speech.prepare_worker('recording-test-1234567890')
            self.assertIsNone(speech.take_prepared_worker(Path('new-model'),'cpu'))
            proc.kill.assert_called_once()

    def test_expiry_and_shutdown_release_unclaimed_worker(self):
        proc=Mock();proc.poll.return_value=None
        with patch.object(speech,'speech_model_path',return_value=Path('model')), patch.object(speech,'speech_device',return_value='cpu'), patch.object(speech.subprocess,'Popen',return_value=proc), patch.object(speech.threading,'Timer') as timer:
            speech.prepare_worker('recording-test-1234567890')
            delay,callback=timer.call_args.args
            self.assertEqual(delay,360)
            callback(*timer.call_args.kwargs['args'])
            proc.kill.assert_called_once()
            self.assertIsNone(speech.PREPARED)

    def test_cancel_cannot_kill_a_claimed_transcription(self):
        proc=Mock();proc.poll.return_value=None
        with patch.object(speech,'speech_model_path',return_value=Path('model')), patch.object(speech,'speech_device',return_value='cpu'), patch.object(speech.subprocess,'Popen',return_value=proc):
            speech.prepare_worker('recording-test-1234567890')
            self.assertIs(speech.take_prepared_worker(Path('model'),'cpu'),proc)
            speech.cancel_preparation('recording-test-1234567890')
            proc.kill.assert_not_called()

    def test_loaded_model_keeps_recognition_settings_without_loading_again(self):
        model=Mock(transcribe=Mock(return_value=(iter([Mock(text=' original')]),None)))
        with patch('faster_whisper.WhisperModel') as load:
            result=speech_worker.run_model([0]*16000,'model','cpu',['React'],loaded_model=('cpu',model))
        load.assert_not_called()
        self.assertEqual(result['text'],'original')
        options=model.transcribe.call_args.kwargs
        self.assertEqual(options['beam_size'],5)
        self.assertTrue(options['multilingual'])
        self.assertEqual(options['hotwords'],'React')

if __name__=='__main__':unittest.main()
