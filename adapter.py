"""Offline MCP 2.0 event prototype. No network transport or credentials enabled."""
import base64
import hashlib
import hmac
import json
import secrets
import ssl
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlsplit

VERSION = '2026-07-28'
EVENT = 'message.created'

class CallbackVerificationError(ValueError):
    def __init__(self,reason):
        super().__init__('Callback verification failed'); self.reason=reason

def encoded(value):
    return json.dumps(value, separators=(',', ':'), ensure_ascii=False).encode()

def key_bytes(secret):
    try:
        if not secret.startswith('whsec_'): raise ValueError()
        key = base64.b64decode(secret[6:], validate=True)
        if not 24 <= len(key) <= 64: raise ValueError()
        return key
    except (ValueError, TypeError):
        raise ValueError('Invalid signing key') from None

def signed_headers(secret, event_id, body, timestamp, subscription_id):
    signature = hmac.new(key_bytes(secret), event_id.encode() + b'.' + str(timestamp).encode() + b'.' + body, hashlib.sha256).digest()
    return {'Content-Type': 'application/json', 'webhook-id': event_id,
            'webhook-timestamp': str(timestamp),
            'webhook-signature': 'v1,' + base64.b64encode(signature).decode(),
            'X-MCP-Subscription-Id': subscription_id}

def verify(secret, body, headers, now):
    try:
        timestamp = int(headers['webhook-timestamp'])
        if abs(now - timestamp) > 300: return False
        expected = signed_headers(secret, headers['webhook-id'], body, timestamp, headers['X-MCP-Subscription-Id'])['webhook-signature']
        return any(hmac.compare_digest(expected, item) for item in headers['webhook-signature'].split(' '))
    except (KeyError, ValueError):
        return False

class MockCallback:
    """Only a reserved example hostname is accepted; never sends HTTP."""
    def __init__(self):
        self.calls = []
        self.statuses = []
        self.bad_challenge = False
    def post(self, url, body, headers):
        if urlsplit(url).hostname != 'callback.example.com':
            raise ValueError('Offline prototype accepts only callback.example.com')
        self.calls.append((body, headers))
        status = self.statuses.pop(0) if self.statuses else 200
        payload = json.loads(body)
        response = {'challenge': 'wrong' if self.bad_challenge else payload.get('challenge')}
        return status, response
    def validate_url(self, url):
        parsed=urlsplit(url)
        if parsed.scheme!='https' or parsed.hostname!='callback.example.com' or parsed.username or parsed.password or parsed.fragment or parsed.port not in (None,443):
            raise ValueError('Offline callback must be HTTPS callback.example.com')

class MockFeishu:
    def __init__(self):
        self.calls = []
        self.failures = 0
    def send(self, chat_id, text, uuid, reply_to=None):
        if self.failures:
            self.failures -= 1
            raise TimeoutError('Mock transient transport failure')
        self.calls.append({'chat_id': chat_id, 'text': text, 'uuid': uuid, 'reply_to': reply_to})
        return {'message_id': 'mock_out_' + uuid}

