#!/usr/bin/env python3
"""Never run by agent. User-only combined approval, provisioning and activation."""
import getpass
import argparse
import json
import os
from pathlib import Path
import pwd
import secrets
import subprocess
from cryptography.fernet import Fernet
from deploy.run import read_secret
from deploy.store import Store
from deploy.tunnel import Control
from deploy.literal_env import read_literal_env,validate_identifier
from ops.tunnel_profile import render_profile,profile_tunnel_id

def validate_existing_target(config_path,profile,app_id,tunnel_id):
    # Existing encrypted pairing/state belongs to one application and Tunnel.
    # Never silently repoint it while reprovisioning credentials.
    if config_path.exists() and json.loads(config_path.read_text()).get('feishu_app_id')!=app_id:
        raise ValueError('Existing application binding differs; explicit migration required')
    if profile.exists() and profile_tunnel_id(profile.read_text())!=tunnel_id:
        raise ValueError('Existing Tunnel binding differs; explicit migration required')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--env-file', default='/etc/feishu-dot/.env', help='Protected literal .env file; never sourced')
    parser.add_argument('--prompt', action='store_true', help='Use hidden prompts instead of the .env file')
    args=parser.parse_args()
    if os.geteuid() != 0 or not os.isatty(0): raise ValueError('Root interactive terminal required')
    for unit in ('feishu-dot-adapter','feishu-dot-tunnel'):
        if subprocess.run(['systemctl','is-active','--quiet',unit]).returncode == 0:
            raise ValueError('Stop existing services explicitly before provisioning')
    os.umask(0o077)
    root=Path('/opt/apps/feishu-dot-adapter')
    base=Path('/etc/feishu-dot')
    credentials=base/'credentials'
    print('This personal mode treats EVERY effective Tunnel user as you. Confirm all Platform roles/principals, ChatGPT workspace associations, API keys and connected clients permitted to use this Tunnel belong only to you. It is unsafe for a shared audience.')
    print('Approval stores Feishu App Secret, Tunnel runtime key, generated local MCP secret and state encryption key on this host in root-only credential files. It enables unattended startup and starts both services now. Pairing still requires your separate local acceptance; no chat is auto-trusted. Widening Tunnel access requires immediate revoke.')
    if input('Type ENABLE PERSONAL TUNNEL to approve all of the above: ') != 'ENABLE PERSONAL TUNNEL': raise PermissionError('Not approved')
    # Collect before mutation; never reuse old chats, attachments or authentication stores.
    values={}
    if not args.prompt:
        _, env=read_literal_env(args.env_file)
        app_id,tunnel_id=env['FEISHU_APP_ID'],env['TUNNEL_ID']
        values={'feishu_app_secret':env['FEISHU_APP_SECRET'],'control_plane_api_key':env['CONTROL_PLANE_API_KEY']}
    else:
        app_id=validate_identifier(input('Your Feishu App ID: ').strip(),'cli_')
        tunnel_id=validate_identifier(input('Your Tunnel ID: ').strip(),'tunnel_')
        for name in ('feishu_app_secret','control_plane_api_key'):
            values[name]=getpass.getpass(name+' (hidden): ').strip()
            if not values[name]: raise ValueError('Empty credential')
    config_path=base/'config.json'
    profile=base/'tunnel/avalon-feishu.yaml'
    validate_existing_target(config_path,profile,app_id,tunnel_id)
    if input(f'Type CONFIRM {app_id} {tunnel_id} to select these exact targets: ').strip()!=f'CONFIRM {app_id} {tunnel_id}':
        raise PermissionError('Target identifiers not confirmed')
    credentials.mkdir(mode=0o700,parents=True,exist_ok=True)
    if credentials.is_symlink(): raise ValueError('Symlink directory rejected')
    for name,generator in [('state_key',lambda:Fernet.generate_key().decode()), ('local_mcp_auth',lambda:secrets.token_hex(32))]:
        if (credentials/name).exists(): read_secret(name,'',str(credentials))
        else: values[name]=generator()
    for name,value in values.items():
        fd=os.open(credentials/name,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'w') as target:
            target.write(value+'\n');target.flush();os.fsync(target.fileno())
    config=json.loads((root/'ops/config.tunnel.example.json').read_text())
    config['feishu_app_id']=app_id
    if config_path.exists() and not (base/'config.oauth.backup.json').exists():
        (base/'config.oauth.backup.json').write_bytes(config_path.read_bytes())
    config_path.write_text(json.dumps(config,indent=2)+'\n')
    store=Store.encrypted(config['control_path'],read_secret('state_key','',str(credentials)).encode())
    control=Control(store)
    control.update(lambda state:state.update(personal_audience_confirmed=True))
    store.close()
    account=pwd.getpwnam('ubuntu')
    os.chown(config['control_path'],account.pw_uid,account.pw_gid)
    for unit,source in [('feishu-dot-adapter','adapter-personal.conf'),('feishu-dot-tunnel','tunnel-personal.conf')]:
        destination=Path('/etc/systemd/system')/(unit+'.service.d')
        destination.mkdir(mode=0o755,exist_ok=True)
        path=destination/'personal.conf'
        path.write_bytes((root/'ops'/source).read_bytes());path.chmod(0o644)
    profile.parent.mkdir(mode=0o755,parents=True,exist_ok=True)
    profile.write_text(render_profile(tunnel_id));profile.chmod(0o644)
    (base/'approved').touch(mode=0o600)
    subprocess.run(['systemctl','daemon-reload'],check=True)
    subprocess.run(['systemctl','enable','feishu-dot-adapter','feishu-dot-tunnel','feishu-dot-health.timer'],check=True)
    subprocess.run(['systemctl','start','feishu-dot-adapter','feishu-dot-tunnel'],check=True)
    print('Services requested. Wait for actual WS OPEN, send a private text, then run the interactive pair command within 600 seconds. Readiness alone is not end-to-end completion.')

if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('Personal setup stopped; inspect only sanitized status. No credentials printed.')
