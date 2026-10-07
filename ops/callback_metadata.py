#!/usr/bin/env python3
"""Human-run read-only encrypted-state summary. No approval, network, or writes."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import time
from cryptography.fernet import Fernet

def digest(url):return hashlib.sha256(url.encode()).hexdigest()[:16]

def summary(state,now):
    pending=state.get('callback_pending',{})
    approved=state.get('callback_approved',[])
    approved_digests=sorted({digest(url) for url in approved})
    pending_rows=[{'digest':digest(item['url']),'expired':item['expires']<=now,'seconds_remaining':max(0,int(item['expires']-now))} for item in pending.values()]
    return {'pending_count':len(pending_rows),'pending':pending_rows,'approved_count':len(approved_digests),'approved_digests':approved_digests,'overlap_digests':sorted({row['digest'] for row in pending_rows}&set(approved_digests))}

def main():
    if os.geteuid()!=0:raise ValueError('User must run locally through sudo')
    path=Path('/etc/feishu-dot/credentials/state_key')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as source:
        info=os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode)&0o077:raise ValueError('Protected state key required')
        key=source.read(256).strip()
    db=sqlite3.connect('file:/var/lib/feishu-dot/control.sqlite3?mode=ro',uri=True)
    try:
        row=db.execute('SELECT payload FROM control WHERE id=1').fetchone()
        state=json.loads(Fernet(key).decrypt(row[0])) if row else {}
        print(json.dumps(summary(state,time.time()),separators=(',',':')))
    finally:db.close()

if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('Metadata unavailable; no secrets or URLs output.')
