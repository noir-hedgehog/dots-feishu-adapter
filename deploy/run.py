"""Explicit live startup only; no secrets in arguments/files/logging."""
import argparse
import getpass
import importlib.util
import json
import stat
import threading
from pathlib import Path
from urllib.parse import urlsplit
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server
from socketserver import ThreadingMixIn
from deploy.auth import OAuthVerifier, IntrospectionAuthorization
from deploy.feishu import FeishuSDK, delivery_worker, start_ws
from deploy.network import SafeHTTPS
from deploy.store import Store, DurableAdapter
from deploy.transport import HTTPApplication

class Server(ThreadingMixIn,WSGIServer):
    daemon_threads=True
class SilentHandler(WSGIRequestHandler):
    def log_message(self,*args): pass

def read_secret(name, prompt, directory=None):
    if directory is None:
        return getpass.getpass(prompt)
    path = Path(directory) / name
    if path.is_symlink() or not path.is_file():
        raise ValueError('Missing protected credential')
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError('Credential permissions must be private')
    value = path.read_text().strip()
    if not value:
        raise ValueError('Empty credential')
    return value

class HealthApplication:
    def __init__(self, app, adapter, ws_holder, worker):
        self.app, self.adapter, self.ws_holder, self.worker = app, adapter, ws_holder, worker
    def __call__(self, environ, start_response):
        if environ['PATH_INFO'] not in ('/healthz', '/readyz'):
            return self.app(environ, start_response)
        client = self.ws_holder.get('client')
        conn = getattr(client, '_conn', None)
        ws_open = getattr(getattr(conn, 'state', None), 'name', None) == 'OPEN'
        with self.adapter.lock:
            subscriptions = len(self.adapter.subscriptions)
            queued = len(self.adapter.outbox)
        ready = ws_open and self.worker.is_alive()
        # Readiness is transport-only; no claim of subscription or end-to-end success.
        status = '200 OK' if environ['PATH_INFO'] == '/healthz' or ready else '503 Service Unavailable'
        body = json.dumps({'process': True, 'ws_open': ws_open,
                           'delivery_worker': self.worker.is_alive(),
                           'subscriptions': subscriptions, 'queued': queued}).encode()
        start_response(status, [('Content-Type', 'application/json'), ('Cache-Control', 'no-store'), ('Content-Length', str(len(body)))])
        return [body]

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',default='deploy/config.example.json')
    parser.add_argument('--run',action='store_true',help='Explicitly enable approved live connections; prompts for new secrets')
    parser.add_argument('--auth-mode', choices=['oauth', 'tunnel-single-user'], default='oauth')
    parser.add_argument('--credential-dir', help='Protected directory, normally systemd CREDENTIALS_DIRECTORY')
    args=parser.parse_args()
    missing=[n for n in ('lark_oapi','cryptography') if importlib.util.find_spec(n) is None]
    if missing:
        print('Missing official dependencies: '+', '.join(missing)); return 2
    if not args.run:
        print('Dependencies present. Network and ports remain disabled without --run.'); return 0
    config=json.loads(Path(args.config).read_text())
    if args.auth_mode == 'tunnel-single-user':
        from deploy.tunnel_runtime import run
        run(config, args.credential_dir)
        return 0
    serialized=json.dumps(config)
    if 'REPLACE_' in serialized or '.example.' in serialized: raise ValueError('Complete non-secret configuration before live startup')
    resource=urlsplit(config['resource']); issuer=urlsplit(config['oauth_issuer'])
    if resource.scheme!='https' or resource.path!='/mcp' or issuer.scheme!='https': raise ValueError('HTTPS resource and issuer required')
    jwks=urlsplit(config['oauth_jwks_url'])
    if jwks.hostname!=issuer.hostname: raise ValueError('JWKS must belong to configured issuer')
    if not config['allowed_callback_hosts'] or not config['authorized_private_chat_id'] or not config['authorized_feishu_open_id']: raise ValueError('Explicit single-user binding required')
    callback=SafeHTTPS(config['allowed_callback_hosts'])
    verifier=OAuthVerifier(config['oauth_issuer'],config['resource'],config['authorized_oauth_subject'],config['oauth_jwks_url'],SafeHTTPS([issuer.hostname]))
    if urlsplit(config['oauth_introspection_url']).hostname!=issuer.hostname: raise ValueError('Introspection must belong to issuer')
    # User-controlled hidden prompts. Never inspect existing logins or old credentials.
    key=read_secret('state_key', 'New/preserved encrypted-state key (Fernet base64; hidden): ', args.credential_dir).encode()
    app_secret=read_secret('feishu_app_secret', 'Replacement Feishu App Secret (hidden): ', args.credential_dir)
    introspection_credential=read_secret('oauth_introspection', 'Approved OAuth introspection resource credential (hidden): ', args.credential_dir)
    authorization=IntrospectionAuthorization(verifier,config['oauth_introspection_url'],introspection_credential,SafeHTTPS([issuer.hostname]))
    store=Store.encrypted(config['state_path'],key)
    feishu=FeishuSDK.connect_client(config['feishu_app_id'],app_secret)
    adapter=DurableAdapter(store,owner=config['authorized_oauth_subject'],user=config['authorized_feishu_open_id'],chat=config['authorized_private_chat_id'],callback=callback,feishu=feishu)
    adapter.check_authorization=authorization
    app=HTTPApplication(adapter,authorization,config['resource'],config['oauth_issuer'],config['allowed_origins'])
    stop=threading.Event()
    worker=threading.Thread(target=delivery_worker,args=(adapter,stop),daemon=True)
    holder={}
    app=HealthApplication(app,adapter,holder,worker)
    server=make_server('127.0.0.1',config['local_port'],app,server_class=Server,handler_class=SilentHandler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    worker.start()
    try: start_ws(config['feishu_app_id'],app_secret,adapter,on_client=lambda client: holder.update(client=client))
    finally: stop.set(); server.shutdown(); store.close()

if __name__=='__main__':
    try: raise SystemExit(main())
    except Exception: raise SystemExit('Startup failed; verify approved configuration and dependencies. No diagnostic credentials are logged.')
