import base64
import json
import secrets
import socket
import tempfile
import unittest
from pathlib import Path
from adapter import EVENT, MockCallback, MockFeishu, CallbackVerificationError, encoded, verify
from cryptography.fernet import Fernet
from deploy.store import Store
from deploy.tunnel import Control, OWNER, LocalAuthorization, ApprovedCallback, TunnelAdapter, TunnelHTTP
from deploy.network import SafeHTTPS

class PublicMock(MockCallback):
    def __init__(self, hosts): super().__init__()
    def addresses(self, host): return ['8.8.8.8']

class TunnelTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.now=1000000000
        self.key=Fernet.generate_key()
        self.control_store=Store.encrypted(Path(self.tmp.name)/'control.sqlite3',self.key)
        self.addCleanup(self.control_store.close)
        self.control=Control(self.control_store,clock=lambda:self.now)
        self.local=secrets.token_hex(32)
        self.message={'chat_type':'p2p','sender_type':'user','sender_id':'u1','chat_id':'c1','message_type':'text','message_id':'m1','text':'never store pairing message'}
        self.url='https://callback.example.com/events'
        self.secret='whsec_'+base64.b64encode(secrets.token_bytes(32)).decode()
        self.params={'name':EVENT,'arguments':{'chat_id':'c1'},'delivery':{'mode':'webhook','url':self.url,'secret':self.secret}}
    def audience(self): self.control.update(lambda state:state.update(personal_audience_confirmed=True))
    def pair(self):
        self.audience(); self.control.observe_ws(True); self.control.candidate(self.message)
        identifier=next(iter(self.control.snapshot()['candidates']))
        self.control.accept_pair(identifier)
    def adapter(self):
        store=Store.encrypted(Path(self.tmp.name)/'adapter.sqlite3',self.key);self.addCleanup(store.close)
        return TunnelAdapter(store,owner=OWNER,user='u1',chat='c1',callback=ApprovedCallback(self.control,PublicMock),feishu=MockFeishu(),clock=lambda:self.now)
    def request(self, app, params=None, secret=None):
        method='events/subscribe'; params={**(params or self.params),'_meta':{'io.modelcontextprotocol/protocolVersion':'2026-07-28','io.modelcontextprotocol/clientCapabilities':{}}}
        headers={'X-Feishu-Local-Auth':self.local if secret is None else secret,'Content-Type':'application/json','Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2026-07-28','Mcp-Method':method}
        return app.handle('POST','/mcp',headers,encoded({'jsonrpc':'2.0','id':1,'method':method,'params':params}))
    def test_optin_and_authentication_gate(self):
        auth=LocalAuthorization(self.local,self.control)
        with self.assertRaises(PermissionError):auth(self.local)
        self.audience()
        self.assertEqual(auth(self.local),OWNER)
        for value in ['',self.local+'x','Bearer '+self.local]:
            with self.assertRaises(PermissionError):auth(value)
        self.control.update(lambda state:state.update(personal_audience_confirmed=False))
        with self.assertRaises(PermissionError):auth(self.local)
    def test_pairing_only_after_actual_open_and_no_auto_trust(self):
        self.audience();self.control.observe_ws(False)
        self.assertFalse(self.control.candidate(self.message))
        self.assertNotIn('pairing_started',self.control.snapshot())
        self.control.observe_ws(True)
        self.assertTrue(self.control.candidate(self.message))
        self.assertNotIn('binding',self.control.snapshot())
        self.assertNotIn('never store pairing message',json.dumps(self.control.snapshot()))
        identifier=next(iter(self.control.snapshot()['candidates']))
        self.control.accept_pair(identifier)
        self.assertEqual(self.control.snapshot()['binding'],[OWNER,'u1','c1'])
        with self.assertRaises(PermissionError):self.control.accept_pair(identifier)
    def test_window_one_shot_survives_reconnect_and_restart(self):
        self.audience();self.control.observe_ws(True)
        deadline=self.control.snapshot()['pairing_deadline']
        self.now+=30;self.control.observe_ws(False);self.control.observe_ws(True)
        self.assertEqual(self.control.snapshot()['pairing_deadline'],deadline)
        second=Store.encrypted(Path(self.tmp.name)/'control.sqlite3',self.key);self.addCleanup(second.close)
        restored=Control(second,clock=lambda:self.now)
        self.now=deadline;restored.observe_ws(True)
        self.assertFalse(restored.candidate(self.message))
        self.assertEqual(restored.snapshot()['pairing_deadline'],deadline)
    def test_pairing_rejects_groups_bots_stale_and_expired(self):
        self.audience();self.control.observe_ws(True)
        for field,value in [('chat_type','group'),('sender_type','app'),('message_type','image')]:
            self.assertFalse(self.control.candidate({**self.message,field:value}))
        self.control.candidate(self.message)
        identifier=next(iter(self.control.snapshot()['candidates']))
        self.now+=6
        with self.assertRaises(ValueError):self.control.accept_pair(identifier)
        self.now+=600;self.control.observe_ws(True)
        with self.assertRaises(ValueError):self.control.accept_pair(identifier)
    def test_callback_queue_requires_authenticated_subscribe(self):
        self.pair();adapter=self.adapter()
        app=TunnelHTTP(adapter,LocalAuthorization(self.local,self.control),'http://127.0.0.1:8765/mcp','',[])
        self.assertEqual(self.request(app,secret='wrong')[0],403)
        self.assertFalse(self.control.snapshot().get('callback_pending'))
        status,_,body=self.request(app)
        self.assertEqual(status,400)
        self.assertEqual(json.loads(body)['error']['data']['reason'],'local_approval_pending')
        self.assertEqual(app.telemetry['subscribe_last_rpc_code'],-32015)
        self.assertEqual(app.telemetry['subscribe_last_reason'],'local_approval_pending')
        self.assertEqual(adapter.callback.diagnostic_snapshot()['challenge_attempts'],0)
        self.assertFalse(adapter.subscriptions)
        self.assertEqual(next(iter(self.control.snapshot()['callback_pending'].values()))['url'],self.url)
        self.assertNotIn(self.secret,json.dumps(self.control.snapshot()))
        self.assertEqual(app.handle('GET','/.well-known/oauth-protected-resource',{},b'')[0],404)
    def test_callback_approval_exact_url_and_still_signed_challenge(self):
        self.pair();adapter=self.adapter()
        with self.assertRaises(CallbackVerificationError):adapter.subscribe_authorized(OWNER,self.params,self.local)
        identifier=next(iter(self.control.snapshot()['callback_pending']))
        self.control.approve_callback(identifier)
        result=adapter.subscribe_authorized(OWNER,self.params,self.local)
        self.assertEqual(adapter.callback.diagnostic_snapshot(),{'challenge_attempts':1,'challenge_last_http_status':200})
        self.assertNotIn('authorization',adapter.subscriptions[result['id']])
        altered={**self.params,'delivery':{**self.params['delivery'],'url':self.url+'?different=1'}}
        with self.assertRaises(CallbackVerificationError):adapter.subscribe_authorized(OWNER,altered)
        class Bad(PublicMock):
            def __init__(self,hosts):super().__init__(hosts);self.bad_challenge=True
        adapter.callback=ApprovedCallback(self.control,Bad)
        with self.assertRaises(CallbackVerificationError):adapter.subscribe_authorized(OWNER,self.params)
    def test_callback_private_dns_rejected_before_pending(self):
        self.pair()
        def factory(hosts):
            return SafeHTTPS(hosts,resolver=lambda *a,**kw:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))])
        callback=ApprovedCallback(self.control,factory)
        with self.assertRaises(ValueError):callback.validate_url('https://public-looking.example/events')
        self.assertFalse(self.control.snapshot().get('callback_pending'))
        for url in ['http://callback.example.com/a','https://u:p@callback.example.com/a','https://callback.example.com:444/a','https://callback.example.com/a#secret']:
            with self.assertRaises(ValueError):callback.validate_url(url)
    def test_pending_approval_expires(self):
        self.pair();self.control.callback(self.url)
        identifier=next(iter(self.control.snapshot()['callback_pending']))
        self.now+=600
        with self.assertRaises(ValueError):self.control.approve_callback(identifier)
    def test_renewal_restart_queue_and_reply_binding(self):
        self.pair();adapter=self.adapter()
        self.control.callback(self.url);self.control.approve_callback(next(iter(self.control.snapshot()['callback_pending'])))
        first=adapter.subscribe_authorized(OWNER,self.params,self.local)
        adapter.inject(OWNER,self.message)
        second_store=Store.encrypted(Path(self.tmp.name)/'adapter.sqlite3',self.key);self.addCleanup(second_store.close)
        restored=TunnelAdapter(second_store,owner=OWNER,user='u1',chat='c1',callback=ApprovedCallback(self.control,PublicMock),feishu=MockFeishu(),clock=lambda:self.now)
        self.assertEqual(len(restored.outbox),1)
        self.now+=1
        renewed=restored.subscribe_authorized(OWNER,self.params,self.local)
        self.assertEqual(first['id'],renewed['id'])
        self.assertNotEqual(first['refreshBefore'],renewed['refreshBefore'])
        restored.drain();self.assertFalse(restored.outbox)
        result=restored.send(OWNER,{'chat_id':'c1','message_id':'m1','text':'reply','idempotency_key':'once'},True)
        self.assertIn('message_id',result)
        with self.assertRaises(PermissionError):restored.send(OWNER,{'chat_id':'wrong','message_id':'m1','text':'reply','idempotency_key':'other'},True)
        restored.unsubscribe(OWNER,self.params)
        restored.inject(OWNER,{**self.message,'message_id':'m2'})
        self.assertFalse(restored.outbox)
    def test_control_is_encrypted_and_no_message_or_secret_telemetry(self):
        self.pair();self.control.callback(self.url)
        raw=(Path(self.tmp.name)/'control.sqlite3').read_bytes()
        self.assertNotIn(self.url.encode(),raw)
        self.assertNotIn(self.local.encode(),raw)
        self.assertNotIn(self.message['text'].encode(),raw)
        self.assertNotIn(self.secret.encode(),raw)
    def test_revoke_cancels_pending_delivery(self):
        self.pair();adapter=self.adapter()
        self.control.callback(self.url);self.control.approve_callback(next(iter(self.control.snapshot()['callback_pending'])))
        adapter.subscribe_authorized(OWNER,self.params)
        adapter.inject(OWNER,self.message)
        def check(_):
            if not self.control.audience():raise PermissionError()
            return OWNER
        adapter.check_authorization=check
        self.control.update(lambda state:state.update(personal_audience_confirmed=False))
        adapter.drain()
        self.assertFalse(adapter.outbox);self.assertFalse(adapter.subscriptions)
    def test_control_updates_from_two_handles_do_not_lose_approvals(self):
        self.pair()
        second=Store.encrypted(Path(self.tmp.name)/'control.sqlite3',self.key);self.addCleanup(second.close)
        other=Control(second,clock=lambda:self.now)
        self.control.callback(self.url)
        other.observe_ws(True)
        identifier=next(iter(other.snapshot()['callback_pending']))
        other.approve_callback(identifier)
        self.control.observe_ws(True)
        self.assertIn(self.url,self.control.snapshot()['callback_approved'])
        self.assertEqual(self.control.snapshot()['binding'],[OWNER,'u1','c1'])
    def test_bridge_does_not_persist_pre_pair_or_revoked_messages(self):
        from deploy.tunnel_runtime import Bridge
        self.audience();self.control.observe_ws(True)
        holder={};bridge=Bridge(self.control,holder)
        bridge.inject(OWNER,self.message)
        self.assertNotIn('binding',self.control.snapshot())
        identifier=next(iter(self.control.snapshot()['candidates']))
        self.control.accept_pair(identifier)
        adapter=self.adapter();holder['adapter']=adapter
        bridge.inject(OWNER,self.message)
        self.assertIn('m1',adapter.messages)
        self.control.update(lambda state:state.update(personal_audience_confirmed=False))
        bridge.inject(OWNER,{**self.message,'message_id':'m2'})
        self.assertNotIn('m2',adapter.messages)
    def test_runtime_refuses_unconfirmed_audience_before_sockets_or_sdk(self):
        from deploy.tunnel_runtime import run
        from unittest.mock import patch
        credentials=Path(self.tmp.name)/'credentials';credentials.mkdir(mode=0o700)
        for name,value in [('state_key',self.key.decode()),('local_mcp_auth',self.local),('feishu_app_secret','test-only-not-live')]:
            p=credentials/name;p.write_text(value);p.chmod(0o600)
        config={'auth_mode':'tunnel-single-user','control_path':str(Path(self.tmp.name)/'unconfirmed.sqlite3'),'state_path':str(Path(self.tmp.name)/'unused.sqlite3'),'feishu_app_id':'test-only'}
        with patch('deploy.tunnel_runtime.make_server') as server, patch('deploy.tunnel_runtime.FeishuSDK.connect_client') as sdk:
            with self.assertRaises(PermissionError):run(config,str(credentials))
            server.assert_not_called();sdk.assert_not_called()
