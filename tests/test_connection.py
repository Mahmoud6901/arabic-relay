"""MCP uses the negotiated protocol and retains no half-initialized session."""
import unittest
from unittest.mock import patch
from arabic_relay import t3


class ConnectionProtocol(unittest.TestCase):
    def setUp(self):
        for name, value in (('MCP_SESSION', None), ('MCP_PROTOCOL', None), ('access_token', lambda: 'test-token')):
            mocked = patch.object(t3, name, value)
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_negotiated_protocol_is_used_for_notification_and_tools(self):
        replies = [({'result': {'protocolVersion': '2025-11-25'}}, {'Mcp-Session-Id': 'session'}),
                   (None, {}), ({'result': {'tools': []}}, {})]
        with patch.object(t3, 'request_json', side_effect=replies) as request:
            self.assertEqual(t3.rpc('tools/list'), {'tools': []})
        initialize, notification, tools = request.call_args_list
        self.assertEqual(initialize.args[1]['params']['protocolVersion'], '2025-06-18')
        for call in (notification, tools):
            self.assertEqual(call.args[2]['MCP-Protocol-Version'], '2025-11-25')
            self.assertEqual(call.args[2]['Mcp-Session-Id'], 'session')

    def test_failed_notification_does_not_cache_session_or_retry(self):
        replies = [({'result': {'protocolVersion': '2025-06-18'}}, {'Mcp-Session-Id': 'session'}),
                   ValueError('rejected')]
        with patch.object(t3, 'request_json', side_effect=replies) as request:
            with self.assertRaises(ValueError):
                t3.rpc('tools/list')
        self.assertEqual(request.call_count, 2)
        self.assertIsNone(t3.MCP_SESSION)
        self.assertIsNone(t3.MCP_PROTOCOL)

    def test_invalid_negotiation_cannot_be_sent_in_headers(self):
        with patch.object(t3, 'request_json', return_value=({'result': {'protocolVersion': 'bad\r\nvalue'}}, {})) as request:
            with self.assertRaises(ValueError):
                t3.rpc('tools/list')
        request.assert_called_once()
        self.assertIsNone(t3.MCP_SESSION)

    def test_catalog_omits_empty_cursors_but_preserves_numeric_pages(self):
        pages = [
            {'projects': [{'id': 'project', 'title': 'Project', 'workspaceRoot': 'root'}], 'nextCursor': 1},
            {'projects': [], 'nextCursor': None},
            {'threads': [], 'nextCursor': 0},
            {'threads': [], 'nextCursor': None},
        ]
        with patch.object(t3, 'tool', side_effect=pages) as tool:
            self.assertEqual(len(t3.catalog()), 1)
        calls = tool.call_args_list
        self.assertNotIn('cursor', calls[0].args[1])
        self.assertEqual(calls[1].args[1]['cursor'], 1)
        self.assertNotIn('cursor', calls[2].args[1])
        self.assertEqual(calls[3].args[1]['cursor'], 0)


if __name__ == '__main__':
    unittest.main()
