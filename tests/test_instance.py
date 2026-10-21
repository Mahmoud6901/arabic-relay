"""One window per user, focus on a second launch, and a free port when the preferred one is busy."""
import json
from pathlib import Path
import socket
import tempfile
import unittest
import uuid
from unittest.mock import patch
from arabic_relay import desktop, paths, server


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


class Port(unittest.TestCase):
    def setUp(self):
        saved = server.PORT, server.ORIGIN

        def restore():
            server.PORT, server.ORIGIN = saved
        self.addCleanup(restore)

    def serve(self, port):
        httpd = server.Server(('127.0.0.1', port), server.Handler)
        self.addCleanup(httpd.server_close)
        return httpd

    def test_second_server_on_the_same_port_fails(self):
        first = self.serve(0)
        with self.assertRaises(OSError):
            self.serve(first.server_address[1])

    def test_bind_prefers_the_requested_port(self):
        port = free_port()
        httpd = server.bind(port)
        self.addCleanup(httpd.server_close)
        self.assertEqual(httpd.server_address[1], port)
        self.assertEqual((server.PORT, server.ORIGIN), (port, f'http://127.0.0.1:{port}'))

    def test_busy_port_falls_back(self):
        busy = self.serve(0).server_address[1]
        httpd = server.bind(busy)
        self.addCleanup(httpd.server_close)
        self.assertNotEqual(httpd.server_address[1], busy)
        self.assertEqual(server.ORIGIN, f'http://127.0.0.1:{httpd.server_address[1]}')
        with self.assertRaises(OSError):
            server.bind(busy, fallback=False)


class Instance(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        patcher = patch.object(paths, 'DATA', Path(temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_mutex_allows_one_instance(self):
        name = f'Local\\ArabicRelayTest-{uuid.uuid4()}'
        saved = desktop.MUTEX
        self.addCleanup(setattr, desktop, 'MUTEX', saved)
        self.assertTrue(desktop.single_instance(name))
        first = desktop.MUTEX
        self.assertFalse(desktop.single_instance(name))
        desktop.MUTEX = first  # the first instance's handle stays alive

    def test_focus_tolerates_missing_or_bad_instance_file(self):
        target = paths.DATA / 'instance.json'
        self.assertFalse(desktop.focus_existing())
        for text in ('{bad json', '{"pid": "x"}', json.dumps({'pid': 4_000_000_000, 'port': 8791}), '[]'):
            target.write_text(text, encoding='utf-8')
            self.assertFalse(desktop.focus_existing(), text)


if __name__ == '__main__':
    unittest.main()
