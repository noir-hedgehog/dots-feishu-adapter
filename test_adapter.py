import base64
import secrets
import json
import unittest
from adapter import Adapter, EVENT, encoded, signed_headers, verify

class Tests(unittest.TestCase):
    def test_rotation_overlap(self):
        self.a.subscribe('mock-owner',self.p)
        old=self.secret
        new='whsec_'+base64.b64encode(secrets.token_bytes(32)).decode()
        self.p['delivery']['secret']=new
        self.a.subscribe('mock-owner',self.p)
        self.a.inject('mock-owner',self.message); self.a.drain()
        body,headers=self.a.callback.calls[-1]
        self.assertTrue(verify(old,body,headers,self.now))
        self.assertTrue(verify(new,body,headers,self.now))
    def setUp(self):
        self.now=1000000000
        self.a=Adapter(clock=lambda:self.now)
        self.secret='whsec_'+base64.b64encode(secrets.token_bytes(32)).decode()
        self.p={'name':EVENT,'arguments':{'chat_id':'mock-dm'},'delivery':{'mode':'webhook','url':'https://callback.example.com/events','secret':self.secret},'cursor':None}
        self.message={'chat_type':'p2p','sender_type':'user','sender_id':'mock-user','chat_id':'mock-dm','message_type':'text','message_id':'m1','text':'你好'}
        self.send={'chat_id':'mock-dm','message_id':'m1','text':'reply','idempotency_key':'r1'}
    def subscribe(self): return self.a.subscribe('mock-owner',self.p)
    def test_discovery(self):
        self.assertEqual(self.a.rpc('mock-owner','server/discover',{})['supportedVersions'],['2026-07-28'])
        self.assertEqual(self.a.event_list()['events'][0]['name'],EVENT)
        self.assertEqual(len(self.a.tools()['tools']),3)
    def test_subscription_idempotency_refresh_and_no_secret_leak(self):
        first=self.subscribe(); self.now+=1; second=self.subscribe()
        self.assertEqual(first['id'],second['id']); self.assertEqual(len(self.a.subscriptions),1)
        self.assertNotIn(self.secret,json.dumps(second)); self.assertNotEqual(first['refreshBefore'],second['refreshBefore'])
    def test_callback_verification_failure(self):
        self.a.callback.bad_challenge=True
        with self.assertRaises(ValueError): self.subscribe()
        self.assertFalse(self.a.subscriptions)
    def test_bad_key(self):
        self.p['delivery']['secret']='whsec_bad'
        with self.assertRaises(ValueError): self.subscribe()
    def test_callback_destination_restriction(self):
        for url in ['http://callback.example.com/a','https://127.0.0.1/a','https://evil.example/a','https://u:p@callback.example.com/a']:
            self.p['delivery']['url']=url
            with self.assertRaises(ValueError): self.subscribe()
    def test_authorization(self):
        with self.assertRaises(PermissionError): self.a.subscribe('stranger',self.p)
        with self.assertRaises(PermissionError): self.a.inject('stranger',self.message)
        with self.assertRaises(PermissionError): self.a.send('stranger',self.send,True)
    def test_group_bot_foreign_user_rejected(self):
        for field,value in [('chat_type','group'),('sender_type','app'),('sender_id','foreign'),('chat_id','group'),('message_type','image')]:
            msg={**self.message,field:value}
            with self.assertRaises(PermissionError): self.a.inject('mock-owner',msg)
        self.assertFalse(self.a.messages)
    def test_filters_rejected(self):
        self.p['arguments']={'chat_id':'any-group'}
        with self.assertRaises(ValueError): self.subscribe()
    def test_delivery_signature_correlation_and_dedup(self):
        sub=self.subscribe(); self.a.inject('mock-owner',self.message)
        self.assertTrue(self.a.inject('mock-owner',self.message)['duplicate'])
        self.assertEqual(self.a.drain()['pending'],0)
        body,headers=self.a.callback.calls[-1]
        self.assertTrue(verify(self.secret,body,headers,self.now))
        event=json.loads(body)
        self.assertEqual(event['data']['message_id'],'m1')
        self.assertEqual(headers['X-MCP-Subscription-Id'],sub['id'])
        self.assertEqual(headers['webhook-id'],event['eventId'])
    def test_tamper_and_replay(self):
        body=encoded({'x':1}); headers=signed_headers(self.secret,'id',body,self.now,'s')
        self.assertFalse(verify(self.secret,body+b' ',headers,self.now))
        self.assertFalse(verify(self.secret,body,headers,self.now+301))
    def test_retry_preserves_id_fresh_signature(self):
        self.subscribe(); self.a.inject('mock-owner',self.message); self.a.callback.statuses=[503,200]
        self.assertEqual(self.a.drain()['pending'],1)
        first=self.a.callback.calls[-1]
        self.a.drain(); self.assertEqual(self.a.callback.calls[-1],first)
        self.now+=2; self.a.drain(); second=self.a.callback.calls[-1]
        self.assertEqual(first[0],second[0]); self.assertEqual(first[1]['webhook-id'],second[1]['webhook-id'])
        self.assertNotEqual(first[1]['webhook-signature'],second[1]['webhook-signature'])
    def test_retry_bounded_and_terminal_statuses(self):
        for code in [410,413,400]:
            self.subscribe(); self.a.inject('mock-owner',{**self.message,'message_id':str(code)})
            self.a.callback.statuses=[code]; self.assertEqual(self.a.drain()['pending'],0)
        self.subscribe(); self.a.inject('mock-owner',{**self.message,'message_id':'retry'})
        self.a.callback.statuses=[503]*3
        for _ in range(3): self.a.drain(); self.now+=10
        self.assertFalse(self.a.outbox)
    def test_expiration_and_cancel(self):
        self.p['ttlMs']=1000; self.subscribe(); self.a.inject('mock-owner',self.message)
        self.now+=2; self.a.drain(); self.assertFalse(self.a.outbox)
        self.subscribe(); self.a.unsubscribe('mock-owner',self.p); self.a.unsubscribe('mock-owner',self.p)
        self.assertFalse(self.a.subscriptions)
    def test_reply_association_and_idempotency(self):
        with self.assertRaises(ValueError): self.a.send('mock-owner',self.send,True)
        self.a.inject('mock-owner',self.message)
        r=self.a.send('mock-owner',self.send,True)
        self.assertEqual(r,self.a.send('mock-owner',self.send,True)); self.assertEqual(len(self.a.feishu.calls),1)
        self.assertEqual(self.a.feishu.calls[0]['reply_to'],'m1')
        with self.assertRaises(ValueError): self.a.send('mock-owner',{**self.send,'text':'different'},True)
    def test_send_transport_retry(self):
        self.a.inject('mock-owner',self.message); self.a.feishu.failures=1
        with self.assertRaises(TimeoutError): self.a.send('mock-owner',self.send,True)
        r=self.a.send('mock-owner',self.send,True)
        self.assertTrue(r['message_id']); self.assertEqual(len(self.a.feishu.calls),1)
    def test_send_scope_and_separate_tool(self):
        with self.assertRaises(PermissionError): self.a.send('mock-owner',{**self.send,'chat_id':'other'},True)
        args={k:v for k,v in self.send.items() if k!='message_id'}
        result=self.a.rpc('mock-owner','tools/call',{'name':'send_message','arguments':args})
        self.assertFalse(result['isError']); self.assertIsNone(self.a.feishu.calls[0]['reply_to'])
    def test_tool_schema_rejects_extra_fields(self):
        with self.assertRaises(ValueError): self.a.send('mock-owner',{**self.send,'secret':'unused'},True)

if __name__=='__main__': unittest.main()
