"""MCP 2026-07-28 stateless Streamable HTTP JSON binding, WSGI application.
No legacy sessions, SSE/list changes, sampling or elicitation are advertised.
"""
import base64
import json
from adapter import VERSION,encoded,CallbackVerificationError
from deploy.auth import InsufficientScope

class HTTPApplication:
    def __init__(self,adapter,authenticate,resource,issuer,allowed_origins):
        self.adapter,self.authenticate=adapter,authenticate
        self.resource,self.issuer=resource,issuer
        self.origins=frozenset(allowed_origins)
    def respond(self,status,body=None,extra=None):
        return status,{'Content-Type':'application/json','Cache-Control':'no-store',**(extra or {})},encoded(body) if body is not None else b''
    def error(self,status,code,message,rid=None,data=None):
        error={'code':code,'message':message}
        if data is not None: error['data']=data
        body={'jsonrpc':'2.0','error':error}
        if rid is not None: body['id']=rid
        return self.respond(status,body)
    def handle(self,method,path,headers,body):
        h={k.lower():v for k,v in headers.items()}
        if h.get('origin') and h['origin'] not in self.origins: return self.error(403,-32600,'Origin rejected')
        if path=='/.well-known/oauth-protected-resource' and method=='GET':
            return self.respond(200,{'resource':self.resource,'authorization_servers':[self.issuer],'scopes_supported':['feishu:chat'],'bearer_methods_supported':['header']})
        if path!='/mcp': return self.respond(404)
        if method!='POST': return self.respond(405,extra={'Allow':'POST'})
        try: principal=self.authenticate(h.get('authorization',''))
        except InsufficientScope:
            return self.respond(403,extra={'WWW-Authenticate':'Bearer error="insufficient_scope", scope="feishu:chat"'})
        except Exception:
            # Metadata URL is public configuration, not supplied by the caller.
            from urllib.parse import urlsplit
            p=urlsplit(self.resource)
            metadata=f'{p.scheme}://{p.netloc}/.well-known/oauth-protected-resource'
            return self.respond(401,extra={'WWW-Authenticate':f'Bearer resource_metadata="{metadata}", scope="feishu:chat"'})
        if h.get('content-type','').split(';')[0].strip()!='application/json': return self.respond(415)
        accept={x.strip().split(';')[0] for x in h.get('accept','').split(',')}
        if not {'application/json','text/event-stream'}<=accept: return self.respond(406)
        if len(body)>262144: return self.respond(413)
        try: req=json.loads(body)
        except (ValueError,UnicodeDecodeError): return self.error(400,-32700,'Parse error')
        if not isinstance(req,dict) or req.get('jsonrpc')!='2.0' or not isinstance(req.get('method'),str) or 'result' in req or 'error' in req:
            return self.error(400,-32600,'Invalid request')
        rid=req.get('id'); params=req.get('params',{})
        if rid is not None and (isinstance(rid,bool) or not isinstance(rid,(str,int))): return self.error(400,-32600,'Invalid request ID')
        if 'id' not in req:
            # No notifications defined for the advertised 2026 binding.
            return self.error(400,-32601,'Notification unsupported')
        if not isinstance(params,dict) or not isinstance(params.get('_meta'),dict): return self.error(400,-32602,'Required metadata missing',rid)
        meta=params['_meta']; version=meta.get('io.modelcontextprotocol/protocolVersion')
        if not isinstance(version,str) or not isinstance(meta.get('io.modelcontextprotocol/clientCapabilities'),dict):
            return self.error(400,-32602,'Required metadata missing',rid)
        if h.get('mcp-protocol-version')!=version or h.get('mcp-method')!=req['method']:
            return self.error(400,-32020,'Required header mismatch',rid)
        if version!=VERSION: return self.error(400,-32022,'Unsupported protocol version',rid,{'requested':version,'supported':[VERSION]})
        if req['method']=='tools/call':
            name=params.get('name')
            expected=name if isinstance(name,str) and name.isascii() and all(32<=ord(c)<127 for c in name) else None
            if h.get('mcp-name')!=expected or expected is None: return self.error(400,-32020,'Mcp-Name mismatch',rid)
        clean={k:v for k,v in params.items() if k!='_meta'}
        supported={'server/discover','ping','events/list','events/subscribe','events/unsubscribe','tools/list','tools/call'}
        if req['method'] not in supported: return self.error(404,-32601,'Method not found; supported version 2026-07-28',rid)
        try:
            if req['method']=='ping': result={}
            else:
                if req['method'] in ('server/discover','events/list','tools/list') and clean.get('cursor') is not None: raise ValueError('Cursor unsupported')
                if req['method']=='events/subscribe' and hasattr(self.adapter,'subscribe_authorized'):
                    result=self.adapter.subscribe_authorized(principal,clean,h.get('authorization',''))
                else:result=self.adapter.rpc(principal,req['method'],clean)
            result={'resultType':'complete',**result,'_meta':{'io.modelcontextprotocol/serverInfo':{'name':'feishu-direct','version':'0.2'}}}
            if req['method'] in ('server/discover','tools/list'): result.update(ttlMs=60000,cacheScope='private')
            return self.respond(200,{'jsonrpc':'2.0','id':rid,'result':result})
        except CallbackVerificationError as error: return self.error(400,-32015,'Callback endpoint verification failed',rid,{'reason':error.reason})
        except PermissionError: return self.error(403,-32602,'Access rejected',rid)
        except (ValueError,TypeError,KeyError,AttributeError):
            if req['method']=='tools/call' and clean.get('name') in ('send_message','reply_to_message'):
                return self.respond(200,{'jsonrpc':'2.0','id':rid,'result':{'resultType':'complete','content':[{'type':'text','text':'Invalid tool arguments or idempotency key conflict.'}],'isError':True}})
            return self.error(400,-32602,'Invalid parameters or callback rejected',rid)
        except Exception:
            if req['method']=='tools/call':
                return self.respond(200,{'jsonrpc':'2.0','id':rid,'result':{'resultType':'complete','content':[{'type':'text','text':'Send failed. Retry using the same idempotency_key.'}],'isError':True}})
            return self.error(500,-32603,'Operation failed',rid)
    def __call__(self,environ,start_response):
        length=environ.get('CONTENT_LENGTH','0')
        try: size=int(length or '0')
        except ValueError: size=-1
        if not 0<=size<=262144: status,headers,body=self.respond(413)
        else:
            headers={k[5:].replace('_','-'):v for k,v in environ.items() if k.startswith('HTTP_')}
            headers['Content-Type']=environ.get('CONTENT_TYPE','')
            status,headers,body=self.handle(environ['REQUEST_METHOD'],environ['PATH_INFO'],headers,environ['wsgi.input'].read(size))
        from http import HTTPStatus
        start_response(f'{status} {HTTPStatus(status).phrase}',list(headers.items())+[('Content-Length',str(len(body)))])
        return [body]
