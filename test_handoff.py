import base64
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from cryptography.fernet import Fernet
from adapter import MockCallback,CallbackVerificationError
from deploy.handoff import HandoffCallback,approve_live,DeadlineHTTPS
from deploy.store import Store
from deploy.tunnel import Control,TunnelAdapter,OWNER

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store.encrypted(Path(self.tmp.name)/'control.db',Fernet.generate_key());self.addCleanup(self.store.close)
        self.control=Control(self.store)
        self.control.update(lambda s:s.update(personal_audience_confirmed=True,binding=[OWNER,'u','c']))
        self.url='https://callback.example.com/events'
        self.params={'name':'message.created','arguments':{'chat_id':'c'},'delivery':{'mode':'webhook','url':self.url,'secret':'whsec_'+base64.b64encode(b'x'*32).decode()}}
        self.active=True;self.closed=False
        def check():
            if not self.active:raise PermissionError()
        self.callback=HandoffCallback(self.control,check,lambda:self.closed)
        self.mock=MockCallback();self.callback.transport=lambda _:self.mock
        self.adapter_store=Store.encrypted(Path(self.tmp.name)/'adapter.db',Fernet.generate_key());self.addCleanup(self.adapter_store.close)
        self.a=TunnelAdapter(self.adapter_store,owner=OWNER,user='u',chat='c',callback=self.callback)
    def run_request(self):
        results=[]
        def run():
            try:results.append(self.a.subscribe_authorized(OWNER,self.params))
            except Exception as e:results.append(e)
        thread=threading.Thread(target=run);thread.start()
        for _ in range(100):
            h=self.control.snapshot().get('handoff',{})
            if h.get('status')=='waiting':return thread,results,h
            time.sleep(.01)
        self.fail('No live handoff')
    def test_live_same_request_signed_success(self):
        thread,results,h=self.run_request()
        approve_live(self.control,h['digest'],h['nonce']);thread.join(2)
        self.assertFalse(thread.is_alive());self.assertIsInstance(results[0],dict)
        self.assertEqual(len(self.a.subscriptions),1);self.assertEqual(self.callback.diagnostics['challenge_attempts'],1)
        self.assertEqual(self.control.snapshot()['handoff']['status'],'ended')
        self.assertNotIn(self.params['delivery']['secret'],json.dumps(self.control.snapshot()))
    def test_revocation_or_disconnect_cancels_without_challenge(self):
        thread,results,h=self.run_request();self.closed=True;thread.join(2)
        self.assertIsInstance(results[0],CallbackVerificationError);self.assertFalse(self.a.subscriptions)
        self.assertEqual(self.callback.diagnostics['challenge_attempts'],0)
        with self.assertRaises(ValueError):approve_live(self.control,h['digest'],h['nonce'])
    def test_nonce_and_expired_approval_rejected(self):
        thread,results,h=self.run_request()
        with self.assertRaises(ValueError):approve_live(self.control,h['digest'],'wrong')
        self.control.update(lambda s:s['handoff'].update(expires=time.time()-1))
        with self.assertRaises(ValueError):approve_live(self.control,h['digest'],h['nonce'])
        self.active=False;thread.join(2);self.assertIsInstance(results[0],PermissionError)
        self.assertEqual(self.callback.diagnostics['challenge_attempts'],0)
    def test_timeout_and_busy(self):
        old=self.callback.begin
        def short():
            old();self.callback.local.context['wait_deadline']=time.monotonic()+.05
        self.callback.begin=short
        thread,results,h=self.run_request()
        with self.assertRaises(CallbackVerificationError):self.a.subscribe_authorized(OWNER,self.params)
        thread.join(2);self.assertEqual(results[0].reason,'handoff_timeout');self.assertFalse(self.a.subscriptions)
    def test_dns_deadline_prevents_late_dial(self):
        transport=DeadlineHTTPS(['example.com'],time.monotonic()+.02,lambda:None)
        def slow(*a,**k):time.sleep(.1);return []
        transport.resolver=slow
        started=time.monotonic()
        with self.assertRaises(TimeoutError):transport.addresses('example.com')
        self.assertLess(time.monotonic()-started,.09)
