#!/usr/bin/env python3
"""Human-run nonsecret patch installer. Never reads credentials or starts services."""
import os
from pathlib import Path
import subprocess
import time
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from ops.apply_startup_fix import atomic_install
FILES=('adapter.py','deploy/store.py','deploy/tunnel.py','deploy/tunnel_runtime.py')
def main():
    if os.geteuid()!=0:raise ValueError('Root required')
    for unit in ('feishu-dot-adapter','feishu-dot-health.timer'):
        state=subprocess.check_output(['systemctl','show',unit,'-p','ActiveState','--value'],text=True).strip()
        if state not in ('inactive','failed'):raise ValueError('Stop adapter and health timer first')
    staged=Path(__file__).resolve().parent.parent;live=Path('/opt/apps/feishu-dot-adapter')
    for name in FILES:compile((staged/name).read_text(),name,'exec')
    backup=live/'pending'/('before-inbox-'+str(time.time_ns()))
    for name in FILES:atomic_install(live/name,backup/name)
    try:
        for name in FILES:atomic_install(staged/name,live/name)
    except Exception:
        for name in FILES:atomic_install(backup/name,live/name)
        raise
    print('Nonsecret inbox and diagnostic patch installed. Adapter remains stopped. No credentials read or changed.')
if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('Patch failed; services remain stopped. No secrets logged.')
