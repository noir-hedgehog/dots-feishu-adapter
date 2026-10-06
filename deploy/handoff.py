"""Opt-in single-request exact callback handoff. No pending signing secret storage."""
import hashlib
import json
import queue
import secrets
import socket
import threading
import time
from adapter import CallbackVerificationError
from deploy.network import SafeHTTPS
from deploy.tunnel import ApprovedCallback

class DeadlineHTTPS(SafeHTTPS):
    def __init__(self,hosts,deadline,check):
        super().__init__(hosts);self.deadline=deadline;self.check=check
    def remaining(self):
        self.check();left=self.deadline-time.monotonic()
        if left<=0:raise TimeoutError()
        return min(left,10)
    def addresses(self,host):
        result=queue.Queue(maxsize=1)
        def resolve():
            try:result.put((True,super(DeadlineHTTPS,self).addresses(host)))
            except Exception as error:result.put((False,error))
        threading.Thread(target=resolve,daemon=True).start()
        try:ok,value=result.get(timeout=self.remaining())
        except queue.Empty:raise TimeoutError() from None
        self.remaining()
        if not ok:raise value
        return value
    def request(self,url,method='GET',body=None,headers=None):
        # A deadline timer shuts down active IO; bounded DNS may finish later but cannot dial.
        sockets=[]
        def shutdown():
            for sock in list(sockets):
                try:sock.shutdown(socket.SHUT_RDWR)
                except OSError:pass
        timer=threading.Timer(max(0,self.deadline-time.monotonic()),shutdown);timer.daemon=True;timer.start()
        original=self.connector
        def connect(address,timeout):
            sock=original(address,timeout=self.remaining());sockets.append(sock);self.remaining();return sock
        self.connector=connect
        original_tls=self.tls
        class TLS:
            def wrap_socket(_,raw,server_hostname):
                raw.settimeout(self.remaining())
                secured=original_tls.wrap_socket(raw,server_hostname=server_hostname)
                sockets.append(secured);secured.settimeout(self.remaining());return secured
        self.tls=TLS()
        try:
            self.remaining();result=super().request(url,method,body,headers);self.remaining();return result
        finally:timer.cancel();shutdown()

class HandoffCallback(ApprovedCallback):
    def __init__(self,control,recheck,disconnected=lambda:False):
        super().__init__(control);self.recheck=recheck;self.disconnected=disconnected
        self.local=threading.local();self.admission=threading.Lock()
    def begin(self):
        if not self.admission.acquire(blocking=False):raise CallbackVerificationError('handoff_busy')
        self.local.context={'deadline':time.monotonic()+60,'wait_deadline':time.monotonic()+45,'nonce':secrets.token_hex(16),'key':None}
    def check(self):
        self.recheck()
        if self.disconnected() or time.monotonic()>=self.local.context['deadline']:raise CallbackVerificationError('handoff_abandoned')
    def transport(self,url):
        ctx=getattr(self.local,'context',None)
        if ctx is None:return super().transport(url)
        from urllib.parse import urlsplit
        if not isinstance(url,str) or len(url)>4096:raise ValueError('Invalid callback')
        host=urlsplit(url).hostname
        if not host:raise ValueError('Invalid callback')
        self.check();transport=DeadlineHTTPS([host],ctx['deadline'],self.check)
        transport.validate_url(url);transport.addresses(host);return transport
    def validate_url(self,url):
        ctx=getattr(self.local,'context',None)
        if ctx is None:return super().validate_url(url)
        self.check();self.transport(url)
        if self.control.callback(url):return
        key=hashlib.sha256(url.encode()).hexdigest()[:16];ctx['key']=key
        def announce(state):
            state['handoff']={'digest':key,'nonce':ctx['nonce'],'expires':time.time()+max(0,ctx['wait_deadline']-time.monotonic()),'status':'waiting'}
        self.control.update(announce)
        while time.monotonic()<ctx['wait_deadline']:
            self.check()
            if self.control.callback(url):return
            time.sleep(.1)
        raise CallbackVerificationError('handoff_timeout')
    def finish(self):
        ctx=getattr(self.local,'context',None)
        if ctx is None:return
        def clear(state):
            if state.get('handoff',{}).get('nonce')==ctx['nonce']:state['handoff']['status']='ended'
        try:self.control.update(clear)
        finally:self.local.context=None;self.admission.release()

def approve_live(control,digest,nonce):
    def apply(state):
        handoff=state.get('handoff',{})
        if handoff.get('digest')!=digest or handoff.get('nonce')!=nonce or handoff.get('status')!='waiting' or handoff.get('expires',0)<=time.time():raise ValueError('Request no longer waiting')
        if not state.get('personal_audience_confirmed') or not state.get('binding'):raise PermissionError()
        item=state.get('callback_pending',{}).get(digest)
        if not item or item['expires']<=control.clock():raise ValueError('Expired candidate')
        approved=state.setdefault('callback_approved',[])
        if item['url'] not in approved:
            if len(approved)>=16:raise ValueError('Approval capacity reached')
            approved.append(item['url'])
        del state['callback_pending'][digest]
        handoff['status']='approved'
    control.update(apply)
