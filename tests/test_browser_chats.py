"""Real loopback HTTP pairing plus single-use delivery, without external messages."""
import http.client
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from arabic_relay import browser_chats as bridge, paths, server, t3

ORIGIN = 'chrome-extension://' + 'a' * 32
URL = 'https://chatgpt.com/c/12345678-1234-4234-8234-123456789abc'


class BrowserChats(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        for p in [patch.object(paths, 'DATA', Path(temp.name)), patch.object(bridge, 'TABS', {}),
                  patch.object(bridge, 'JOBS', {}), patch.object(bridge, 'PAIR', None)]:
            p.start()
            self.addCleanup(p.stop)

    def pair(self):
        code = bridge.start_pairing()['code']
        return bridge.pair(ORIGIN, code)['token']

    def snapshot(self, url=URL, offline=False):
        return {'tabs': [{'tabId': 42, 'chats': [{'url': url, 'title': 'Existing conversation', 'offline': offline}]}]}

    def target(self):
        return {'projectId': bridge.PREFIX+'chatgpt', 'threadId': bridge.address(URL)['id']}

    def test_pairing_is_one_time_origin_bound_and_revocable(self):
        code = bridge.start_pairing()['code']
        for origin in ['https://chatgpt.com', 'null', 'chrome-extension://'+'x'*32]:
            with self.assertRaises(ValueError):
                bridge.pair(origin, code)
        token = bridge.pair(ORIGIN, code)['token']
        self.assertTrue(bridge.authorize(ORIGIN, token))
        self.assertFalse(bridge.authorize(ORIGIN.replace('a', 'b'), token))
        self.assertFalse(bridge.authorize(ORIGIN, 'wrong'))
        with self.assertRaises(ValueError):
            bridge.pair(ORIGIN, code)
        bridge.disconnect()
        self.assertFalse(bridge.authorize(ORIGIN, token))

    def test_pairing_code_expires(self):
        code = bridge.start_pairing()['code']
        bridge.PAIR['expires'] = 0
        with self.assertRaises(ValueError):
            bridge.pair(ORIGIN, code)

    def test_exact_routes_and_stable_conversation_identity(self):
        project = URL.replace('/c/', '/g/g-p-project/c/')
        self.assertEqual(bridge.address(URL)['id'], bridge.address(project)['id'])
        for route in ['https://claude.ai/chat/'+URL.rsplit('/',1)[1],
                      'https://claude.ai/code/session_01ExampleCodeSessionABC1',
                      'https://claude.ai/cowork/cse_01ExampleWorkSessionABC1']:
            self.assertIsNotNone(bridge.address(route))
        for route in [URL+'?share=1', URL+'#x', URL.replace('chatgpt.com','chatgpt.com.evil.test'),
                      URL.replace('https://','https://user@'), URL.replace('/c/','/share/'),
                      'https://claude.ai/new', 'file:///C:/private', 'https://[', None]:
            self.assertIsNone(bridge.address(route), route)

    def test_snapshot_removes_closed_tabs_and_expires_without_heartbeat(self):
        self.pair()
        bridge.sync(self.snapshot())
        self.assertEqual(bridge.catalog()['projects'][0]['threads'][0]['title'], 'Existing conversation')
        bridge.validate(self.target())
        with self.assertRaises(ValueError):
            bridge.validate({**self.target(), 'projectId':bridge.PREFIX+'claude'})
        with patch.object(bridge.time, 'time', return_value=time.time()+100):
            self.assertFalse(bridge.catalog()['projects'][0]['threads'])
            with self.assertRaises(ValueError):
                bridge.validate(self.target())
        bridge.sync({'tabs': []})
        self.assertFalse(bridge.catalog()['projects'][0]['threads'])

    def test_offline_code_sessions_are_visible_but_not_sendable(self):
        self.pair()
        url = 'https://claude.ai/code/session_01ExampleCodeSessionABC1'
        bridge.sync(self.snapshot(url, True))
        thread = bridge.catalog()['projects'][1]['threads'][0]
        self.assertTrue(thread['disabled'])
        with self.assertRaisesRegex(ValueError, 'offline'):
            bridge.validate({'projectId':bridge.PREFIX+'claude','threadId':thread['id']})

    def test_one_send_leases_once_and_requires_exact_url_receipt(self):
        self.pair()
        bridge.sync(self.snapshot())
        result = []
        thread = threading.Thread(target=lambda: result.append(bridge.send(self.target(), 'A reviewed prompt.')))
        thread.start()
        for _ in range(100):
            response = bridge.sync(self.snapshot())
            if response['commands']:
                break
            time.sleep(.005)
        command = response['commands'][0]
        self.assertEqual(command['url'], URL)
        self.assertEqual(command['text'], 'A reviewed prompt.')
        self.assertEqual(bridge.sync(self.snapshot())['commands'], [])
        bridge.complete({'id':command['id'], 'url':URL, 'submitted':True})
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result[0]['delivery'], 'browser_submit')
        self.assertFalse(bridge.JOBS)
        with self.assertRaises(ValueError):
            bridge.complete({'id':command['id'], 'url':URL, 'submitted':True})

    def test_wrong_url_and_late_receipts_never_count_as_submitted(self):
        job = {'url':URL, 'claimed':True, 'expires':time.time()+60, 'event':threading.Event()}
        bridge.JOBS['test'] = job
        bridge.complete({'id':'test','url':URL+'-other','submitted':True})
        self.assertIn('error',job['result'])
        job['expires'] = 0
        with self.assertRaises(ValueError):
            bridge.complete({'id':'test','url':URL,'submitted':True})

    def test_existing_dispatch_guard_keeps_browser_retries_at_most_once(self):
        self.pair()
        bridge.sync(self.snapshot())
        with patch.object(t3, 'tool') as tool, patch.object(bridge, 'send', return_value={'delivery':'browser_submit','messageId':'test'}) as send:
            request = t3.prepare_send({'english':'Reviewed.', 'targets':[self.target()]})['requestId']
            result = t3.send(request)
            self.assertEqual(result['deliveries'][0]['state'], 'submitted')
            self.assertEqual(t3.send(request), result)
            send.assert_called_once()
            tool.assert_not_called()

    def test_http_bridge_does_not_expose_relay_api_or_accept_web_origins(self):
        httpd = server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        threading.Thread(target=httpd.serve_forever,daemon=True).start()
        port = httpd.server_address[1]
        def post(path, body, origin=ORIGIN, token=''):
            c = http.client.HTTPConnection('127.0.0.1',port)
            c.request('POST',path,json.dumps(body),{'Origin':origin,'Authorization':'Bearer '+token,'Content-Type':'application/json'})
            r=c.getresponse();data=json.loads(r.read());headers=dict(r.getheaders());c.close()
            return r.status,data,headers
        with patch.object(server,'ORIGIN',f'http://127.0.0.1:{port}'):
            code=bridge.start_pairing()['code']
            self.assertEqual(post('/bridge/pair',{'code':code},'https://claude.ai')[0],403)
            status,data,headers=post('/bridge/pair',{'code':code})
            self.assertEqual(status,200)
            self.assertEqual(headers['Access-Control-Allow-Origin'],ORIGIN)
            token=data['token']
            self.assertEqual(post('/bridge/sync',self.snapshot())[0],403)
            self.assertEqual(post('/bridge/sync',self.snapshot(),token=token)[0],200)
            status,_,headers=post('/api/send',{'english':'must not send'},token=token)
            self.assertEqual(status,403)
            self.assertNotIn('Access-Control-Allow-Origin',headers)


if __name__ == '__main__':
    unittest.main()
