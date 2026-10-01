"""OAuth protected resource: verify issuer/audience/subject and RS256 JWKS.
Authorization server is an existing user-approved external OAuth provider.
"""
import base64
import json
import time

def b64(text):
    return base64.urlsafe_b64decode(text+'='*((-len(text))%4))

class InsufficientScope(PermissionError):
    pass

class OAuthVerifier:
    def __init__(self,issuer,audience,subject,jwks_url,network,clock=time.time):
        self.issuer,self.audience,self.subject=issuer,audience,subject
        self.jwks_url,self.network,self.clock=jwks_url,network,clock
        self.network.validate_url(jwks_url)
    def __call__(self,authorization):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding,rsa
        try:
            if not authorization.startswith('Bearer '): raise ValueError()
            token=authorization[7:]
            if len(token)>16384: raise ValueError()
            parts=token.split('.')
            if len(parts)!=3: raise ValueError()
            header,claims=json.loads(b64(parts[0])),json.loads(b64(parts[1]))
            if header.get('alg')!='RS256' or not isinstance(header.get('kid'),str) or header.get('crit'): raise ValueError()
            status,data=self.network.request(self.jwks_url)
            if status!=200: raise ValueError()
            keys=json.loads(data)['keys']
            matches=[k for k in keys if k.get('kid')==header['kid'] and k.get('kty')=='RSA' and k.get('use','sig')=='sig' and k.get('alg','RS256')=='RS256']
            if len(matches)!=1: raise ValueError()
            key=matches[0]; n=int.from_bytes(b64(key['n']),'big'); e=int.from_bytes(b64(key['e']),'big')
            if n.bit_length()<2048: raise ValueError()
            rsa.RSAPublicNumbers(e,n).public_key().verify(b64(parts[2]),(parts[0]+'.'+parts[1]).encode(),padding.PKCS1v15(),hashes.SHA256())
            return self.validate_claims(claims)
        except InsufficientScope: raise
        except Exception:
            raise PermissionError('Invalid access token') from None
    def validate_claims(self,claims):
        now=self.clock(); aud=claims.get('aud')
        audience_match=self.audience==aud or isinstance(aud,list) and self.audience in aud
        expiry=claims.get('exp'); not_before=claims.get('nbf',0)
        if isinstance(expiry,bool) or not isinstance(expiry,(float,int)) or expiry<=now or expiry>now+3600:
            raise PermissionError('Invalid token lifetime')
        if not isinstance(not_before,(float,int)) or not_before>now: raise PermissionError('Token not active')
        if claims.get('iss')!=self.issuer or not audience_match or claims.get('sub')!=self.subject:
            raise PermissionError('Token identity rejected')
        if 'feishu:chat' not in str(claims.get('scope','')).split(): raise InsufficientScope('Scope missing')
        return self.subject

class IntrospectionAuthorization:
    """RFC 7662: approved resource-server credential, separate from user token."""
    def __init__(self,verifier,url,credential,network):
        self.verifier,self.url,self.credential,self.network=verifier,url,credential,network
        network.validate_url(url)
    def __call__(self,authorization):
        from urllib.parse import urlencode
        subject=self.verifier(authorization)
        status,body=self.network.request(self.url,'POST',urlencode({'token':authorization[7:],'token_type_hint':'access_token'}).encode(),
          {'Authorization':'Bearer '+self.credential,'Content-Type':'application/x-www-form-urlencoded'})
        if status!=200:raise PermissionError('Authorization status unavailable')
        claims=json.loads(body)
        if claims.get('active') is not True:raise PermissionError('Authorization revoked')
        self.verifier.validate_claims(claims)
        return subject
