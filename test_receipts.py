import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from cryptography.fernet import Fernet
from lark_oapi.api.im import v1
from deploy.store import Store,DurableAdapter
from deploy.receipts import ReceiptWorker,ReactionOutcome
from deploy.feishu import FeishuSDK

class Provider:
    def __init__(self,state='confirmed'):self.calls=[];self.state=state
    def receipt(self,mid,emoji):self.calls.append((mid,emoji));return ReactionOutcome(self.state,'r1' if self.state=='confirmed' else None)
class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'state.db';self.key=Fernet.generate_key()
        self.store=Store.encrypted(self.path,self.key);self.addCleanup(self.store.close)
        self.provider=Provider();self.a=DurableAdapter(self.store,feishu=self.provider);self.a.receipt_app_id='cli_test'
        self.worker=ReceiptWorker(self.a,'cli_test',threading.Event())
        self.data=dict(chat_type='p2p',sender_type='user',sender_id=self.a.user,chat_id=self.a.chat,message_type='text',message_id='m1',text='hi')
    def test_persist_then_worker_duplicate_and_restart(self):
        self.a.inject(self.a.owner,self.data);self.assertEqual(self.provider.calls,[])
        self.assertEqual(len(self.store.load()['receipts']),1)
        self.a.inject(self.a.owner,self.data);self.worker.step();self.worker.step()
        self.assertEqual(self.provider.calls,[('m1','Get')])
        second=Store.encrypted(self.path,self.key)
        try:
            restored=DurableAdapter(second,feishu=self.provider);restored.receipt_app_id='cli_test'
            restored.inject(restored.owner,self.data);ReceiptWorker(restored,'cli_test',threading.Event()).step()
            self.assertEqual(len(self.provider.calls),1)
        finally:second.close()
    def test_failed_store_does_not_ack_and_owner_scope_rejected(self):
        for field,value in [('sender_type','app'),('chat_type','group'),('sender_id','other')]:
            data={**self.data,field:value}
            with self.assertRaises(PermissionError):self.a.inject(self.a.owner,data)
        original=self.store.save
        self.store.save=lambda _:(_ for _ in ()).throw(OSError())
        with self.assertRaises(OSError):self.a.inject(self.a.owner,self.data)
        self.assertFalse(self.a.receipts);self.worker.step();self.assertFalse(self.provider.calls)
        self.store.save=original
    def test_permission_blocked_and_unknown_never_retry(self):
        self.provider.state='permission_denied';self.a.inject(self.a.owner,self.data);self.worker.step()
        self.a.inject(self.a.owner,{**self.data,'message_id':'m2'});self.worker.step()
        self.assertEqual(len(self.provider.calls),1);self.assertEqual(len(self.a.messages),2)
        self.assertTrue(self.a.receipt_counters['permission_blocked'])
    def test_crash_sending_becomes_unknown(self):
        self.a.inject(self.a.owner,self.data)
        self.a.mutate(lambda:next(iter(self.a.receipts.values())).update(state='sending'))
        second=Store.encrypted(self.path,self.key)
        try:
            restored=DurableAdapter(second,feishu=self.provider)
            ReceiptWorker(restored,'cli_test',threading.Event()).step()
            self.assertEqual(next(iter(restored.receipts.values()))['state'],'safe_unknown')
            self.assertFalse(self.provider.calls)
        finally:second.close()
    def test_bounded_pending_and_revocation(self):
        for n in range(101):self.a.inject(self.a.owner,{**self.data,'message_id':str(n)})
        self.assertEqual(len(self.a.messages),101);self.assertEqual(len(self.a.receipts),100)
        self.a.check_authorization=lambda _: 'revoked';self.worker.step();self.assertFalse(self.provider.calls)
    def test_official_sdk_builder_and_ambiguous_no_retry(self):
        calls=[]
        def create(req):
            calls.append(req);return NS(raw=NS(status_code=200),code=0,success=lambda:True,data=NS(reaction_id='rid'))
        sdk=FeishuSDK(NS(im=NS(v1=NS(message_reaction=NS(create=create)))),v1)
        result=sdk.receipt('m1','Get')
        self.assertEqual(result.state,'confirmed');self.assertEqual(calls[0].body.reaction_type.emoji_type,'Get')
        def timeout(req):calls.append(req);raise TimeoutError()
        sdk.client.im.v1.message_reaction.create=timeout
        self.assertEqual(sdk.receipt('m1','Get').state,'safe_unknown');self.assertEqual(len(calls),2)
