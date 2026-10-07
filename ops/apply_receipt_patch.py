#!/usr/bin/env python3
"""Human-run nonsecret experiment installer. Never starts services or reads credentials."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from ops.apply_startup_fix import atomic_install
FILES=('deploy/receipts.py','deploy/feishu.py','deploy/store.py','deploy/tunnel_runtime.py')
def main():
    if os.geteuid()!=0:raise ValueError('Root required')
    for unit in ('feishu-dot-adapter','feishu-dot-health.timer'):
        state=subprocess.check_output(['systemctl','show',unit,'-p','ActiveState','--value'],text=True).strip()
        if state not in ('inactive','failed'):raise ValueError('Stop adapter and timer first')
    staged=Path(__file__).resolve().parent.parent;live=Path('/opt/apps/feishu-dot-adapter');config=Path('/etc/feishu-dot/config.json')
    data=json.loads(config.read_text())
    if data.get('auth_mode')!='tunnel-single-user':raise ValueError('Personal mode required')
    if sys.argv[1:]==['--disable']:
        data['received_reaction_enabled']=False
        temp=staged/'disabled-config.json';temp.write_text(json.dumps(data,indent=2)+'\n');atomic_install(temp,config);temp.unlink()
        print('Receipt feature disabled in nonsecret config; adapter remains stopped.');return
    if sys.argv[1:]:raise ValueError('Unknown argument')
    for name in FILES:compile((staged/name).read_text(),name,'exec')
    backup=live/'pending'/('before-receipts-'+str(time.time_ns()))
    existing=[name for name in FILES if (live/name).exists()]
    for name in existing:atomic_install(live/name,backup/name)
    atomic_install(config,backup/'config.json')
    data['received_reaction_enabled']=True
    rendered=staged/'enabled-config.json';rendered.write_text(json.dumps(data,indent=2)+'\n')
    try:
        for name in FILES:atomic_install(staged/name,live/name)
        atomic_install(rendered,config)
    except Exception:
        for name in existing:atomic_install(backup/name,live/name)
        atomic_install(backup/'config.json',config);raise
    finally:rendered.unlink(missing_ok=True)
    print('Nonsecret bounded Get receipt feature enabled; adapter remains stopped. No credentials read or changed.')
if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('Patch failed; do not start until diagnosed. No secrets logged.')
