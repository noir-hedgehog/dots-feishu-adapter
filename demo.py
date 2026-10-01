"""Entirely in-memory lifecycle; prints no signing keys or message content."""
import base64
import secrets
import json
from adapter import Adapter

a = Adapter()
s = {'name':'message.created','arguments':{'chat_id':'mock-dm'},'delivery':{
    'mode':'webhook','url':'https://callback.example.com/events',
    'secret':'whsec_'+base64.b64encode(secrets.token_bytes(32)).decode()},'cursor':None}
a.subscribe('mock-owner',s)
a.inject('mock-owner',{'chat_type':'p2p','sender_type':'user','sender_id':'mock-user',
    'chat_id':'mock-dm','message_type':'text','message_id':'demo-inbound','text':'mock input'})
a.drain()
a.rpc('mock-owner','tools/call',{'name':'reply_to_message','arguments':{
    'chat_id':'mock-dm','message_id':'demo-inbound','text':'mock reply','idempotency_key':'demo-reply'}})
a.unsubscribe('mock-owner',s)
print(json.dumps({'verified_callbacks':1,'events_delivered':1,'mock_replies':len(a.feishu.calls),
    'remaining_subscriptions':len(a.subscriptions),'external_requests':0}))
