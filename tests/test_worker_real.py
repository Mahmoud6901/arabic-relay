"""The real speech worker, started the way the app starts it, transcribes on the CPU. Needs the cached tiny model."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from arabic_relay import locate, speech


def tiny_model():
    try:
        return locate.validate_model_folder(locate.hf_cache() / 'models--Systran--faster-whisper-tiny')
    except (ValueError, OSError):
        return None


SPEAK = r'''
Add-Type -AssemblyName System.Speech
$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ($voice.GetInstalledVoices().Count -eq 0) { exit 3 }  # e.g. a CI runner without voices
$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$voice.SetOutputToWaveFile($args[0], $format)
$voice.Speak('Hello world. This is a short test.')
$voice.Dispose()
'''


@unittest.skipIf(tiny_model() is None, 'tiny model not cached: snapshot_download("Systran/faster-whisper-tiny")')
class RealWorker(unittest.TestCase):
    def test_real_worker_transcribes_english_on_cpu(self):
        with tempfile.TemporaryDirectory() as folder:
            wav = Path(folder) / 'hello.wav'
            script = Path(folder) / 'speak.ps1'
            script.write_text(SPEAK, encoding='utf-8')
            spoken = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script), str(wav)], timeout=60)
            if spoken.returncode == 3:
                self.skipTest('no speech synthesis voice installed')
            self.assertEqual(spoken.returncode, 0)
            with patch.object(speech, 'speech_device', return_value='cpu'):
                result = speech.run_worker(tiny_model(), wav.read_bytes(), [])
        self.assertIn('hello', result['text'].lower())
        self.assertEqual(result['device'], 'CPU')
        self.assertFalse(result['fallback'])


    def test_real_worker_transcribes_arabic_on_cpu(self):
        # Google FLEURS ar_eg test row 1778 (CC-BY-4.0), see tests/fixtures/README.md.
        clip = Path(__file__).parent / 'fixtures' / 'arabic_fleurs.wav'
        with patch.object(speech, 'speech_device', return_value='cpu'):
            result = speech.run_worker(tiny_model(), clip.read_bytes(), [])
        self.assertGreaterEqual(len(re.findall('[\u0621-\u064a]', result['text'])), 3, result['text'])
        self.assertEqual(result['device'], 'CPU')


if __name__ == '__main__':
    unittest.main()
