import unittest
import tempfile
from pathlib import Path
from cryptography.fernet import Fernet
from adapter import Adapter
from deploy.store import Store,DurableAdapter

class InboxTests(unittest.TestCase):
    def message(self,mid='m1'):
        return dict(chat_type='p2p',sender_type='user',sender_id='mock-user',chat_id='mock-dm',message_type='text',message_id=mid,text='hello')
    def test_scope_dedup_cursor_time_and_no_network(self):
        a=Adapter(clock=lambda:1000000000)
        a.inject(a.owner,self.message());a.inject(a.owner,self.message());a.inject(a.owner,self.message('m2'))
        first=a.list_received_messages(a.owner,{'chat_id':a.chat,'limit':1})
        self.assertEqual(len(first['messages']),1)
        self.assertEqual(first['next_cursor'],'m1')
        second=a.list_received_messages(a.owner,{'chat_id':a.chat,'cursor':'m1'})
        self.assertEqual(second['messages'][0]['message_id'],'m2')
        self.assertEqual(a.list_received_messages(a.owner,{'chat_id':a.chat,'since':'2030-01-01T00:00:00Z'})['messages'],[])
        self.assertEqual(a.callback.calls,[]);self.assertEqual(a.feishu.calls,[])
        for args in ({'chat_id':'other'},{'chat_id':a.chat,'limit':True},{'chat_id':a.chat,'limit':51},{'chat_id':a.chat,'cursor':'foreign'},{'chat_id':a.chat,'since':'2020-01-01'},{'chat_id':a.chat,'secret':'x'}):
            with self.assertRaises(ValueError):a.list_received_messages(a.owner,args)
        with self.assertRaises(PermissionError):a.list_received_messages('other',{'chat_id':a.chat})
    def test_legacy_null_and_restart_revocation(self):
        with tempfile.TemporaryDirectory() as d:
            key=Fernet.generate_key();store=Store.encrypted(Path(d)/'s.db',key)
            a=DurableAdapter(store);a.inject(a.owner,self.message())
            a.mutate(lambda:a.messages['m1'].pop('timestamp'));store.close()
            store=Store.encrypted(Path(d)/'s.db',key);a=DurableAdapter(store)
            self.assertIsNone(a.list_received_messages(a.owner,{'chat_id':a.chat})['messages'][0]['timestamp'])
            self.assertEqual(a.list_received_messages(a.owner,{'chat_id':a.chat,'since':'2000-01-01T00:00:00Z'})['messages'],[])
            a.check_authorization=lambda _: 'revoked'
            with self.assertRaises(PermissionError):a.list_received_messages(a.owner,{'chat_id':a.chat})
            store.close()
