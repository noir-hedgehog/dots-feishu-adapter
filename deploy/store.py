"""Authenticated encrypted SQLite snapshot; key supplied by secure host at startup."""
import json
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
        super().__init__(**kwargs); self.store=store; self.lock=threading.RLock()
        state=store.load()
        if state:
            if state['binding']!=[self.owner,self.user,self.chat]: raise ValueError('State identity mismatch')
            self.subscriptions=state['subscriptions']; self.messages=state['messages']; self.sent=state['sent']
            self.outbox={tuple(k):v for k,v in state['outbox']}
    def snapshot(self):
        return {'binding':[self.owner,self.user,self.chat],'subscriptions':self.subscriptions,'messages':self.messages,'sent':self.sent,'outbox':[[list(k),v] for k,v in self.outbox.items()]}
    def mutate(self,fn,*args):
        with self.lock:
            old=json.loads(json.dumps(self.snapshot()))
            try:
                result=fn(*args); self.store.save(self.snapshot()); return result
            except Exception:
                self.subscriptions=old['subscriptions']; self.messages=old['messages']; self.sent=old['sent']; self.outbox={tuple(k):v for k,v in old['outbox']}
                raise
    def subscribe(self,*args): return self.mutate(super().subscribe,*args)
    def unsubscribe(self,*args): return self.mutate(super().unsubscribe,*args)
    def inject(self,*args):
        with self.lock:
            if len(self.messages)>=10000 and args[1].get('message_id') not in self.messages: raise ValueError('Message retention capacity reached')
            if len(self.outbox)>=10000: raise ValueError('Delivery queue capacity reached')
            return self.mutate(super().inject,*args)
    def drain(self): return self.mutate(super().drain)
    def send(self,*args): return self.mutate(super().send,*args)
