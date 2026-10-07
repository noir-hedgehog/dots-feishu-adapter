"""Four-key literal env parser. No shell evaluation, expansion or interpolation."""
import os
import re
import stat

KEYS={'FEISHU_APP_ID','FEISHU_APP_SECRET','TUNNEL_ID','CONTROL_PLANE_API_KEY'}

def validate_identifier(value,prefix):
    if not isinstance(value,str) or not re.fullmatch(re.escape(prefix)+r'[A-Za-z0-9]{1,128}',value):
        raise ValueError('Replace identifier placeholders with your own application and Tunnel IDs')
    return value

def parse_literal_env(raw, require_secrets=True):
    if isinstance(raw,bytes):raw=raw.decode('utf-8')
    if len(raw.encode('utf-8'))>16384 or '\x00' in raw:raise ValueError('Invalid env file')
    values={}
    for line in raw.splitlines():
        line=line.strip()
        if not line or line.startswith('#'):continue
        if '=' not in line:raise ValueError('Expected literal KEY=value')
        key,value=line.split('=',1);key=key.strip();value=value.strip()
        if key not in KEYS or key in values:raise ValueError('Unknown or duplicate env key')
        if value.startswith(('"',"'")) or any(ord(c)<32 for c in value):raise ValueError('Use unquoted single-line literal values')
        values[key]=value
    if set(values)!=KEYS:raise ValueError('Missing required env keys')
    validate_identifier(values['FEISHU_APP_ID'],'cli_')
    validate_identifier(values['TUNNEL_ID'],'tunnel_')
    if require_secrets and any(not values[key] for key in ('FEISHU_APP_SECRET','CONTROL_PLANE_API_KEY')):raise ValueError('Fill both credentials locally')
    return values

def read_literal_env(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode)&0o077:raise ValueError('Private regular env file required (chmod 600)')
        with os.fdopen(fd,'rb') as source:
            fd=None;raw=source.read(16385)
        return raw,parse_literal_env(raw)
    finally:
        if fd is not None:os.close(fd)
