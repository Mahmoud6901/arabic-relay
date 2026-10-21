"""Desktop destination guards and duplicate handling. All UI actions are mocked."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
from arabic_relay import paths, t3
from arabic_relay import desktop_chats as bridge

URL = 'https://claude.ai/chat/12345678-1234-4234-8234-123456789abc'


class DesktopChatBoundary(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Mock()
        root.Current.NativeWindowHandle = 42
        self.root = root
        self.target = {'projectId': 'desktop:claude', 'threadId': 'synthetic-target'}
        self.item = {'provider': 'claude', 'url': 'new', 'title': 'New chat', 'root': root,
                     'created': time.time(), 'prepared': True}
        for patcher in [patch.object(paths, 'DATA', Path(temporary.name)),
                        patch.dict(os.environ, {'ARABIC_RELAY_NATIVE': '1'}),
                        patch.object(bridge, 'TARGETS', {'synthetic-target': self.item})]:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_only_exact_private_chat_addresses_are_accepted(self):
        self.assertEqual(bridge.chat_url('claude', URL), URL)
        self.assertEqual(bridge.chat_url('chatgpt', URL.replace('claude.ai/chat/', 'chatgpt.com/c/')), URL.replace('claude.ai/chat/', 'chatgpt.com/c/'))
        self.assertEqual(bridge.chat_url('claude', 'https://claude.ai/new'), 'new')
        for value in ['https://claude.ai.evil.test/chat/id', 'https://user@claude.ai/new',
                      'file:///new', 'https://claude.ai/chat/not-an-id', URL+'?q=secret', 'https://chatgpt.com/c/123']:
            self.assertIsNone(bridge.chat_url('claude', value))

    def test_unavailable_apps_have_no_selectable_destinations(self):
        with patch.object(bridge, '_roots', return_value={}):
            result = bridge.catalog()
        self.assertEqual(len(result['projects']), 2)
        self.assertTrue(all(not project['threads'] for project in result['projects']))

    def test_chatgpt_ignores_editors_from_its_browser_panel(self):
        ct = SimpleNamespace(Edit='edit')
        def editor(name):
            return Mock(Current=SimpleNamespace(ControlType='edit', Name=name, IsEnabled=True, IsOffscreen=False))
        composer = editor('Work with ChatGPT')
        nodes = [editor('Arabic and English transcript'), editor('English prompt'), composer]
        pattern = Mock(Current=SimpleNamespace(IsReadOnly=False))
        with patch.object(bridge, '_uia', return_value=(None,ct,None,None,None,None,None,None)), \
                patch.object(bridge, '_pattern', return_value=pattern):
            self.assertIs(bridge._composer(nodes, 'chatgpt'), composer)

    def test_stale_and_cross_provider_targets_cannot_be_approved(self):
        with self.assertRaises(ValueError):
            bridge.validate({**self.target, 'projectId': 'desktop:chatgpt'})
        self.item['created'] -= 1900
        with self.assertRaises(ValueError):
            bridge.validate(self.target)

    def test_desktop_only_approval_and_submission_need_no_t3_connection(self):
        with patch.object(t3, 'tool') as t3_tool, patch.object(bridge, 'prepare') as prepare, \
                patch.object(bridge, 'send', return_value={'delivery': 'desktop_submit'}) as send:
            approval = t3.prepare_send({'english': 'Reviewed prompt.', 'targets': [self.target]})['requestId']
            result = t3.send(approval)
            self.assertEqual(result['deliveries'][0]['state'], 'submitted')
            self.assertEqual(t3.send(approval), result)
        send.assert_called_once()
        prepare.assert_called_once_with({**self.target, 'title':'Claude Desktop / New chat'})
        t3_tool.assert_not_called()

    def test_uncertain_desktop_outcome_is_never_replayed(self):
        with patch.object(bridge, 'prepare'), \
                patch.object(bridge, 'send', side_effect=TimeoutError('Desktop did not respond')) as send:
            approval = t3.prepare_send({'english': 'Reviewed prompt.', 'targets': [self.target]})['requestId']
            self.assertEqual(t3.send(approval)['deliveries'][0]['state'], 'unknown')
            t3.send(approval)
        send.assert_called_once()

    def test_wrong_chat_stops_before_text_insertion(self):
        composer = Mock()
        with patch.object(bridge, '_uia', return_value=(None,)*8), \
                patch.object(bridge, '_roots', return_value={'claude': [self.root]}), \
                patch.object(bridge, '_elements', return_value=[]), \
                patch.object(bridge, '_composer', return_value=composer), \
                patch.object(bridge, '_value', return_value=''), \
                patch.object(bridge, '_document', return_value=(Mock(), URL)), \
                patch.object(bridge, '_button'), \
                patch.object(bridge, '_pattern') as action:
            with self.assertRaisesRegex(ValueError, 'did not open a new chat'):
                bridge.send(self.target, 'Reviewed prompt.')
        action.assert_not_called()

    def test_existing_desktop_draft_is_never_overwritten(self):
        with patch.object(bridge, '_uia', return_value=(None,)*8), \
                patch.object(bridge, '_roots', return_value={'claude': [self.root]}), \
                patch.object(bridge, '_elements', return_value=[]), \
                patch.object(bridge, '_composer', return_value=Mock()), \
                patch.object(bridge, '_document', return_value=(Mock(), 'new')), \
                patch.object(bridge, '_value', return_value='Unsent draft'), \
                patch.object(bridge, '_pattern') as action:
            with self.assertRaisesRegex(ValueError, 'unsent draft'):
                bridge.send(self.target, 'Reviewed prompt.')
        action.assert_not_called()

    def test_verified_text_is_submitted_once(self):
        composer, button = Mock(), Mock()
        value, invoke = Mock(), Mock()
        with patch.object(bridge, '_uia', return_value=(None,None,None,None,'value','invoke',None,None)), \
                patch.object(bridge, '_roots', return_value={'claude': [self.root]}), \
                patch.object(bridge, '_elements', return_value=[]), \
                patch.object(bridge, '_composer', return_value=composer), \
                patch.object(bridge, '_value', side_effect=['', 'Reviewed prompt.']), \
                patch.object(bridge, '_document', return_value=(Mock(), 'new')), \
                patch.object(bridge, '_button', return_value=button), \
                patch.object(bridge, '_pattern', side_effect=lambda node, pattern: value if node is composer else invoke):
            result = bridge.send(self.target, 'Reviewed prompt.')
        value.SetValue.assert_called_once_with('Reviewed prompt.')
        invoke.Invoke.assert_called_once()
        self.assertEqual(result['delivery'], 'desktop_submit')

    def test_existing_chat_approvals_are_rejected(self):
        self.item['url'] = URL
        with self.assertRaisesRegex(ValueError, 'Refresh desktop chats'):
            bridge.validate(self.target)

    def test_catalog_only_offers_new_chat_for_both_apps(self):
        with patch.object(bridge, '_roots', return_value={'claude': [self.root], 'chatgpt': [self.root]}), \
                patch.object(bridge, '_elements', return_value=[]), \
                patch.object(bridge, '_composer'), patch.object(bridge, '_button'):
            result = bridge.catalog()
        for project in result['projects']:
            self.assertEqual([thread['title'] for thread in project['threads']], ['New chat'])
            self.assertEqual(bridge.validate({'projectId': project['id'], 'threadId': project['threads'][0]['id']})['url'], 'new')

    def test_chatgpt_requires_its_home_surface_and_document(self):
        ct = SimpleNamespace(Document='document', Group='group')
        document = Mock(Current=SimpleNamespace(ControlType='document', Name='ChatGPT', IsOffscreen=False))
        home = Mock(Current=SimpleNamespace(ControlType='group', ClassName='[container-name:home-main-content]', IsOffscreen=False))
        with patch.object(bridge, '_uia', return_value=(None,ct,None,None,None,None,None,None)), \
                patch.object(bridge, '_value', return_value='app://-/index.html'):
            self.assertEqual(bridge._document('chatgpt', [document,home]), (home,'new'))
            with self.assertRaisesRegex(ValueError, 'empty New chat'):
                bridge._document('chatgpt', [document])
            document.Current.Name = 'Existing conversation'
            with self.assertRaises(ValueError):
                bridge._document('chatgpt', [document,home])

    def test_chatgpt_placeholder_is_empty_only_with_the_empty_editor_shape(self):
        c = Mock(Current=SimpleNamespace(Name='Work with ChatGPT'))
        c.FindAll.return_value = [Mock(Current=SimpleNamespace(Name='Work with ChatGPT')),
                                 Mock(Current=SimpleNamespace(Name='\n', ClassName='ProseMirror-trailingBreak'))]
        with patch.object(bridge, '_uia', return_value=(None,None,SimpleNamespace(Descendants='desc'),SimpleNamespace(TrueCondition=True),None,None,None,None)), \
                patch.object(bridge, '_value', return_value='Work with ChatGPT\n'):
            self.assertEqual(bridge._draft('chatgpt', c), '')
            c.FindAll.return_value = [Mock(Current=SimpleNamespace(Name='Work with ChatGPT\n'))]
            self.assertEqual(bridge._draft('chatgpt', c), 'Work with ChatGPT\n')

    def test_chat_change_after_insertion_prevents_submission(self):
        value, navigation = Mock(), Mock()
        composer = Mock()
        with patch.object(bridge, '_uia', return_value=(None,None,None,None,'value','invoke',None,None)), \
                patch.object(bridge, '_roots', return_value={'claude': [self.root]}), \
                patch.object(bridge, '_elements', return_value=[]), \
                patch.object(bridge, '_composer', return_value=composer), \
                patch.object(bridge, '_value', return_value=''), \
                patch.object(bridge, '_document', side_effect=[(Mock(),'new'),(Mock(),URL)]), \
                patch.object(bridge, '_button', return_value=Mock()), \
                patch.object(bridge, '_pattern', side_effect=lambda node, pattern: value if node is composer else navigation):
            with self.assertRaisesRegex(ValueError, 'chat changed'):
                bridge.send(self.target, 'Reviewed prompt.')
        navigation.Invoke.assert_not_called()
        value.SetValue.assert_called_once_with('Reviewed prompt.')

    def test_approval_opens_new_chat_without_inserting_or_sending_text(self):
        action = Mock()
        with patch.object(bridge, '_uia', return_value=(None,)*8), \
                patch.object(bridge, '_roots', return_value={'claude':[self.root]}), \
                patch.object(bridge, '_elements', return_value=[]), \
                patch.object(bridge, '_composer'), patch.object(bridge, '_value', return_value=''), \
                patch.object(bridge, '_button'), patch.object(bridge, '_document', return_value=(Mock(), 'new')), patch.object(bridge, '_pattern', return_value=action):
            bridge.prepare(self.target)
        action.Invoke.assert_called_once()
        action.SetValue.assert_not_called()
        self.assertTrue(self.item['prepared'])

    def test_approval_preserves_existing_draft(self):
        with patch.object(bridge, '_uia', return_value=(None,)*8), \
                patch.object(bridge, '_roots', return_value={'claude':[self.root]}), \
                patch.object(bridge, '_elements', return_value=[]), \
                patch.object(bridge, '_composer'), patch.object(bridge, '_value', return_value='Existing draft'), \
                patch.object(bridge, '_pattern') as action:
            with self.assertRaisesRegex(ValueError,'unsent draft'):
                bridge.prepare(self.target)
        action.assert_not_called()
        self.assertFalse(self.item['prepared'])

    def test_send_requires_prepared_new_chat(self):
        self.item['prepared'] = False
        with self.assertRaisesRegex(ValueError, 'not ready'):
            bridge.send(self.target, 'Reviewed prompt.')


if __name__ == '__main__':
    unittest.main()
