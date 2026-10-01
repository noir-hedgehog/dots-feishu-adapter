import base64
import io
import json
import os
import secrets
import socket
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from adapter import Adapter,MockCallback,encoded
from deploy.network import SafeHTTPS
from deploy.store import Store,DurableAdapter
from deploy.transport import HTTPApplication
from deploy.auth import OAuthVerifier
from deploy.feishu import FeishuSDK,normalized

class TestCipher:
    # Persistence transport test double, NOT encryption. Production never selects it.
    def encrypt(self,data): return b'test-only:'+base64.b64encode(data)
    def decrypt(self,data): return base64.b64decode(data[len(b'test-only:'):])

class DeployTests(unittest.TestCase):
    def setUp(self):
        self.a=Adapter(); self.app=HTTPApplication(self.a,self.auth,'https://adapter.example.com/mcp','https://identity.example.com',['https://chatgpt.com'])
        self.secret='whsec_'+base64.b64encode(secrets.token_bytes(32)).decode()
    def auth(self,value):
        if value!='Bearer mock-only': raise PermissionError()
        return 'mock-owner'
    def rpc(self,method='server/discover',params=None):
        params={'_meta':{'io.modelcontextprotocol/protocolVersion':'2026-07-28','io.modelcontextprotocol/clientCapabilities':{}},**(params or {})}
        req={'jsonrpc':'2.0','id':7,'method':method,'params':params}
        headers={'Authorization':'Bearer mock-only','Content-Type':'application/json','Accept':'application/json, text/event-stream','MCP-Protocol-Version':'2026-07-28','Mcp-Method':method}
        if method=='tools/call': headers['Mcp-Name']=params['name']
        return req,headers
    def request(self,req,headers): return self.app.handle('POST','/mcp',headers,encoded(req))
    def test_modern_wire_and_private_catalog(self):
        req,h=self.rpc('tools/list'); status,_,body=self.request(req,h)
        self.assertEqual(status,200); result=json.loads(body)['result']
        self.assertEqual(result['resultType'],'complete'); self.assertEqual(result['cacheScope'],'private'); self.assertIn('ttlMs',result)
    def test_discovery_cache_and_tool_argument_errors(self):
        req,h=self.rpc(); result=json.loads(self.request(req,h)[2])['result']
        self.assertEqual(result['cacheScope'],'private')
        req,h=self.rpc('tools/call',{'name':'send_message','arguments':{}})
        status,_,body=self.request(req,h)
        self.assertEqual(status,200); self.assertTrue(json.loads(body)['result']['isError'])
    def test_auth_origin_and_http_methods(self):
        req,h=self.rpc(); h['Authorization']='wrong'; self.assertEqual(self.request(req,h)[0],401)
        h['Origin']='https://foreign.example'; self.assertEqual(self.request(req,h)[0],403)
        self.assertEqual(self.app.handle('GET','/mcp',{},b'')[0],405)
    def test_resource_metadata(self):
        status,_,body=self.app.handle('GET','/.well-known/oauth-protected-resource',{},b'')
        self.assertEqual(status,200); self.assertEqual(json.loads(body)['scopes_supported'],['feishu:chat'])
    def test_metadata_missing_mismatch_unsupported(self):
        req,h=self.rpc(); req['params'].pop('_meta'); self.assertEqual(json.loads(self.request(req,h)[2])['error']['code'],-32602)
        req,h=self.rpc(); h['Mcp-Method']='tools/list'; self.assertEqual(json.loads(self.request(req,h)[2])['error']['code'],-32020)
        req,h=self.rpc(); req['params']['_meta']['io.modelcontextprotocol/protocolVersion']='unknown'; h['MCP-Protocol-Version']='unknown'
        data=json.loads(self.request(req,h)[2]); self.assertEqual(data['error']['code'],-32022); self.assertEqual(data['error']['data']['supported'],['2026-07-28'])
    def test_unknown_batch_and_parse(self):
        req,h=self.rpc('unknown'); self.assertEqual(self.request(req,h)[0],404)
        self.assertEqual(self.app.handle('POST','/mcp',h,b'[')[0],400)
        self.assertEqual(self.app.handle('POST','/mcp',h,encoded([req]))[0],400)
    def test_content_accept_and_tool_header(self):
        req,h=self.rpc(); h['Accept']='application/json'; self.assertEqual(self.request(req,h)[0],406)
        req,h=self.rpc('tools/call',{'name':'send_message','arguments':{}}); h['Mcp-Name']='reply_to_message'
        self.assertEqual(json.loads(self.request(req,h)[2])['error']['code'],-32020)
    def test_wsgi_in_memory_no_socket(self):
        req,h=self.rpc(); raw=encoded(req); captured=[]
        env={'REQUEST_METHOD':'POST','PATH_INFO':'/mcp','CONTENT_LENGTH':str(len(raw)),'CONTENT_TYPE':'application/json','wsgi.input':io.BytesIO(raw)}
        env.update({'HTTP_'+k.upper().replace('-','_'):v for k,v in h.items() if k!='Content-Type'})
        body=b''.join(self.app(env,lambda status,headers:captured.append(status)))
        self.assertEqual(captured,['200 OK']); self.assertEqual(json.loads(body)['id'],7)
    def test_network_blocks_private_dns_and_unapproved_urls(self):
        def dns(*a,**kw): return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]
        net=SafeHTTPS(['callback.example.com'],resolver=dns)
        with self.assertRaises(ValueError): net.addresses('callback.example.com')
        for url in ['http://callback.example.com','https://evil.example','https://user:pass@callback.example.com','https://callback.example.com:8443']:
            with self.assertRaises(ValueError): net.validate_url(url)
    def test_network_pins_public_ip_and_preserves_tls_hostname(self):
        class Raw:
            def close(self): pass
        class TLS:
            def __init__(self): self.hostname=None
            def wrap_socket(self,raw,server_hostname): self.hostname=server_hostname; raise RuntimeError('Stop before any HTTP')
        addresses=[]; tls=TLS()
        dns=lambda *a,**kw:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))]
        net=SafeHTTPS(['callback.example.com'],resolver=dns,connector=lambda target,**kw:addresses.append(target) or Raw(),tls=tls)
        with self.assertRaises(RuntimeError): net.request('https://callback.example.com/events')
        self.assertEqual(addresses,[('93.184.216.34',443)]); self.assertEqual(tls.hostname,'callback.example.com')
    def test_oauth_claim_boundaries(self):
        net=NS(validate_url=lambda url:None)
        v=OAuthVerifier('https://identity.example.com','https://adapter.example.com/mcp','owner','https://identity.example.com/jwks',net,clock=lambda:1000)
        valid={'iss':v.issuer,'aud':v.audience,'sub':'owner','exp':2000,'scope':'feishu:chat'}
        self.assertEqual(v.validate_claims(valid),'owner')
        for field,value in [('sub','other'),('aud','other'),('iss','other'),('exp',999),('exp',5000),('scope','other'),('nbf',2000)]:
            with self.assertRaises(PermissionError): v.validate_claims({**valid,field:value})
    def test_persistence_restart_dedup_queue_cancel_and_binding(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'state.sqlite3'; store=Store(path,TestCipher()); a=DurableAdapter(store)
            p={'name':'message.created','arguments':{'chat_id':'mock-dm'},'delivery':{'mode':'webhook','url':'https://callback.example.com/a','secret':self.secret}}
            a.subscribe('mock-owner',p)
            msg={'chat_type':'p2p','sender_type':'user','sender_id':'mock-user','chat_id':'mock-dm','message_type':'text','message_id':'test-only','text':'synthetic'}
            a.inject('mock-owner',msg); store.close()
            store=Store(path,TestCipher()); b=DurableAdapter(store)
            self.assertTrue(b.inject('mock-owner',msg)['duplicate']); self.assertEqual(len(b.outbox),1)
            b.drain(); args={'chat_id':'mock-dm','message_id':'test-only','text':'synthetic reply','idempotency_key':'test-key'}
            b.send('mock-owner',args,True); b.unsubscribe('mock-owner',p); store.close()
            store=Store(path,TestCipher()); c=DurableAdapter(store)
            self.assertFalse(c.subscriptions); c.send('mock-owner',args,True); self.assertFalse(c.feishu.calls)
            with self.assertRaises(ValueError): DurableAdapter(store,user='other')
            store.close()
    def test_sdk_port_normalization_and_model_calls(self):
        class Builder:
            def __init__(self): self.values={}
            def __getattr__(self,name): return lambda value:self.values.update({name:value}) or self
            def build(self): return NS(**self.values)
        class Model:
            @staticmethod
            def builder(): return Builder()
        models=NS(**{n:Model for n in ('ReplyMessageRequestBody','ReplyMessageRequest','CreateMessageRequestBody','CreateMessageRequest')})
        calls=[]
        def send(request): calls.append(request); return NS(success=lambda:True,data=NS(message_id='synthetic-out'),raw=NS(status_code=200))
        sdk=FeishuSDK(NS(im=NS(v1=NS(message=NS(create=send,reply=send)))),models,sleep=lambda _:None)
        sdk.send('dm','synthetic','stable-uuid'); sdk.send('dm','synthetic','stable-uuid','inbound')
        self.assertEqual(calls[0].receive_id_type,'chat_id'); self.assertEqual(calls[1].message_id,'inbound'); self.assertEqual(calls[1].request_body.uuid,'stable-uuid')
        event=NS(event=NS(message=NS(chat_type='p2p',chat_id='dm',message_type='text',message_id='inbound',content='{"text":"synthetic"}'),sender=NS(sender_type='user',sender_id=NS(open_id='synthetic-user'))))
        self.assertEqual(normalized(event)['message_id'],'inbound')

try:
    import cryptography
    HAVE_CRYPTO=True
except ImportError: HAVE_CRYPTO=False
try:
    import lark_oapi
    HAVE_LARK=True
except ImportError: HAVE_LARK=False

class DependencyTests(unittest.TestCase):
    @unittest.skipUnless(HAVE_CRYPTO,'cryptography not installed; CI must exercise real encryption')
    def test_real_authenticated_encrypted_store(self):
        from cryptography.fernet import Fernet,InvalidToken
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'state.sqlite3'; key=Fernet.generate_key(); store=Store.encrypted(path,key)
            value={'secret':secrets.token_urlsafe(32)}; store.save(value); self.assertNotIn(value['secret'].encode(),path.read_bytes()); store.close()
            store=Store.encrypted(path,key); self.assertEqual(store.load(),value); store.close()
            store=Store.encrypted(path,Fernet.generate_key())
            with self.assertRaises(InvalidToken): store.load()
            store.close()
    @unittest.skipUnless(HAVE_LARK,'official lark-oapi not installed; CI must exercise actual builders')
    def test_real_official_sdk_builders_no_network(self):
        from lark_oapi.api.im import v1
        calls=[]
        def send(req): calls.append(req); return NS(success=lambda:True,data=NS(message_id='synthetic'),raw=NS(status_code=200))
        sdk=FeishuSDK(NS(im=NS(v1=NS(message=NS(create=send,reply=send)))),v1)
        sdk.send('synthetic-dm','synthetic','idempotency-test'); sdk.send('synthetic-dm','synthetic','idempotency-test','synthetic-in')
        self.assertEqual(calls[0].request_body.uuid,'idempotency-test'); self.assertEqual(calls[1].message_id,'synthetic-in')
    @unittest.skipUnless(HAVE_CRYPTO,'cryptography not installed; CI must exercise RSA token validation')
    def test_real_rs256_jwks_signature(self):
        from cryptography.hazmat.primitives.asymmetric import rsa,padding
        from cryptography.hazmat.primitives import hashes
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        nums=key.public_key().public_numbers()
        def enc(x): return base64.urlsafe_b64encode(x).rstrip(b'=').decode()
        jwks={'keys':[{'kid':'synthetic-kid','kty':'RSA','n':enc(nums.n.to_bytes(256,'big')),'e':enc(nums.e.to_bytes(3,'big'))}]}
        net=NS(validate_url=lambda _:None,request=lambda _:(200,encoded(jwks)))
        v=OAuthVerifier('https://identity.example.com','https://adapter.example.com/mcp','synthetic-owner','https://identity.example.com/jwks',net,clock=lambda:1000)
        first=enc(encoded({'alg':'RS256','kid':'synthetic-kid'})); second=enc(encoded({'iss':v.issuer,'aud':v.audience,'sub':'synthetic-owner','exp':2000,'scope':'feishu:chat'}))
        signature=enc(key.sign((first+'.'+second).encode(),padding.PKCS1v15(),hashes.SHA256()))
        self.assertEqual(v('Bearer '+first+'.'+second+'.'+signature),'synthetic-owner')
        with self.assertRaises(PermissionError): v('Bearer '+first+'.'+second+'.'+enc(b'tampered'))

if __name__=='__main__': unittest.main()
