"""The per-user data folder and the runtime T3 catalog snapshot."""
import importlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from arabic_relay import server, paths, store, t3


class DataFolder(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        catalog = patch.object(server.direct_sessions, 'catalog', return_value={'projects': [], 'apps': []})
        catalog.start()
        self.addCleanup(catalog.stop)

    def tearDown(self):
        importlib.reload(paths)
        self.temp.cleanup()

    def test_env_override_creates_folder(self):
        target = Path(self.temp.name) / 'nested' / 'data'
        with patch.dict(os.environ, {'ARABIC_RELAY_DATA_DIR': str(target)}):
            module = importlib.reload(paths)
        self.assertEqual(module.DATA, target)
        self.assertTrue(target.is_dir())

    def test_default_is_localappdata(self):
        with patch.dict(os.environ, {'LOCALAPPDATA': self.temp.name}, clear=True):
            module = importlib.reload(paths)
        self.assertEqual(module.DATA, Path(self.temp.name) / 'Arabic Relay')

    def test_vocabulary_lives_in_data_folder(self):
        with patch.object(paths, 'DATA', Path(self.temp.name)):
            store.learn_vocabulary('React deadline')
        self.assertTrue((Path(self.temp.name) / 'vocabulary.sqlite3').is_file())

    def test_offline_catalog_without_snapshot_is_empty(self):
        with patch.object(paths, 'DATA', Path(self.temp.name)), patch.object(t3, 'load_auth', return_value=None):
            body = server.catalog_response()
        self.assertFalse(body['live'])
        self.assertTrue(all(p['id'].startswith('desktop:') for p in body['projects']))

    def test_live_catalog_writes_snapshot(self):
        live = [{'id': 'p1', 'title': 'Demo', 'workspaceRoot': 'X', 'threads': []}]
        with patch.object(paths, 'DATA', Path(self.temp.name)), patch.object(t3, 'load_auth', return_value={'a': 1}), \
                patch.object(t3, 'catalog', return_value=live):
            server.catalog_response()
        self.assertEqual(json.loads((Path(self.temp.name) / 'catalog.json').read_text(encoding='utf-8')), live)


if __name__ == '__main__':
    unittest.main()
