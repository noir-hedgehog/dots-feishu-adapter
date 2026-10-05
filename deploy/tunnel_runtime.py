"""Loopback runtime for expressly confirmed personal Tunnel audience."""
import json
import threading
from deploy.feishu import FeishuSDK, start_ws
from deploy.run import read_secret, make_server, Server, SilentHandler
from deploy.store import Store
from deploy.tunnel import Control, OWNER, ApprovedCallback, LocalAuthorization, TunnelAdapter, TunnelHTTP

class Bridge:
    owner = OWNER
    def __init__(self, control, holder): self.control, self.holder = control, holder
    def inject(self, principal, data):
        if not self.control.audience(): return
        adapter = self.holder.get('adapter')
        if adapter is None:
            self.control.candidate(data)
            return
        return adapter.inject(principal, data)

class Gateway:
    def __init__(self, holder): self.holder = holder
    def __call__(self, environ, start_response):
        client = self.holder.get('client')
        opened = getattr(getattr(getattr(client, '_conn', None), 'state', None), 'name', '') == 'OPEN'
        if environ['PATH_INFO'] in ('/healthz', '/readyz'):
            ready = opened and self.holder['monitor'].is_alive()
            adapter = self.holder.get('adapter')
            counters = {'subscriptions': 0, 'queued': 0}
            if adapter:
                with adapter.lock: counters.update(subscriptions=len(adapter.subscriptions), queued=len(adapter.outbox))
            payload = {'process': True, 'ws_open': opened, 'paired': adapter is not None, **counters}
            body = json.dumps(payload).encode()
            status = '200 OK' if environ['PATH_INFO'] == '/healthz' or ready else '503 Service Unavailable'
        elif self.holder.get('http'):
            return self.holder['http'](environ, start_response)
        else:
            body, status = b'{"status":"pairing_required"}', '503 Service Unavailable'
        start_response(status, [('Content-Type','application/json'), ('Cache-Control','no-store'), ('Content-Length',str(len(body)))])
        return [body]

def run(config, credential_dir):
    if config.get('auth_mode') != 'tunnel-single-user' or not credential_dir:
        raise ValueError('Explicit tunnel mode and protected credentials required')
    key = read_secret('state_key', '', credential_dir).encode()
    local_secret = read_secret('local_mcp_auth', '', credential_dir)
    app_secret = read_secret('feishu_app_secret', '', credential_dir)
    control_store = Store.encrypted(config['control_path'], key)
    control = Control(control_store)
    if not control.audience():
        control_store.close()
        raise PermissionError('Personal-only audience not confirmed locally')
    authorization = LocalAuthorization(local_secret, control)
    feishu = FeishuSDK.connect_client(config['feishu_app_id'], app_secret)
    holder, stop = {}, threading.Event()
    def monitor():
        while not stop.wait(1):
            client = holder.get('client')
            opened = getattr(getattr(getattr(client, '_conn', None), 'state', None), 'name', '') == 'OPEN'
            control.observe_ws(opened)
            binding = control.snapshot().get('binding')
            if binding and 'adapter' not in holder:
                if binding[0] != OWNER: raise PermissionError('Binding owner mismatch')
                store = Store.encrypted(config['state_path'], key)
                adapter = TunnelAdapter(store, owner=OWNER, user=binding[1], chat=binding[2], callback=ApprovedCallback(control), feishu=feishu)
                def check(_):
                    state = control.snapshot()
                    if state.get('personal_audience_confirmed') is not True or state.get('binding') != binding:
                        raise PermissionError('Personal transport authorization revoked')
                    return OWNER
                adapter.check_authorization = check
                holder['http'] = TunnelHTTP(adapter, authorization, 'http://127.0.0.1:8765/mcp', '', config.get('allowed_origins', ['https://chatgpt.com']))
                holder['adapter'] = adapter
            if holder.get('adapter'): holder['adapter'].drain()
    def safe_monitor():
        try: monitor()
        except Exception:
            print('Local state worker stopped; transport readiness will fail', flush=True)
    holder['monitor'] = threading.Thread(target=safe_monitor, daemon=True)
    holder['monitor'].start()
    server = make_server('127.0.0.1', config.get('local_port',8765), Gateway(holder), server_class=Server, handler_class=SilentHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        start_ws(config['feishu_app_id'], app_secret, Bridge(control, holder), on_client=lambda client: holder.update(client=client))
    finally:
        stop.set()
        server.shutdown()
        holder['monitor'].join(timeout=25)
        if not holder['monitor'].is_alive():
            if holder.get('adapter'): holder['adapter'].store.close()
            control_store.close()
