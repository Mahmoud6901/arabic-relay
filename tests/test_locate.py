"""Finding a faster-whisper model wherever the user keeps it."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from arabic_relay import locate


def make_model(folder):
    folder.mkdir(parents=True, exist_ok=True)
    for name in ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.json'):
        (folder / name).write_bytes(b'')
    return folder


class ModelFolder(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_valid_folder(self):
        self.assertEqual(locate.validate_model_folder(make_model(self.root / 'm')), self.root / 'm')

    def test_arabic_and_space_path_validates(self):
        folder = make_model(self.root / 'نماذج' / 'large v3')
        self.assertEqual(locate.validate_model_folder(str(folder)), folder)

    def test_missing_files_are_rejected(self):
        (self.root / 'empty').mkdir()
        with self.assertRaisesRegex(ValueError, 'faster-whisper'):
            locate.validate_model_folder(self.root / 'empty')

    def test_missing_folder_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'faster-whisper'):
            locate.validate_model_folder(self.root / 'nowhere')

    def test_openai_pt_model_is_rejected_by_name(self):
        folder = self.root / 'pt'
        folder.mkdir()
        (folder / 'large-v3.pt').write_bytes(b'')
        with self.assertRaisesRegex(ValueError, r'\.pt'):
            locate.validate_model_folder(folder)

    def test_whisper_cpp_model_is_rejected_by_name(self):
        folder = self.root / 'cpp'
        folder.mkdir()
        (folder / 'ggml-large-v3.bin').write_bytes(b'')
        with self.assertRaisesRegex(ValueError, 'whisper.cpp'):
            locate.validate_model_folder(folder)

    def test_repo_folder_resolves_to_snapshot(self):
        repo = self.root / 'models--Systran--faster-whisper-large-v3'
        snapshot = make_model(repo / 'snapshots' / 'abc123')
        self.assertEqual(locate.validate_model_folder(repo), snapshot)

    def test_saved_folder_wins(self):
        saved = make_model(self.root / 'saved')
        make_model(self.root / 'cache' / 'models--Systran--faster-whisper-large-v3' / 'snapshots' / 'x')
        with patch.dict(os.environ, {'HF_HUB_CACHE': str(self.root / 'cache')}):
            self.assertEqual(locate.whisper_model(str(saved)), saved)

    def test_deleted_saved_folder_falls_back(self):
        with patch.dict(os.environ, {'HF_HUB_CACHE': str(self.root / 'nocache')}):
            self.assertIsNone(locate.whisper_model(str(self.root / 'gone')))

    def test_hf_hub_cache_env_is_honored(self):
        cache = self.root / 'elsewhere'
        snapshot = make_model(cache / 'models--Systran--faster-whisper-large-v3' / 'snapshots' / 'x')
        with patch.dict(os.environ, {'HF_HUB_CACHE': str(cache)}):
            self.assertEqual(locate.whisper_model(None), snapshot)

    def test_hf_home_env_is_honored(self):
        snapshot = make_model(self.root / 'hfhome' / 'hub' / 'models--Systran--faster-whisper-large-v3' / 'snapshots' / 'y')
        with patch.dict(os.environ, {'HF_HOME': str(self.root / 'hfhome')}):
            os.environ.pop('HF_HUB_CACHE', None)
            self.assertEqual(locate.whisper_model(None), snapshot)


def make_exe(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'MZ')
    return path


class FakeRegistry:
    """Just enough of winreg for the Uninstall walk; the second hive is unreadable."""
    HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE = 'HKCU', 'HKLM'

    def __init__(self, entries):
        self.entries = entries

    def OpenKey(self, hive, path):
        if hive == 'HKLM':
            raise OSError('access denied')
        return path if not isinstance(hive, str) or hive == 'HKCU' else hive + '\\' + path

    def EnumKey(self, key, index):
        if key.endswith('Uninstall'):
            names = list(self.entries)
            if index >= len(names):
                raise OSError('no more items')
            return names[index]
        raise OSError

    def QueryValueEx(self, key, name):
        values = self.entries[key.rsplit('\\', 1)[-1]]
        if name not in values:
            raise OSError(name)
        return values[name], 1

    def CloseKey(self, key):
        pass


class Tools(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.local = self.root / 'Local'
        env = {'LOCALAPPDATA': str(self.local), 'APPDATA': str(self.root / 'Roaming'), 'USERPROFILE': str(self.root / 'home'),
               'ProgramFiles': str(self.root / 'pf'), 'ProgramFiles(x86)': str(self.root / 'pf86')}
        for patcher in (patch.object(locate.paths, 'DATA', self.root / 'data'), patch.dict(os.environ, env),
                        patch.object(locate.shutil, 'which', return_value=None),
                        patch.object(locate, 'registry_install_folders', return_value=[])):
            patcher.start()
            self.addCleanup(patcher.stop)
        (self.root / 'data').mkdir()

    def test_override_wins_over_auto_detection(self):
        make_exe(self.local / 'Microsoft/WinGet/Links/codex.exe')
        mine = make_exe(self.root / 'C' / 'Tools' / 'codex.exe')
        self.assertEqual(locate.set_tool_path('codex', str(mine)), {'kind': 'codex', 'path': str(mine)})
        self.assertEqual(locate.tool_path('codex'), mine)

    def test_override_must_be_an_exe(self):
        text = self.root / 'codex.txt'
        text.write_text('x')
        for bad in (text, self.root, self.root / 'missing.exe'):
            with self.assertRaisesRegex(ValueError, r'^Choose the program file \(\.exe\)\.$'):
                locate.set_tool_path('codex', str(bad))

    def test_deleted_override_falls_back_and_is_reported(self):
        override = make_exe(self.root / 'Tools' / 'codex.exe')
        auto = make_exe(self.local / 'Microsoft/WinGet/Links/codex.exe')
        locate.set_tool_path('codex', str(override))
        override.unlink()
        self.assertEqual(locate.tool_path('codex'), auto)
        self.assertEqual(locate.missing_tools(), {'codex': str(override)})

    def test_clearing_override(self):
        locate.set_tool_path('claude', str(make_exe(self.root / 'Tools' / 'claude.exe')))
        self.assertEqual(locate.set_tool_path('claude', ''), {'kind': 'claude', 'path': None})
        self.assertEqual(locate.missing_tools(), {})
        self.assertIsNone(locate.tool_path('claude'))

    def test_codex_desktop_updates_are_found_without_pinning_version(self):
        binaries = self.local / 'OpenAI/Codex/bin'
        old = make_exe(binaries / 'old-version/codex.exe')
        locate.set_tool_path('codex', str(old))
        old.unlink()
        current = make_exe(binaries / 'current-version/codex.exe')
        os.utime(current, (100, 100))
        locate.set_tool_path('codex', '')  # reconnect using automatic discovery
        self.assertEqual(locate.tool_path('codex'), current)
        self.assertEqual(locate.missing_tools(), {})
        updated = make_exe(binaries / 'updated-version/codex.exe')
        os.utime(updated, (200, 200))
        self.assertEqual(locate.tool_path('codex'), updated)
        self.assertIsNone(locate.store.get_setting('codex_path') or None)

    def test_winget_links_are_found(self):
        codex = make_exe(self.local / 'Microsoft/WinGet/Links/codex.exe')
        claude = make_exe(self.local / 'Microsoft/WinGet/Links/claude.exe')
        self.assertEqual((locate.tool_path('codex'), locate.tool_path('claude')), (codex, claude))

    def test_wispr_found_through_registry(self):
        custom = self.root / 'Custom' / 'Wispr'
        app = make_exe(custom / 'app-1.2' / 'Wispr Flow.exe')
        with patch.object(locate, 'registry_install_folders', return_value=[custom]) as registry:
            self.assertEqual(locate.tool_path('wispr'), app)
        registry.assert_called_with('Wispr Flow')

    def test_registry_reader_filters_by_display_name(self):
        fake = FakeRegistry({'A': {'DisplayName': 'Wispr Flow', 'InstallLocation': 'C:\\Apps\\Wispr'},
                             'B': {'DisplayName': 'Other', 'InstallLocation': 'C:\\Apps\\Other'},
                             'C': {'DisplayName': 'Wispr Flow'}})
        with patch.object(locate, 'winreg', fake):  # REAL_REGISTRY: the unpatched function, captured at import
            self.assertEqual(REAL_REGISTRY('Wispr Flow'), [Path('C:/Apps/Wispr')])

    def test_unknown_kind_is_refused(self):
        with self.assertRaises(ValueError):
            locate.tool_path('python')
        with self.assertRaises(ValueError):
            locate.set_tool_path('python', '')


REAL_REGISTRY = locate.registry_install_folders if hasattr(locate, 'registry_install_folders') else None


if __name__ == '__main__':
    unittest.main()
