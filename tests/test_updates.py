"""Signed updates: never touches GitHub, never runs a real installer."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from arabic_relay import models, paths, speech, store, translate, updates

SETUP = b'MZ fake installer for 0.2.0'


def public_hex(key):
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class Updates(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.data = Path(temp.name)
        self.key = Ed25519PrivateKey.generate()
        self.signature = self.key.sign(SETUP)
        self.release = self.make_release('v0.2.0')
        self.requests = []
        for patcher in (patch.object(paths, 'DATA', self.data), patch.object(updates, '__version__', '0.1.0'),
                        patch.object(updates, 'PUBLIC_KEY', public_hex(self.key)),
                        patch.object(updates.urllib.request, 'urlopen', side_effect=self.urlopen),
                        patch.object(models, 'fetch', side_effect=self.fetch),
                        patch.object(updates.subprocess, 'Popen', side_effect=AssertionError('installer started'))):
            patcher.start()
            self.addCleanup(patcher.stop)
        updates.STATE.update(available=None, releaseUrl=None, failed=None)
        self.addCleanup(updates.STATE.update, available=None, releaseUrl=None, failed=None)

    def make_release(self, tag, signed=True):
        name = f'ArabicRelay-Setup-{tag[1:]}.exe'
        assets = [{'name': name, 'size': len(SETUP), 'browser_download_url': 'https://example.test/' + name},
                  {'name': 'SHA256SUMS.txt', 'size': 100, 'browser_download_url': 'https://example.test/SHA256SUMS.txt'}]
        if signed:
            assets.append({'name': name + '.sig', 'size': 64, 'browser_download_url': 'https://example.test/' + name + '.sig'})
        return {'tag_name': tag, 'html_url': f'https://github.com/{updates.REPO}/releases/tag/{tag}', 'assets': assets}

    def urlopen(self, request, timeout=None):
        self.requests.append(request)
        url = request.full_url
        if url == updates.API:
            return Reply(json.dumps(self.release).encode())
        if url.endswith('SHA256SUMS.txt'):
            return Reply(f'{hashlib.sha256(SETUP).hexdigest()}  ArabicRelay-Setup-0.2.0.exe\n'.encode())
        if url.endswith('.sig'):
            return Reply(self.signature)
        raise AssertionError(url)

    def fetch(self, url, dest, sha256, size, report):
        self.assertEqual(sha256, hashlib.sha256(SETUP).hexdigest())
        dest.write_bytes(SETUP)
        return dest

    def folder(self):
        return self.data / 'updates'

    def test_versions_compare_numerically(self):
        self.assertTrue(updates.newer('v0.10.0', '0.9.1'))
        self.assertFalse(updates.newer('0.1.0', '0.1.0'))
        for bad in ('0.2', 'latest', '', 'v1.2.3-beta'):
            with self.assertRaises(ValueError):
                updates.parse_version(bad)

    def test_signatures(self):
        file = self.data / 'setup.exe'
        file.write_bytes(SETUP)
        self.assertTrue(updates.verify(file, self.signature))
        self.assertFalse(updates.verify(file, self.signature, public_hex(Ed25519PrivateKey.generate())))
        file.write_bytes(b'X' + SETUP[1:])
        self.assertFalse(updates.verify(file, self.signature))
        self.assertFalse(updates.verify(file, b'short'))

    def test_auto_mode_downloads_and_marks_ready(self):
        state = updates.check()
        self.assertEqual((state['available'], state['ready']), ('0.2.0', '0.2.0'))
        self.assertEqual(state['releaseUrl'], self.release['html_url'])
        self.assertTrue((self.folder() / 'ready.json').is_file())
        self.assertTrue(all(r.get_header('User-agent', '').startswith('Arabic-Relay/') for r in self.requests))

    def test_tampered_or_unsigned_update_is_deleted(self):
        for release, signature in ((self.release, self.key.sign(b'something else')), (self.make_release('v0.2.0', signed=False), self.signature)):
            self.release, self.signature = release, signature
            with self.assertLogs(level='WARNING'):
                state = updates.check()
            self.assertIsNone(state['ready'])
            self.assertEqual(list(self.folder().glob('*')), [])

    def test_notify_mode_only_announces(self):
        updates.set_preferences('notify', True)
        with patch.object(models, 'fetch', side_effect=AssertionError('downloaded in notify mode')):
            state = updates.check()
        self.assertEqual((state['available'], state['ready'], state['mode']), ('0.2.0', None, 'notify'))

    def test_off_mode_never_checks(self):
        self.assertTrue(updates.due())
        store.set_setting('update_checked', time.time() - 3600)
        self.assertFalse(updates.due())
        store.set_setting('update_checked', time.time() - 25 * 3600)
        self.assertTrue(updates.due())
        updates.set_preferences('off', True)
        self.assertFalse(updates.due())
        with self.assertRaises(ValueError):
            updates.set_preferences('weekly', True)

    def test_older_or_same_release_is_ignored(self):
        for tag in ('v0.1.0', 'v0.0.9'):
            self.release = self.make_release(tag)
            state = updates.check()
            self.assertEqual((state['available'], state['ready']), (None, None))

    def test_install_refused_while_busy(self):
        updates.check()
        for lock in (translate.TRANSLATE_LOCK, speech.TRANSCRIBE_LOCK):
            held, release = threading.Event(), threading.Event()

            def hold():
                with lock:
                    held.set()
                    release.wait(5)
            worker = threading.Thread(target=hold)
            worker.start()
            held.wait(5)
            try:
                with self.assertRaisesRegex(ValueError, '^Finish recording, transcribing, translating or sending first, then update.$'):
                    updates.install(True)
            finally:
                release.set()
                worker.join()

    def test_install_reverifies(self):
        updates.check()
        (self.folder() / 'ArabicRelay-Setup-0.2.0.exe').write_bytes(b'X' + SETUP[1:])
        with self.assertRaisesRegex(ValueError, 'signature'):
            updates.install(True)
        self.assertIsNone(updates.state()['ready'])
        self.assertFalse((self.folder() / 'ArabicRelay-Setup-0.2.0.exe').exists())

    def test_install_runs_setup_silently(self):
        updates.check()
        exe = str(self.folder() / 'ArabicRelay-Setup-0.2.0.exe')
        flags = ['/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/CLOSEAPPLICATIONS']
        with patch.object(updates.subprocess, 'Popen') as popen:
            self.assertEqual(updates.install(True), {'installing': '0.2.0'})
            self.assertEqual(popen.call_args.args[0], [exe, *flags, '/RELAUNCH=1'])
            updates.install(False)
            self.assertEqual(popen.call_args.args[0], [exe, *flags])
        self.assertEqual(json.loads((self.folder() / 'attempt.json').read_text(encoding='utf-8')), {'version': '0.2.0'})
        # The reopened app must start as its own program, not as a child of this frozen one.
        self.assertEqual(popen.call_args.kwargs['env']['PYINSTALLER_RESET_ENVIRONMENT'], '1')

    def test_failed_install_is_offered_again(self):
        self.folder().mkdir()
        (self.folder() / 'attempt.json').write_text('{"version": "0.2.0"}', encoding='utf-8')
        updates.startup()
        self.assertEqual(updates.state()['failed'], '0.2.0')
        updates.STATE['failed'] = None
        with patch.object(updates, '__version__', '0.2.0'):
            updates.startup()
        self.assertIsNone(updates.state()['failed'])
        self.assertEqual(list(self.folder().glob('*')), [])

    def test_startup_survives_a_locked_installer(self):
        # Restart and update reopens the app while Setup still runs; its exe can't be deleted yet.
        self.folder().mkdir()
        (self.folder() / 'attempt.json').write_text('{"version": "0.1.0"}', encoding='utf-8')
        (self.folder() / 'ArabicRelay-Setup-0.1.0.exe').write_bytes(b'MZ')
        real = Path.unlink

        def locked(path, missing_ok=False):
            if path.suffix == '.exe':
                raise PermissionError(32, 'The process cannot access the file')
            return real(path, missing_ok=missing_ok)
        with patch.object(Path, 'unlink', locked):
            updates.startup()
        self.assertFalse((self.folder() / 'attempt.json').exists())
        with patch.object(updates, 'startup', side_effect=OSError('disk')), patch.object(updates.threading, 'Thread'):
            updates.start_background()  # never stops the window from opening

    def test_release_page_link_must_be_this_repos(self):
        # The banner's links open in the system shell; only this repo's release pages may get there.
        default = f'https://github.com/{updates.REPO}/releases'
        for url in ('search-ms:query=x', 'file://host/share/setup.exe', 'https://github.com/someone/else/releases/tag/v0.2.0',
                    f'https://github.com/{updates.REPO}/releases/../../x', None):
            self.release['html_url'] = url
            self.assertEqual(updates.check()['releaseUrl'], default, url)
            updates.STATE['releaseUrl'] = None
            self.assertEqual(updates.state()['releaseUrl'], default, url)
        self.release['html_url'] = f'https://github.com/{updates.REPO}/releases/tag/v0.2.0'
        self.assertEqual(updates.check()['releaseUrl'], self.release['html_url'])

    def test_install_on_quit_respects_the_setting(self):
        updates.check()
        with patch.object(updates.subprocess, 'Popen') as popen:
            updates.set_preferences('auto', False)
            updates.install_on_quit()
            popen.assert_not_called()
            updates.set_preferences('auto', True)
            updates.install_on_quit()
            self.assertNotIn('/RELAUNCH=1', popen.call_args.args[0])
            updates.install_on_quit()  # the attempt for this version is already running
            self.assertEqual(popen.call_count, 1)


class ReleaseKey(unittest.TestCase):
    def test_public_key_is_set(self):
        self.assertRegex(updates.PUBLIC_KEY, '^[0-9a-f]{64}$')
        self.assertNotEqual(updates.PUBLIC_KEY, '0' * 64)


if __name__ == '__main__':
    unittest.main()
