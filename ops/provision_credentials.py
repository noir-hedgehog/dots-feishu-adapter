#!/usr/bin/env python3
"""User-run only, after approval to persist these secrets on Avalon."""
import getpass
import os
from pathlib import Path
from cryptography.fernet import Fernet

if os.geteuid() != 0:
    raise SystemExit('Run through sudo on Avalon in your own interactive terminal.')
if not os.isatty(0):
    raise SystemExit('Interactive terminal required; do not pipe secrets.')
os.umask(0o077)
base = Path('/etc/feishu-dot/credentials')
base.mkdir(mode=0o700, parents=True, exist_ok=True)
if base.is_symlink():
    raise SystemExit('Symlink directory rejected')
values = {}
for name in ('feishu_app_secret', 'oauth_introspection', 'control_plane_api_key'):
    value = getpass.getpass(name + ' (hidden): ').strip()
    if not value:
        raise SystemExit('Empty credential; nothing written')
    values[name] = value
# Preserve state key without reading or printing it. Rotation requires state migration.
if not (base / 'state_key').exists():
    values['state_key'] = Fernet.generate_key().decode()
for name, value in values.items():
    path = base / name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as target:
        target.write(value + '\n')
        target.flush()
        os.fsync(target.fileno())
print('Protected credentials installed; no services activated. Keep the state key for restarts.')