class Adapter:
    def __init__(self, owner='mock-owner', user='mock-user', chat='mock-dm', callback=None, feishu=None, clock=time.time):
        self.owner, self.user, self.chat = owner, user, chat
        self.callback, self.feishu = callback or MockCallback(), feishu or MockFeishu()
        self.clock = clock
        # Ephemeral only. Restart invalidates subscriptions, dedup and associations.
        self.subscriptions, self.messages, self.outbox, self.sent = {}, {}, {}, {}
        self.delivery_counts = {}
        self.message_status = {}
    def authorize(self, principal):
        if principal != self.owner: raise PermissionError('Unauthorized principal')
    def filters(self, params):
        if params.get('name') != EVENT or params.get('arguments') != {'chat_id': self.chat}:
            raise ValueError('Only the authorized private chat is available')
    def event_list(self):
        return {'events': [{'name': EVENT, 'description': 'New text from the single authorized Feishu private chat.',
          'delivery': ['webhook'], 'inputSchema': {'type':'object','properties':{'chat_id':{'type':'string','enum':[self.chat]}},'required':['chat_id'],'additionalProperties':False},
          'payloadSchema': {'type':'object','properties':{k:{'type':'string'} for k in ('chat_id','message_id','text')},'required':['chat_id','message_id','text'],'additionalProperties':False}}]}
    def subscribe(self, principal, params):
        self.authorize(principal); self.filters(params)
        if params.get('cursor') is not None: raise ValueError('Replay cursor unsupported')
        delivery = params.get('delivery', {})
        url, secret = delivery.get('url', ''), delivery.get('secret', '')
        parsed = urlsplit(url)
        if delivery.get('mode') != 'webhook': raise ValueError('Webhook delivery required')
        key_bytes(secret)
        try:self.callback.validate_url(url)
        except CallbackVerificationError:raise
        except (ValueError,TypeError):raise CallbackVerificationError('destination_rejected') from None
        ttl = params.get('ttlMs', 3600000)
        if ttl is None: ttl = 3600000  # never grant indefinite access
        if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl <= 0: raise ValueError('Invalid TTL')
        expiration = self.clock() + min(ttl,3600000)/1000
        identity = encoded([principal,url,EVENT,{'chat_id':self.chat}])
        sid = 'sub_' + hashlib.sha256(identity).hexdigest()[:24]
        challenge = secrets.token_urlsafe(32)
        body = encoded({'type':'verification','challenge':challenge})
        headers = signed_headers(secret,'verify_'+secrets.token_hex(16),body,int(self.clock()),sid)
        try: status, response = self.callback.post(url, body, headers)
        except TimeoutError: raise CallbackVerificationError('timeout') from None
        except Exception: raise CallbackVerificationError('connection_failed') from None
        if not isinstance(response,dict) or not 200 <= status < 300 or not hmac.compare_digest(str(response.get('challenge','')),challenge):
            raise CallbackVerificationError('challenge_failed')
        previous=self.subscriptions.get(sid)
        self.subscriptions[sid] = {'owner':principal,'url':url,'secret':secret,'expires':expiration}
        if previous and previous['secret']!=secret and previous['expires']>self.clock():
            self.subscriptions[sid].update(previous_secret=previous['secret'],rotation_until=self.clock()+60)
        return {'id':sid,'refreshBefore':datetime.fromtimestamp(expiration,timezone.utc).isoformat(),'cursor':None,'truncated':False}
    def unsubscribe(self, principal, params):
        self.authorize(principal); self.filters(params)
        url = params.get('delivery',{}).get('url')
        for sid,sub in list(self.subscriptions.items()):
            if sub['owner']==principal and sub['url']==url:
                del self.subscriptions[sid]
                self.outbox = {k:v for k,v in self.outbox.items() if k[0]!=sid}
        return {}
    def inject(self, principal, data):
        self.authorize(principal)
        # SDK-normalized event, not a trusted public HTTP webhook.
        if data.get('chat_type')!='p2p' or data.get('sender_type')!='user' or data.get('sender_id')!=self.user or data.get('chat_id')!=self.chat or data.get('message_type')!='text':
            raise PermissionError('Only authorized human text in private chat is accepted')
        mid, text = data.get('message_id'),data.get('text')
        if not isinstance(mid,str) or not mid or not isinstance(text,str) or not text.strip() or len(text)>8000:
            raise ValueError('Invalid message')
        if mid in self.messages: return {'duplicate':True}
        event = {'eventId':'evt_'+hashlib.sha256(mid.encode()).hexdigest(), 'name':EVENT,
          'timestamp':datetime.fromtimestamp(self.clock(),timezone.utc).isoformat(),
          'data':{'chat_id':self.chat,'message_id':mid,'text':text},'cursor':None}
        self.messages[mid] = {**event['data'], 'timestamp': event['timestamp']}
        self.message_status[mid]={'delivery':{}}
        for sid,sub in self.subscriptions.items():
            if sub['expires']>self.clock(): self.outbox[(sid,event['eventId'])]={'event':event,'attempts':0,'due':self.clock()}
        return {'duplicate':False,'queued':len(self.outbox)}
    def terminal(self, key, reason):
        item=self.outbox.pop(key,None)
        if item:
            mid=item['event']['data']['message_id']
            counts=self.message_status.setdefault(mid,{}).setdefault('delivery',{})
            counts[reason]=counts.get(reason,0)+1
        self.delivery_counts[reason]=self.delivery_counts.get(reason,0)+1
    def drain_one(self,key):
        item=self.outbox.get(key)
        if not item: return
        sid,eid=key; sub=self.subscriptions.get(sid)
        if not sub or sub['expires']<=self.clock():
            self.terminal(key,'expired'); return
        if item['due']>self.clock(): return
        item['attempts']+=1
        status=None; reason='unexpected_error'; permanent=False
        try:
            body=encoded(item['event'])
            headers=signed_headers(sub['secret'],eid,body,int(self.clock()),sid)
            if sub.get('rotation_until',0)>self.clock():
                headers['webhook-signature']+=' '+signed_headers(sub['previous_secret'],eid,body,int(self.clock()),sid)['webhook-signature']
            else:
                sub.pop('previous_secret',None); sub.pop('rotation_until',None)
            status,_=self.callback.post(sub['url'],body,headers)
            reason='http_error'
        except ssl.SSLCertVerificationError:
            reason='tls_certificate_rejected'; permanent=True
        except ValueError:
            reason='destination_or_payload_rejected'; permanent=True
        except ssl.SSLError: reason='tls_transport_error'
        except OSError: reason='network_error'
        except Exception: pass  # bounded unknown failures; never persist exception text
        if status is not None and 200<=status<300:
            self.terminal(key,'delivered'); return
        if status==410: self.subscriptions.pop(sid,None)
        if permanent or status in (410,413) or (status is not None and 400<=status<500 and status!=429):
            self.terminal(key,reason); return
        if item['attempts']>=3:
            self.terminal(key,reason+'_exhausted'); return
        item['last_error']=reason
        item['due']=self.clock()+2**item['attempts']
    def drain(self):
        for key in list(self.outbox): self.drain_one(key)
        return {'pending':len(self.outbox),'outcomes':dict(self.delivery_counts)}
    def send(self, principal, args, reply=False):
        self.authorize(principal)
        required = {'chat_id','text','idempotency_key'} | ({'message_id'} if reply else set())
        if set(args) != required: raise ValueError('Invalid tool fields')
        if args.get('chat_id')!=self.chat: raise PermissionError('Unauthorized chat')
        mid=args.get('message_id') if reply else None
        if reply and (mid not in self.messages or self.messages[mid]['chat_id']!=self.chat): raise ValueError('Unknown inbound message')
        text,key=args.get('text'),args.get('idempotency_key')
        if not isinstance(text,str) or not text.strip() or len(text)>8000 or not isinstance(key,str) or not key or len(key)>128: raise ValueError('Invalid send arguments')
        fingerprint=hashlib.sha256(encoded([self.chat,mid,text])).hexdigest()
        if key in self.sent:
            old,result=self.sent[key]
            if old!=fingerprint: raise ValueError('Idempotency key conflict')
            return result
        # Deterministic provider UUID must also deduplicate an ambiguous timeout.
        uuid=hashlib.sha256(encoded([principal,key])).hexdigest()[:32]
        result=self.feishu.send(self.chat,text,uuid,mid)
        self.sent[key]=(fingerprint,result)
        return result
    def list_received_messages(self, principal, args):
        self.authorize(principal)
        if not isinstance(args,dict) or set(args)-{'chat_id','limit','cursor','since'} or args.get('chat_id')!=self.chat:
            raise ValueError('Invalid inbox scope')
        limit=args.get('limit',20)
        if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=50:raise ValueError('Invalid limit')
        cursor=args.get('cursor'); since=args.get('since')
        if cursor is not None and (not isinstance(cursor,str) or cursor not in self.messages):raise ValueError('Unknown cursor')
        if since is not None:
            if not isinstance(since,str):raise ValueError('Invalid since')
            parsed=datetime.fromisoformat(since.replace('Z','+00:00'))
            if parsed.tzinfo is None:raise ValueError('Timezone required')
            since=parsed.timestamp()
        rows=[]; after=cursor is None
        for mid,item in self.messages.items():
            if not after:
                if mid==cursor:after=True
                continue
            if item.get('chat_id')!=self.chat:continue
            stamp=item.get('timestamp')
            if since is not None and (stamp is None or datetime.fromisoformat(stamp).timestamp()<since):continue
            rows.append({'message_id':mid,'text':item['text'],'timestamp':stamp})
        page=rows[:limit]
        return {'messages':page,'next_cursor':page[-1]['message_id'] if len(rows)>limit else None,
                'timestamp_basis':'adapter_received_at; legacy records have null timestamp'}
    def get_message_status(self,principal,args):
        self.authorize(principal)
        if not isinstance(args,dict) or set(args)!={'chat_id','message_id'} or args.get('chat_id')!=self.chat:raise ValueError('Invalid diagnostic scope')
        mid=args.get('message_id')
        if not isinstance(mid,str) or mid not in self.messages:raise ValueError('Unknown retained message')
        item=self.messages[mid];status=self.message_status.get(mid,{})
        receipts=[x for x in getattr(self,'receipts',{}).values() if x['message_id']==mid]
        receipt=receipts[-1] if receipts else None
        return {'message_id':mid,'received_at':item.get('timestamp'),'durably_received':True,
            'webhook_pending':sum(x['event']['data']['message_id']==mid for x in self.outbox.values()),
            'webhook_outcomes':dict(status.get('delivery',{})),
            'reply':dict(status.get('reply',{})),
            'get_receipt':{'state':receipt['state'] if receipt else 'not_recorded','provider_confirmed':bool(receipt and receipt['state']=='confirmed'),'http_status':receipt.get('last_http_status',0) if receipt else 0,'provider_code':receipt.get('provider_code') if receipt else None},
            'historical_status_available':mid in self.message_status}
    def tools(self):
        common={'chat_id':{'type':'string','enum':[self.chat]},'text':{'type':'string','minLength':1,'maxLength':8000},'idempotency_key':{'type':'string','minLength':1,'maxLength':128}}
        result=[]
        for name in ('send_message','reply_to_message'):
            props=dict(common)
            if name=='reply_to_message': props['message_id']={'type':'string'}
            result.append({'name':name,'description':'Send text only to the authorized Feishu private chat.' if name=='send_message' else 'Reply to a known inbound message in the authorized Feishu private chat.',
              'inputSchema':{'type':'object','properties':props,'required':list(props),'additionalProperties':False},
              'annotations':{'readOnlyHint':False,'destructiveHint':False,'idempotentHint':True,'openWorldHint':True}})
        result.append({'name':'list_received_messages','description':'Read retained incoming text from the paired private chat, oldest first. No provider history fetch. Timestamp is adapter receipt time; older records may have null timestamp.',
          'inputSchema':{'type':'object','properties':{'chat_id':{'type':'string','enum':[self.chat]},'limit':{'type':'integer','minimum':1,'maximum':50,'default':20},'cursor':{'type':'string'},'since':{'type':'string','format':'date-time'}},'required':['chat_id'],'additionalProperties':False},
          'annotations':{'readOnlyHint':True,'destructiveHint':False,'idempotentHint':True,'openWorldHint':False}})
        result.append({'name':'get_message_status','description':'Read status only for a retained paired-chat message: durable receipt, webhook outcomes, reply confirmation and Get receipt. No message body or secrets.',
          'inputSchema':{'type':'object','properties':{'chat_id':{'type':'string','enum':[self.chat]},'message_id':{'type':'string'}},'required':['chat_id','message_id'],'additionalProperties':False},
          'annotations':{'readOnlyHint':True,'destructiveHint':False,'idempotentHint':True,'openWorldHint':False}})
        return {'tools':result}
    def rpc(self, principal, method, params):
        self.authorize(principal)
        if method=='server/discover': return {'resultType':'complete','supportedVersions':[VERSION],'capabilities':{'tools':{},'events':{}}}
        if method=='initialize': return {'protocolVersion':VERSION,'serverInfo':{'name':'feishu-offline-prototype','version':'0.1'},'capabilities':{'tools':{},'events':{}}}
        if method=='events/list': return self.event_list()
        if method=='events/subscribe': return self.subscribe(principal,params)
        if method=='events/unsubscribe': return self.unsubscribe(principal,params)
        if method=='tools/list': return self.tools()
        if method=='tools/call':
            name=params.get('name')
            if name not in ('send_message','reply_to_message','list_received_messages','get_message_status'): raise ValueError('Unknown tool')
            result=self.get_message_status(principal,params.get('arguments',{})) if name=='get_message_status' else self.list_received_messages(principal,params.get('arguments',{})) if name=='list_received_messages' else self.send(principal,params.get('arguments',{}),name=='reply_to_message')
            return {'content':[{'type':'text','text':json.dumps(result)}],'structuredContent':result,'isError':False}
        raise ValueError('Unsupported method')

def serve():
    adapter=Adapter()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass  # never log JSON, signatures or message text
        def do_POST(self):
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=262144: raise ValueError('Invalid body size')
                req=json.loads(self.rfile.read(length)); principal=self.headers.get('X-Mock-Principal')
                if self.path=='/mock/inject': result=adapter.inject(principal,req)
                elif self.path=='/mock/drain': adapter.authorize(principal); result=adapter.drain()
                elif self.path=='/mcp': result=adapter.rpc(principal,req.get('method'),req.get('params',{}))
                else: raise ValueError('Unknown path')
                response={'jsonrpc':'2.0','id':req.get('id'),'result':result} if self.path=='/mcp' else result
                status=200
            except Exception:
                # Deliberately generic; no exception repr may disclose secrets.
                response={'jsonrpc':'2.0','id':None,'error':{'code':-32602,'message':'Request rejected by offline prototype'}}; status=400
            body=encoded(response); self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
    print('Offline prototype on 127.0.0.1:8765; mock-only, no network delivery, not a production MCP transport.')
    HTTPServer(('127.0.0.1',8765),Handler).serve_forever()

if __name__=='__main__': serve()
