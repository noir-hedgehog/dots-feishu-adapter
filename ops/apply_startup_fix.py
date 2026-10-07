#!/usr/bin/env python3
"""User-run only. Apply staged nonsecret patch after explicit service stop; never starts."""
import os
from pathlib import Path
import subprocess
import tempfile
import sys

# Also support the documented direct-file invocation from a staged checkout.
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from ops.tunnel_profile import render_profile,profile_tunnel_id

FILES=('deploy/run.py','deploy/tunnel_runtime.py','deploy/tunnel.py','deploy/diagnostics.py','deploy/literal_env.py','ops/enable_personal.py','ops/tunnel_profile.py')

def atomic_install(source,destination):
    atomic_write(source.read_bytes(),destination)

def atomic_write(content,destination):
    destination.parent.mkdir(mode=0o755,parents=True,exist_ok=True)
    fd,path=tempfile.mkstemp(prefix='.startup-fix-',dir=destination.parent)
    try:
        os.fchmod(fd,0o644)
        with os.fdopen(fd,'wb') as target:
            target.write(content);target.flush();os.fsync(target.fileno())
        os.replace(path,destination)
    finally:
        if os.path.exists(path):os.unlink(path)

def main():
    if os.geteuid()!=0:raise ValueError('Root required')
    for unit in ('feishu-dot-adapter','feishu-dot-tunnel','feishu-dot-health.timer'):
        status=subprocess.check_output(['systemctl','show',unit,'-p','ActiveState','--value'],text=True).strip()
        if status not in ('inactive','failed'):raise ValueError('Explicitly stop services and watchdog first')
    staged=Path(__file__).resolve().parent.parent
    live=Path('/opt/apps/feishu-dot-adapter')
    # Validate and preserve the operator's nonsecret target before any mutation.
    profile=Path('/etc/feishu-dot/tunnel/avalon-feishu.yaml')
    rendered=render_profile(profile_tunnel_id(profile.read_text())).encode()
    for name in FILES:atomic_install(staged/name,live/name)
    backup=Path('/etc/feishu-dot/tunnel/avalon-feishu.before-startup-fix.yaml')
    if not backup.exists():atomic_install(profile,backup)
    atomic_write(rendered,profile)
    print('Nonsecret startup patch applied; services remain stopped. No credentials read or changed.')

if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('Patch not completed; confirm all three services are explicitly stopped. No secrets logged.')
