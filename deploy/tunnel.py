"""Explicit personal-only Tunnel mode. No OAuth identity or multi-tenant claim."""
import copy
import hashlib
import hmac
import json
import threading
import time
from urllib.parse import urlsplit
from adapter import CallbackVerificationError
from deploy.network import SafeHTTPS
from deploy.store import DurableAdapter
from deploy.transport import HTTPApplication

OWNER = 'avalon-personal'

class Control:
    """Encrypted local approvals; SQLite transactions serialize CLI and service."""
    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock
        self.lock = threading.RLock()
        store.db.execute('CREATE TABLE IF NOT EXISTS control (id INTEGER PRIMARY KEY CHECK(id=1), payload BLOB NOT NULL)')
    def update(self, fn):
        with self.lock:
            db = self.store.db
            db.execute('BEGIN IMMEDIATE')
            try:
                row = db.execute('SELECT payload FROM control WHERE id=1').fetchone()
                state = json.loads(self.store.cipher.decrypt(row[0])) if row else {}
                result = fn(state)
                payload = self.store.cipher.encrypt(json.dumps(state, separators=(',', ':')).encode())
                db.execute('INSERT INTO control VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (payload,))
                db.commit()
                return result
            except Exception:
                db.rollback()
                raise
    def snapshot(self):
        return self.update(lambda state: copy.deepcopy(state))
    def audience(self):
        return self.snapshot().get('personal_audience_confirmed') is True
    def observe_ws(self, opened):
        now = self.clock()
        def apply(state):
            state.update(ws_open=bool(opened), heartbeat=now)
            if opened and not state.get('binding') and not state.get('pairing_started'):
                state.update(pairing_started=True, pairing_deadline=now + 600, candidates={})
        self.update(apply)
    def candidate(self, data):
        # No message payload, signing keys or application secrets enter this record.
        if data.get('chat_type') != 'p2p' or data.get('sender_type') != 'user' or data.get('message_type') != 'text':
            return False
        user, chat = data.get('sender_id'), data.get('chat_id')
        if not isinstance(user, str) or not isinstance(chat, str) or not user or not chat or len(user) > 256 or len(chat) > 256:
            return False
        now = self.clock()
        def apply(state):
            if state.get('binding') or not state.get('ws_open') or now-state.get('heartbeat', 0) > 5 or now >= state.get('pairing_deadline', 0):
                return False
            key = hashlib.sha256(json.dumps([user, chat]).encode()).hexdigest()[:16]
            candidates = state.setdefault('candidates', {})
            if key not in candidates and len(candidates) >= 10: return False
            candidates[key] = [user, chat]
            state['candidate_count'] = state.get('candidate_count', 0) + 1
            return True
        return self.update(apply)
    def accept_pair(self, candidate_id):
        now = self.clock()
        def apply(state):
            if not state.get('personal_audience_confirmed') or state.get('binding'):
                raise PermissionError('Audience unconfirmed or already paired')
            if not state.get('ws_open') or now-state.get('heartbeat', 0) > 5 or now >= state.get('pairing_deadline', 0):
                raise ValueError('Pairing expired or WS not open')
            user, chat = state.get('candidates', {})[candidate_id]
            state['binding'] = [OWNER, user, chat]
            state['candidates'] = {}
        self.update(apply)
    def callback(self, url, approve=False):
        now = self.clock()
        key = hashlib.sha256(url.encode()).hexdigest()[:16]
        def apply(state):
            pending = state.setdefault('callback_pending', {})
            for old in list(pending):
                if pending[old]['expires'] <= now: del pending[old]
            approved = state.setdefault('callback_approved', [])
            if url in approved: return True
            if approve:
                raise ValueError('Approve an existing pending identifier through approve_callback')
            if key not in pending:
                if len(pending) >= 10: raise ValueError('Approval queue full')
                pending[key] = {'url': url, 'expires': now + 600}
            return False
        return self.update(apply)
    def approve_callback(self, candidate_id):
        def apply(state):
            if not state.get('personal_audience_confirmed') or not state.get('binding'): raise PermissionError('Not paired')
            item = state.get('callback_pending', {})[candidate_id]
            if item['expires'] <= self.clock(): raise ValueError('Approval expired; retry authenticated subscription')
            approved = state.setdefault('callback_approved', [])
            if len(approved) >= 16: raise ValueError('Approval capacity reached')
            if item['url'] not in approved: approved.append(item['url'])
            del state['callback_pending'][candidate_id]
        self.update(apply)

class ApprovedCallback:
    def __init__(self, control, factory=SafeHTTPS):
        self.control, self.factory = control, factory
        self.diagnostic_lock=threading.Lock()
        self.diagnostics={'challenge_attempts':0,'challenge_last_http_status':0}
    def diagnostic_snapshot(self):
        with self.diagnostic_lock:return dict(self.diagnostics)
    def transport(self, url):
        if not isinstance(url, str) or len(url) > 4096: raise ValueError('Invalid callback')
        host = urlsplit(url).hostname
        if not host: raise ValueError('Invalid callback')
        transport = self.factory([host])
        transport.validate_url(url)
        transport.addresses(host)  # public-only, before any pending record or contact
        return transport
    def validate_url(self, url):
        self.transport(url)
        if not self.control.callback(url): raise CallbackVerificationError('local_approval_pending')
    def post(self, url, body, headers):
        self.validate_url(url)
        verification=json.loads(body).get('type')=='verification'
        if verification:
            with self.diagnostic_lock:
                self.diagnostics['challenge_attempts']+=1
                self.diagnostics['challenge_last_http_status']=0
        status,response=self.transport(url).post(url, body, headers) # normal TLS, pinned IP, no redirects
        if verification:
            with self.diagnostic_lock:self.diagnostics['challenge_last_http_status']=status
        return status,response

class LocalAuthorization:
    def __init__(self, secret, control):
        if len(secret) < 40: raise ValueError('Strong local secret required')
        self.secret, self.control = secret, control
    def __call__(self, supplied):
        if not isinstance(supplied, str) or not hmac.compare_digest(supplied, self.secret) or not self.control.audience():
            raise PermissionError('Local authentication rejected')
        return OWNER

class TunnelAdapter(DurableAdapter):
    def authorize_read(self,principal):
        super().authorize_read(principal)
        # Personal mode's callback checks local audience/binding, not OAuth.
        # Recheck it at read time even if the HTTP secret gate already passed.
        if self.check_authorization and self.check_authorization('')!=principal:
            raise PermissionError('Personal transport authorization revoked')
    def subscribe_authorized(self, principal, params, token=None, authorization_expiry=None):
        # The fixed personal transport credential is not an OAuth token; never store it in subscription state.
        if not hasattr(self.callback,'begin'):return super().subscribe_authorized(principal,params)
        self.callback.begin()
        try:
            result=super().subscribe_authorized(principal,params)
            self.callback.check()
            return result
        except Exception:
            # If cancellation becomes visible immediately after commit, remove this experiment's subscription.
            if 'result' in locals():
                self.mutate(lambda:self.subscriptions.pop(result['id'],None))
            raise
        finally:self.callback.finish()

class TunnelHTTP(HTTPApplication):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.request_local=threading.local()
        self.telemetry_lock=threading.Lock()
        self.telemetry={'subscribe_requests':0,'subscribe_last_http_status':0,'subscribe_last_rpc_code':0,'subscribe_last_reason':'none','mcp_authenticated_requests':0,'mcp_tools_call_requests':0,'last_mcp_request_at':0}
    def __call__(self,environ,start_response):
        self.request_local.disconnected=environ.get('feishu.disconnected',lambda:False)
        try:return super().__call__(environ,start_response)
        finally:self.request_local.disconnected=lambda:False
    def disconnected(self):return getattr(self.request_local,'disconnected',lambda:False)()
    def handle(self, method, path, headers, body):
        h = {k.lower(): v for k,v in headers.items()}
        if path == '/.well-known/oauth-protected-resource': return self.respond(404)
        if path != '/mcp': return self.respond(404)
        try: self.authenticate(h.get('x-feishu-local-auth', ''))
        except Exception: return self.respond(403)
        with self.telemetry_lock:
            self.telemetry['mcp_authenticated_requests']+=1
            self.telemetry['last_mcp_request_at']=int(time.time())
            try:
                request=json.loads(body)
                if isinstance(request,dict) and request.get('method')=='tools/call':self.telemetry['mcp_tools_call_requests']+=1
            except Exception:pass
        # Reuse protocol validation, without accepting external bearer identity.
        h['authorization'] = h['x-feishu-local-auth']
        response=super().handle(method, path, h, body)
        try:
            request=json.loads(body)
            if isinstance(request,dict) and request.get('method')=='events/subscribe':
                result=json.loads(response[2]); error=result.get('error',{})
                reason=error.get('data',{}).get('reason','none')
                allowed={'none','local_approval_pending','destination_rejected','timeout','connection_failed','challenge_failed','handoff_busy','handoff_timeout','handoff_abandoned'}
                code=error.get('code',0)
                from deploy.observability import emit
                emit('subscription',reason if error else 'confirmed',http_status=response[0],rpc_code=code)
                with self.telemetry_lock:
                    self.telemetry.update(subscribe_requests=self.telemetry['subscribe_requests']+1,
                        subscribe_last_http_status=response[0],subscribe_last_rpc_code=code if code in (0,-32600,-32601,-32602,-32700,-32015,-32020,-32022,-32603) else -1,
                        subscribe_last_reason=reason if reason in allowed else 'other')
        except Exception:pass
        return response
