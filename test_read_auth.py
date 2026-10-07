"""Offline HTTP regressions: real JWT/Fernet code, synthetic keys, no sockets."""
import base64
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from adapter import VERSION, encoded
from deploy.auth import OAuthVerifier, IntrospectionAuthorization
from deploy.store import Store, DurableAdapter
from deploy.transport import HTTPApplication
from deploy.tunnel import Control, OWNER, LocalAuthorization, TunnelAdapter, TunnelHTTP


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


class ReadAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.key=Fernet.generate_key();self.now=1000000000
        self.store=self.new_store('adapter')
        self.a=DurableAdapter(self.store,clock=lambda:self.now)
        self.a.inject(self.a.owner,dict(chat_type='p2p',sender_type='user',sender_id=self.a.user,
            chat_id=self.a.chat,message_type='text',message_id='m1',text='retained-test-text'))
        self.private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        public=self.private.public_key().public_numbers()
        self.jwks={'keys':[{'kid':'test','kty':'RSA','alg':'RS256',
            'n':b64(public.n.to_bytes((public.n.bit_length()+7)//8,'big')),
            'e':b64(public.e.to_bytes((public.e.bit_length()+7)//8,'big'))}]}
        self.claims={'iss':'https://identity.example.com','aud':'https://adapter.example.com/mcp',
            'sub':self.a.owner,'scope':'feishu:chat','exp':self.now+300}
        self.active=True;self.available=True;self.introspected=[]
        fixture=self
        class Network:
            def validate_url(self,url):pass
            def request(self,url,method='GET',body=None,headers=None):
                if url.endswith('/jwks'):
                    return 200,encoded(fixture.jwks)
                if url.endswith('/introspect'):
                    fixture.assertEqual(method,'POST')
                    fixture.assertEqual(headers['Authorization'],'Bearer test-resource-credential')
                    token=parse_qs(body.decode())['token'][0]
                    fixture.introspected.append(token)
                    if not fixture.available:return 503,b'{}'
                    claims=json.loads(base64.urlsafe_b64decode(token.split('.')[1]+'==='))
                    return 200,encoded({**claims,'active':fixture.active})
                raise AssertionError('Unexpected network destination')
        net=Network()
        verifier=OAuthVerifier(self.claims['iss'],self.claims['aud'],self.a.owner,
            self.claims['iss']+'/jwks',net,clock=lambda:self.now)
        self.auth=IntrospectionAuthorization(verifier,self.claims['iss']+'/introspect','test-resource-credential',net)
        self.a.check_authorization=self.auth
        self.http=HTTPApplication(self.a,self.auth,self.claims['aud'],self.claims['iss'],[])

    def new_store(self,name):
        store=Store.encrypted(Path(self.tmp.name)/(name+'.sqlite3'),self.key)
        self.addCleanup(store.close);return store

    def token(self,**claims):
        body=b64(encoded({'alg':'RS256','kid':'test'}))+'.'+b64(encoded({**self.claims,**claims}))
        return body+'.'+b64(self.private.sign(body.encode(),padding.PKCS1v15(),hashes.SHA256()))

    def call(self,name,token=None,args=None,app=None,local=None):
        if args is None:
            args={'chat_id':self.a.chat}
            if name=='get_message_status':args['message_id']='m1'
        headers={'Content-Type':'application/json','Accept':'application/json, text/event-stream',
            'Mcp-Protocol-Version':VERSION,'Mcp-Method':'tools/call','Mcp-Name':name}
        if token is not None:headers['Authorization']='Bearer '+token
        if local is not None:headers['X-Feishu-Local-Auth']=local
        params={'name':name,'arguments':args,'_meta':{
            'io.modelcontextprotocol/protocolVersion':VERSION,'io.modelcontextprotocol/clientCapabilities':{}}}
        status,_,body=(app or self.http).handle('POST','/mcp',headers,
            encoded({'jsonrpc':'2.0','id':1,'method':'tools/call','params':params}))
        return status,json.loads(body) if body else {}

    def test_valid_oauth_reads_with_real_signature_and_introspection(self):
        token=self.token()
        for name in ('list_received_messages','get_message_status'):
            with self.subTest(tool=name):
                status,body=self.call(name,token)
                self.assertEqual(status,200)
                self.assertFalse(body['result']['isError'])
                result=body['result']['structuredContent']
                if name=='list_received_messages':self.assertEqual(result['messages'][0]['text'],'retained-test-text')
                else:self.assertTrue(result['durably_received'])
        self.assertEqual(self.introspected,[token,token])

    def test_missing_and_bad_signature_tokens_cannot_reuse_previous_auth(self):
        token=self.token();parts=token.split('.')
        parts[-1]=b64(b'\0'*256)
        for name in ('list_received_messages','get_message_status'):
            self.assertEqual(self.call(name,token)[0],200)
            for bad in (None,'invalid','.'.join(parts)):
                with self.subTest(tool=name,token_kind='missing' if bad is None else 'invalid'):
                    status,body=self.call(name,bad)
                    self.assertEqual(status,401);self.assertNotIn('retained-test-text',json.dumps(body))

    def test_revoked_token_is_rejected_after_prior_success(self):
        token=self.token()
        self.assertEqual(self.call('list_received_messages',token)[0],200)
        self.active=False
        for name in ('list_received_messages','get_message_status'):
            self.assertEqual(self.call(name,token)[0],401)

    def test_expiry_identity_audience_and_scope_still_enforced(self):
        for claims,expected in (({'exp':self.now-1},401),({'sub':'other'},401),
                ({'aud':'https://other.example.com/mcp'},401),({'scope':'other'},403)):
            for name in ('list_received_messages','get_message_status'):
                with self.subTest(tool=name,claims=claims):self.assertEqual(self.call(name,self.token(**claims))[0],expected)

    def test_introspection_outage_fails_closed(self):
        self.available=False
        for name in ('list_received_messages','get_message_status'):
            self.assertEqual(self.call(name,self.token())[0],503)

    def test_oauth_reads_still_reject_foreign_chat_and_unknown_message(self):
        for name in ('list_received_messages','get_message_status'):
            args={'chat_id':'other'}
            if name=='get_message_status':args['message_id']='m1'
            self.assertEqual(self.call(name,self.token(),args)[0],400)
        self.assertEqual(self.call('get_message_status',self.token(),{'chat_id':self.a.chat,'message_id':'missing'})[0],400)

    def tunnel(self):
        control=Control(self.new_store('control'),clock=lambda:self.now)
        binding=[OWNER,self.a.user,self.a.chat]
        control.update(lambda s:s.update(personal_audience_confirmed=True,binding=binding))
        adapter=TunnelAdapter(self.new_store('tunnel'),owner=OWNER,user=self.a.user,chat=self.a.chat)
        adapter.messages.update(self.a.messages);adapter.message_status.update(self.a.message_status)
        def check(_):
            state=control.snapshot()
            if state.get('personal_audience_confirmed') is not True or state.get('binding')!=binding:
                raise PermissionError('Personal transport authorization revoked')
            return OWNER
        adapter.check_authorization=check
        secret='test-local-secret-'+'x'*48
        app=TunnelHTTP(adapter,LocalAuthorization(secret,control),'https://adapter.example.com/mcp','',[])
        return control,app,secret

    def test_personal_tunnel_valid_and_invalid_credentials(self):
        control,app,secret=self.tunnel()
        for name in ('list_received_messages','get_message_status'):
            self.assertEqual(self.call(name,app=app,local=secret)[0],200)
            for bad in (None,'wrong'):
                self.assertEqual(self.call(name,app=app,local=bad)[0],403)
        control.update(lambda s:s.update(personal_audience_confirmed=False))
        for name in ('list_received_messages','get_message_status'):
            self.assertEqual(self.call(name,app=app,local=secret)[0],403)

    def test_personal_tunnel_reads_recheck_current_binding(self):
        control,app,secret=self.tunnel()
        control.update(lambda s:s.update(binding=[OWNER,'other','other']))
        # Local secret and audience still pass the HTTP gate, but the binding is revoked.
        for name in ('list_received_messages','get_message_status'):
            self.assertEqual(self.call(name,app=app,local=secret)[0],403)

    def test_personal_tunnel_revocation_between_http_auth_and_read(self):
        control,app,secret=self.tunnel()
        original=app.authenticate
        def authenticate(value):
            principal=original(value)
            control.update(lambda s:s.update(personal_audience_confirmed=False))
            return principal
        # TunnelHTTP authenticates twice; revoke immediately after the inner gate succeeds.
        calls=0
        def after_inner_gate(value):
            nonlocal calls
            calls+=1
            return authenticate(value) if calls%2==0 else original(value)
        app.authenticate=after_inner_gate
        for name in ('list_received_messages','get_message_status'):
            control.update(lambda s:s.update(personal_audience_confirmed=True))
            self.assertEqual(self.call(name,app=app,local=secret)[0],403)


if __name__=='__main__':unittest.main()
