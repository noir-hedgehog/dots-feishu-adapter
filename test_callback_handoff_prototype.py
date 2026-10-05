"""Offline design experiment only; no runtime import or production activation."""
import base64
import tempfile
import unittest
from pathlib import Path
from cryptography.fernet import Fernet
from adapter import Adapter,CallbackVerificationError,MockCallback
from deploy.store import Store
from deploy.tunnel import Control,ApprovedCallback
from ops.callback_metadata import summary

class PublicMock(MockCallback):
    def __init__(self,hosts):super().__init__()
    def addresses(self,host):return ['8.8.8.8']

class WaitingPrototype(ApprovedCallback):
    def __init__(self,control,tick,recheck,budget=3):
        super().__init__(control,PublicMock);self.tick=tick;self.recheck=recheck;self.budget=budget
    def validate_url(self,url):
        # Deterministic ticks stand in for a bounded monotonic wait. Not deployed.
        self.transport(url)
        for step in range(self.budget+1):
            self.recheck()
            if self.control.callback(url):return
            if step<self.budget:self.tick(step)
        raise CallbackVerificationError('local_approval_pending')

class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'control.db';self.key=Fernet.generate_key()
        self.store=Store.encrypted(self.path,self.key);self.addCleanup(self.store.close)
        self.control=Control(self.store,clock=lambda:1000)
        self.control.update(lambda s:s.update(personal_audience_confirmed=True,binding=['avalon-personal','u','c']))
        self.url='https://callback.example.com/events'
        self.params={'name':'message.created','arguments':{'chat_id':'c'},'delivery':{'mode':'webhook','url':self.url,'secret':'whsec_'+base64.b64encode(b'x'*32).decode()}}
    def test_same_request_approval_then_challenge_and_persisted_approval(self):
        def approve(step):
            if step==0:self.control.approve_callback(next(iter(self.control.snapshot()['callback_pending'])))
        callback=WaitingPrototype(self.control,approve,lambda:None)
        a=Adapter(owner='avalon-personal',user='u',chat='c',callback=callback,clock=lambda:1000)
        result=a.subscribe(a.owner,self.params)
        self.assertIn(result['id'],a.subscriptions)
        self.assertEqual(len(callback.diagnostics),2)
        self.assertEqual(callback.diagnostics['challenge_attempts'],1)
        second=Store.encrypted(self.path,self.key)
        try:
            reloaded=Control(second,clock=lambda:1000)
            self.assertTrue(reloaded.callback(self.url))
            meta=summary(reloaded.snapshot(),1000)
            self.assertEqual(meta['approved_count'],1);self.assertEqual(meta['pending_count'],0)
            self.assertNotIn(self.url,str(meta))
        finally:second.close()
    def test_timeout_does_not_challenge_or_save(self):
        callback=WaitingPrototype(self.control,lambda _:None,lambda:None,budget=1)
        a=Adapter(owner='avalon-personal',user='u',chat='c',callback=callback)
        with self.assertRaises(CallbackVerificationError):a.subscribe(a.owner,self.params)
        self.assertEqual(callback.diagnostics['challenge_attempts'],0);self.assertFalse(a.subscriptions)
    def test_authorization_revoked_during_wait_does_not_challenge(self):
        active=[True]
        def revoke(_):active[0]=False
        def check():
            if not active[0]:raise PermissionError('revoked')
        callback=WaitingPrototype(self.control,revoke,check)
        a=Adapter(owner='avalon-personal',user='u',chat='c',callback=callback)
        with self.assertRaises(PermissionError):a.subscribe(a.owner,self.params)
        self.assertEqual(callback.diagnostics['challenge_attempts'],0);self.assertFalse(a.subscriptions)
