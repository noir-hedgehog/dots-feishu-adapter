"""Loopback runtime for expressly confirmed personal Tunnel audience."""
import json
import threading
import time
import select
import socket
from deploy.feishu import FeishuSDK, start_ws
from deploy.run import read_secret, make_server, Server, SilentHandler
from deploy.store import Store
from deploy.diagnostics import phase,report
from deploy.tunnel import Control, OWNER, ApprovedCallback, LocalAuthorization, TunnelAdapter, TunnelHTTP

class HandoffHandler(SilentHandler):
    def get_environ(self):
        environ=super().get_environ()
        def disconnected():
            try:
                ready,_,_=select.select([self.connection],[],[],0)
                return bool(ready and self.connection.recv(1,socket.MSG_PEEK|socket.MSG_DONTWAIT)==b'')
            except (OSError,ValueError):return True
        environ['feishu.disconnected']=disconnected
        return environ

class Bridge:
    owner = OWNER
    def __init__(self, control, holder): self.control, self.holder = control, holder
    def inject(self, principal, data):
        self.holder['sdk_received_events']=self.holder.get('sdk_received_events',0)+1
        self.holder['last_sdk_event_at']=int(time.time())
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
            payload = {'process': True,'state_worker_alive':self.holder['monitor'].is_alive(),
                       'receipt_worker_alive':bool(self.holder.get('receipts') and self.holder['receipts'].thread.is_alive()), 'ws_open': opened, 'paired': adapter is not None, **counters,
                       'sdk_received_events': self.holder.get('sdk_received_events',0),
                       'last_sdk_event_at': self.holder.get('last_sdk_event_at',0),
                       **self.holder.get('control_status',{})}
            if adapter:
                with adapter.lock:
                    payload['retained_messages']=len(adapter.messages)
                    payload['receipt_enabled']=bool(adapter.receipt_app_id)
                    payload['receipt_pending']=sum(x['state']=='pending' for x in adapter.receipts.values())
                    payload['receipt_confirmed']=adapter.receipt_counters.get('confirmed',0)
                    payload['receipt_unknown']=sum(x['state']=='safe_unknown' for x in adapter.receipts.values())
                    payload['receipt_permission_blocked']=bool(adapter.receipt_counters.get('permission_blocked'))
                    payload['receipt_rejected']=adapter.receipt_counters.get('rejected',0)
                    payload['callback_delivered']=adapter.delivery_counts.get('delivered',0)
                    payload['active_subscriptions']=sum(sub.get('expires',0)>time.time() for sub in adapter.subscriptions.values())
                with self.holder['http'].telemetry_lock:
                    payload.update(self.holder['http'].telemetry)
                payload.update(adapter.callback.diagnostic_snapshot())
                from deploy.observability import health_metrics
                payload.update(health_metrics(adapter,time.time()))
            body = json.dumps(payload).encode()
            status = '200 OK' if environ['PATH_INFO'] == '/healthz' or ready else '503 Service Unavailable'
        elif self.holder.get('http'):
            return self.holder['http'](environ, start_response)
        else:
            body, status = b'{"status":"pairing_required"}', '503 Service Unavailable'
        start_response(status, [('Content-Type','application/json'), ('Cache-Control','no-store'), ('Content-Length',str(len(body)))])
        return [body]

def run(config, credential_dir):
    with phase('config_validate'):
        if config.get('auth_mode') != 'tunnel-single-user' or not credential_dir:
            raise ValueError('Explicit tunnel mode and protected credentials required')
    with phase('credential_load'):
        key = read_secret('state_key', '', credential_dir).encode()
        local_secret = read_secret('local_mcp_auth', '', credential_dir)
        app_secret = read_secret('feishu_app_secret', '', credential_dir)
    with phase('control_load'):
        control_store = Store.encrypted(config['control_path'], key)
        control = Control(control_store)
    with phase('audience_check'):
        if not control.audience():
            control_store.close()
            raise PermissionError('Personal-only audience not confirmed locally')
    with phase('local_auth_validate'):
        authorization = LocalAuthorization(local_secret, control)
    with phase('feishu_client_build'):
        feishu = FeishuSDK.connect_client(config['feishu_app_id'], app_secret)
    holder, stop = {}, threading.Event()
    def monitor():
        while not stop.wait(1):
            client = holder.get('client')
            opened = getattr(getattr(getattr(client, '_conn', None), 'state', None), 'name', '') == 'OPEN'
            control.observe_ws(opened)
            state=control.snapshot()
            binding = state.get('binding')
            handoff=state.get('handoff',{})
            holder['control_status']={'handoff_waiting':handoff.get('status')=='waiting' and handoff.get('expires',0)>time.time(),
                                      'handoff_seconds_remaining':max(0,int(handoff.get('expires',0)-time.time())),
                                      'approved_callback_count':len(state.get('callback_approved',[])),
                                      'pending_callback_count':sum(item.get('expires',0)>time.time() for item in state.get('callback_pending',{}).values()),
                                      'pairing_candidates':len(state.get('candidates',{})),
                                      'pairing_seconds_remaining':max(0,int(state.get('pairing_deadline',0)-time.time())),
                                      'binding_confirmed':bool(binding),
                                      'audience_confirmed':state.get('personal_audience_confirmed') is True,
                                      'owner_is_expected':bool(binding and binding[0]==OWNER)}
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
                if config.get('callback_handoff_experiment') is True:
                    from deploy.handoff import HandoffCallback
                    adapter.callback=HandoffCallback(control,lambda:check(''),lambda:holder['http'].disconnected())
                holder['http'] = TunnelHTTP(adapter, authorization, 'http://127.0.0.1:8765/mcp', '', config.get('allowed_origins', ['https://chatgpt.com']))
                if config.get('received_reaction_enabled') is True:
                    from deploy.receipts import ReceiptWorker
                    adapter.receipt_app_id=config['feishu_app_id']
                    holder['receipts']=ReceiptWorker(adapter,config['feishu_app_id'],stop)
                    holder['receipts'].thread.start()
                holder['adapter'] = adapter
            if holder.get('adapter'): holder['adapter'].drain()
    def safe_monitor():
        try: monitor()
        except Exception as error:
            report(error,'state_worker')
    holder['monitor'] = threading.Thread(target=safe_monitor, daemon=True)
    holder['monitor'].start()
    with phase('http_bind'):
        server = make_server('127.0.0.1', config.get('local_port',8765), Gateway(holder), server_class=Server, handler_class=HandoffHandler if config.get('callback_handoff_experiment') is True else SilentHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with phase('feishu_ws_connect'):
            start_ws(config['feishu_app_id'], app_secret, Bridge(control, holder), on_client=lambda client: holder.update(client=client))
    finally:
        stop.set()
        server.shutdown()
        holder['monitor'].join(timeout=25)
        if holder.get('receipts'):holder['receipts'].thread.join(timeout=12)
        if not holder['monitor'].is_alive() and (not holder.get('receipts') or not holder['receipts'].thread.is_alive()):
            if holder.get('adapter'): holder['adapter'].store.close()
            control_store.close()
