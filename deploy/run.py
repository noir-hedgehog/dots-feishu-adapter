"""Explicit live startup only; no secrets in arguments/files/logging."""
import argparse
import getpass
import importlib.util
import json
import threading
from pathlib import Path
from urllib.parse import urlsplit
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server
from socketserver import ThreadingMixIn
from deploy.auth import OAuthVerifier
from deploy.feishu import FeishuSDK, delivery_worker, start_ws
from deploy.network import SafeHTTPS
from deploy.store import Store, DurableAdapter
from deploy.transport import HTTPApplication

class Server(ThreadingMixIn,WSGIServer):
    daemon_threads=True
class SilentHandler(WSGIRequestHandler):
    def log_message(self,*args): pass

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',default='deploy/config.example.json')
    parser.add_argument('--run',action='store_true',help='Explicitly enable approved live connections; prompts for new secrets')
    args=parser.parse_args()
    missing=[n for n in ('lark_oapi','cryptography') if importlib.util.find_spec(n) is None]
    if missing:
        print('Missing official dependencies: '+', '.join(missing)); return 2
    if not args.run:
        print('Dependencies present. Network and ports remain disabled without --run.'); return 0
    config=json.loads(Path(args.config).read_text())
    serialized=json.dumps(config)
    if 'REPLACE_' in serialized or '.example.' in serialized: raise ValueError('Complete non-secret configuration before live startup')
    resource=urlsplit(config['resource']); issuer=urlsplit(config['oauth_issuer'])
    if resource.scheme!='https' or resource.path!='/mcp' or issuer.scheme!='https': raise ValueError('HTTPS resource and issuer required')
    jwks=urlsplit(config['oauth_jwks_url'])
    if jwks.hostname!=issuer.hostname: raise ValueError('JWKS must belong to configured issuer')
    if not config['allowed_callback_hosts'] or not config['authorized_private_chat_id'] or not config['authorized_feishu_open_id']: raise ValueError('Explicit single-user binding required')
    callback=SafeHTTPS(config['allowed_callback_hosts'])
    verifier=OAuthVerifier(config['oauth_issuer'],config['resource'],config['authorized_oauth_subject'],config['oauth_jwks_url'],SafeHTTPS([issuer.hostname]))
    # User-controlled hidden prompts. Never inspect existing logins or old credentials.
    key=getpass.getpass('New/preserved encrypted-state key (Fernet base64; hidden): ').encode()
    app_secret=getpass.getpass('Replacement Feishu App Secret (hidden): ')
    store=Store.encrypted(config['state_path'],key)
    feishu=FeishuSDK.connect_client(config['feishu_app_id'],app_secret)
    adapter=DurableAdapter(store,owner=config['authorized_oauth_subject'],user=config['authorized_feishu_open_id'],chat=config['authorized_private_chat_id'],callback=callback,feishu=feishu)
    app=HTTPApplication(adapter,verifier,config['resource'],config['oauth_issuer'],config['allowed_origins'])
    server=make_server('127.0.0.1',config['local_port'],app,server_class=Server,handler_class=SilentHandler)
    stop=threading.Event()
    threading.Thread(target=server.serve_forever,daemon=True).start()
    threading.Thread(target=delivery_worker,args=(adapter,stop),daemon=True).start()
    try: start_ws(config['feishu_app_id'],app_secret,adapter)
    finally: stop.set(); server.shutdown(); store.close()

if __name__=='__main__':
    try: raise SystemExit(main())
    except Exception: raise SystemExit('Startup failed; verify approved configuration and dependencies. No diagnostic credentials are logged.')
