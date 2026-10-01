"""Authenticated encrypted SQLite snapshot; key supplied by secure host at startup."""
import json
import copy
import os
import sqlite3
import stat
import threading
from pathlib import Path
from adapter import Adapter

class Store:
    def __init__(self,path,cipher):
        path=Path(path)
        parent=path.parent
        parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        if parent.is_symlink() or stat.S_IMODE(parent.stat().st_mode)&0o077: raise ValueError('Private state directory required')
        if path.is_symlink(): raise ValueError('Symlink state file rejected')
        fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600); os.close(fd)
        if stat.S_IMODE(path.stat().st_mode)&0o077: raise ValueError('Private state file required')
        self.db=sqlite3.connect(path,check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=DELETE')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), payload BLOB NOT NULL)')
        self.cipher=cipher
    @classmethod
    def encrypted(cls,path,key):
        from cryptography.fernet import Fernet
        return cls(path,Fernet(key))
    def load(self):
        row=self.db.execute('SELECT payload FROM state WHERE id=1').fetchone()
        return json.loads(self.cipher.decrypt(row[0])) if row else None
    def save(self,state):
        payload=self.cipher.encrypt(json.dumps(state,separators=(',',':'),ensure_ascii=False).encode())
        with self.db:
            self.db.execute('INSERT INTO state VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',(payload,))
    def close(self): self.db.close()

class DurableAdapter(Adapter):
    def __init__(self,store,**kwargs):
        super().__init__(**kwargs); self.store=store; self.lock=threading.RLock(); self.network_lock=threading.Lock(); self.check_authorization=None
        state=store.load()
        if state:
            if state['binding']!=[self.owner,self.user,self.chat]: raise ValueError('State identity mismatch')
            self.subscriptions=state['subscriptions']; self.messages=state['messages']; self.sent=state['sent']
            self.outbox={tuple(k):v for k,v in state['outbox']}
            self.delivery_counts=state.get('delivery_counts',{})
    def snapshot(self):
        return {'binding':[self.owner,self.user,self.chat],'subscriptions':self.subscriptions,'messages':self.messages,'sent':self.sent,'outbox':[[list(k),v] for k,v in self.outbox.items()],'delivery_counts':self.delivery_counts}
    def mutate(self,fn,*args):
        with self.lock:
            old=json.loads(json.dumps(self.snapshot()))
            try:
                result=fn(*args); self.store.save(self.snapshot()); return result
            except Exception:
                self.delivery_counts=old['delivery_counts']; self.subscriptions=old['subscriptions']; self.messages=old['messages']; self.sent=old['sent']; self.outbox={tuple(k):v for k,v in old['outbox']}
                raise
    def subscribe(self,*args): return self.subscribe_authorized(*args)
    def subscribe_authorized(self,principal,params,token=None):
        args=(principal,params)
        with self.network_lock:
            with self.lock:clone=self.isolated()
            result=clone.subscribe(*args)
            if token is not None:clone.subscriptions[result['id']]['authorization']=token
            def commit():self.subscriptions[result['id']]=clone.subscriptions[result['id']]
            self.mutate(commit);return result
    def unsubscribe(self,*args): return self.mutate(super().unsubscribe,*args)
    def inject(self,*args):
        with self.lock:
            if len(self.messages)>=10000 and args[1].get('message_id') not in self.messages: raise ValueError('Message retention capacity reached')
            if len(self.outbox)>=10000: raise ValueError('Delivery queue capacity reached')
            return self.mutate(super().inject,*args)
    def isolated(self):
        # Snapshot under lock; network work operates on an independent adapter.
        clone=Adapter(owner=self.owner,user=self.user,chat=self.chat,callback=self.callback,feishu=self.feishu,clock=self.clock)
        for name in ('subscriptions','messages','outbox','sent','delivery_counts'):
            setattr(clone,name,copy.deepcopy(getattr(self,name)))
        return clone
    def revoke(self,principal):
        self.authorize(principal)
        def remove():
            self.subscriptions.clear(); self.outbox.clear()
        return self.mutate(remove)
    def drain(self):
        with self.network_lock:
            with self.lock: keys=list(self.outbox)
            for key in keys:
                with self.lock:
                    if key not in self.outbox:continue
                    clone=self.isolated(); original=copy.deepcopy(self.outbox[key]); sub=copy.deepcopy(self.subscriptions.get(key[0]))
                if self.check_authorization:
                    try: approved=self.check_authorization(sub.get('authorization',''))==self.owner
                    except Exception: approved=False
                    if not approved:
                        def disconnect():
                            if self.subscriptions.get(key[0])!=sub:return
                            self.subscriptions.pop(key[0],None)
                            for pending in list(self.outbox):
                                if pending[0]==key[0]: self.terminal(pending,'authorization_revoked')
                        self.mutate(disconnect);continue
                with self.lock:
                    if self.outbox.get(key)!=original or self.subscriptions.get(key[0])!=sub:continue
                clone_base_counts=copy.deepcopy(clone.delivery_counts)
                clone.drain_one(key)  # DNS/TLS/callback HTTP outside storage lock
                def commit():
                    if self.outbox.get(key)!=original or self.subscriptions.get(key[0])!=sub:return
                    if key in clone.outbox:self.outbox[key]=clone.outbox[key]
                    else:self.outbox.pop(key,None)
                    if key[0] not in clone.subscriptions:self.subscriptions.pop(key[0],None)
                    for reason,count in clone.delivery_counts.items():
                        delta=count-clone_base_counts.get(reason,0)
                        if delta:self.delivery_counts[reason]=self.delivery_counts.get(reason,0)+delta
                self.mutate(commit)
            with self.lock:return {'pending':len(self.outbox),'outcomes':dict(self.delivery_counts)}
    def send(self,*args):
        with self.network_lock:
            with self.lock:clone=self.isolated()
            result=clone.send(*args)  # provider IO outside storage lock
            def commit(): self.sent.update(clone.sent)
            self.mutate(commit)
            return result
