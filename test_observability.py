import base64
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from cryptography.fernet import Fernet
from adapter import EVENT,MockFeishu
from deploy.store import Store,DurableAdapter
from deploy.observability import emit,health_metrics
from deploy.receipts import ReceiptWorker,ReactionOutcome
import threading

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'s.db';self.key=Fernet.generate_key()
        self.store=Store.encrypted(self.path,self.key);self.addCleanup(self.store.close)
        self.a=DurableAdapter(self.store,clock=lambda:1000000000)
        self.msg=dict(chat_type='p2p',sender_type='user',sender_id=self.a.user,chat_id=self.a.chat,message_type='text',message_id='m1',text='BODY_SENTINEL')
        self.params={'name':EVENT,'arguments':{'chat_id':self.a.chat},'delivery':{'mode':'webhook','url':'https://callback.example.com/events','secret':'whsec_'+base64.b64encode(b'x'*32).decode()}}
    def test_per_message_delivery_reply_receipt_persistence_and_no_body(self):
        self.a.subscribe(self.a.owner,self.params);self.a.receipt_app_id='cli_fake'
        provider=MockFeishu();provider.receipt=lambda *args:ReactionOutcome('confirmed','r1');self.a.feishu=provider
        self.a.inject(self.a.owner,self.msg);self.a.drain()
        ReceiptWorker(self.a,'cli_fake',threading.Event()).step()
        args={'chat_id':self.a.chat,'message_id':'m1','text':'OUTGOING_SENTINEL','idempotency_key':'key'}
        self.a.send(self.a.owner,args,True);self.a.send(self.a.owner,args,True)
        status=self.a.get_message_status(self.a.owner,{'chat_id':self.a.chat,'message_id':'m1'})
        self.assertEqual(status['webhook_outcomes'],{'delivered':1})
        self.assertEqual(status['reply']['confirmed_count'],1)
        self.assertTrue(status['get_receipt']['provider_confirmed'])
        self.assertNotIn('SENTINEL',json.dumps(status));self.assertNotIn('whsec_',json.dumps(status));self.assertNotIn('callback.example',json.dumps(status))
        second=Store.encrypted(self.path,self.key)
        try:
            restored=DurableAdapter(second,clock=lambda:1000000000)
            self.assertEqual(restored.get_message_status(restored.owner,{'chat_id':restored.chat,'message_id':'m1'}),status)
        finally:second.close()
    def test_scope_and_old_records(self):
        self.a.messages['legacy']={'chat_id':self.a.chat,'text':'BODY_SENTINEL'}
        status=self.a.get_message_status(self.a.owner,{'chat_id':self.a.chat,'message_id':'legacy'})
        self.assertFalse(status['historical_status_available']);self.assertEqual(status['get_receipt']['state'],'not_recorded')
        for args in ({'chat_id':'other','message_id':'legacy'},{'chat_id':self.a.chat,'message_id':'missing'}):
            with self.assertRaises(ValueError):self.a.get_message_status(self.a.owner,args)
        with self.assertRaises(PermissionError):self.a.get_message_status('other',{'chat_id':self.a.chat,'message_id':'legacy'})
    def test_health_lease_and_queue_age(self):
        self.a.subscribe(self.a.owner,self.params);self.a.inject(self.a.owner,self.msg)
        h=health_metrics(self.a,1000000061)
        self.assertTrue(h['queue_delayed']);self.assertEqual(h['queue_oldest_age_seconds'],61)
        self.assertEqual(h['subscription_remaining_seconds'],3539)
        self.assertFalse(health_metrics(self.a,1000003601)['events_ready'])
    def test_logging_strict_allowlist_and_failed_output_safe(self):
        sink=io.StringIO()
        with contextlib.redirect_stdout(sink):emit('https://SECRET_URL','BODY_SECRET',count=1,http_status=200,unknown='CREDENTIAL_SECRET')
        record=json.loads(sink.getvalue());self.assertEqual(record,{'event':'worker','outcome':'unknown','http_status':200,'count':1})
        class Broken:
            def write(self,*args):raise OSError('SECRET')
        with contextlib.redirect_stdout(Broken()):emit('worker','unknown')

class SafeLogReaderTests(unittest.TestCase):
    def test_no_unknown_keys_or_payload_pass_through(self):
        from ops.read_safe_logs import sanitize
        self.assertEqual(sanitize({'event':'receipt','outcome':'confirmed','text':'BODY_SECRET','url':'SECRET_URL','http_status':200}),{'event':'receipt','outcome':'confirmed','http_status':200})
        self.assertIsNone(sanitize({'event':'secret','outcome':'confirmed'}))
        self.assertIsNone(sanitize({'event':'adapter_error','stage':'startup','category':'SECRET'}))
