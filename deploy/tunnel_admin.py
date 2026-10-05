"""User-owned interactive local confirmations. No secret or message output."""
import argparse
import json
import os
from pathlib import Path
from deploy.run import read_secret
from deploy.store import Store
from deploy.tunnel import Control, ApprovedCallback

CONFIRM = 'PERSONAL TUNNEL ONLY'

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action', choices=['confirm-audience', 'pair', 'callback', 'reset-pairing', 'revoke'])
    args=parser.parse_args()
    if not os.isatty(0): raise ValueError('Interactive terminal required')
    config=json.loads(Path('/etc/feishu-dot/config.json').read_text())
    if config.get('auth_mode') != 'tunnel-single-user': raise ValueError('Explicit tunnel mode required')
    key=read_secret('state_key', '', '/etc/feishu-dot/credentials').encode()
    store=Store.encrypted(config['control_path'],key)
    control=Control(store)
    try:
        state=control.snapshot()
        if args.action == 'confirm-audience':
            print('Confirm every effective user/principal permitted to use this Tunnel is only you, including Platform organization roles, workspace associations, API keys and other attached clients. This mode cannot distinguish users. Widening access requires revocation.')
            if input('Type PERSONAL TUNNEL ONLY to confirm: ') != CONFIRM: raise PermissionError('Not confirmed')
            control.update(lambda s: s.update(personal_audience_confirmed=True))
        elif args.action == 'pair':
            for identifier, binding in state.get('candidates',{}).items():
                print(identifier, 'Feishu open_id=', repr(binding[0]), 'private chat_id=', repr(binding[1]))
            identifier=input('Candidate ID you independently recognize as your own private chat: ').strip()
            if input('Type ACCEPT PRIVATE CHAT to bind permanently: ') != 'ACCEPT PRIVATE CHAT': raise PermissionError('Not confirmed')
            control.accept_pair(identifier)
        elif args.action == 'callback':
            for identifier,item in state.get('callback_pending',{}).items():
                print(identifier, repr(item['url']))
            identifier=input('Pending callback ID verified as the URL from your current authenticated ChatGPT subscription: ').strip()
            item=state.get('callback_pending',{})[identifier]
            ApprovedCallback(control).transport(item['url']) # public HTTPS DNS validation again
            if input('Type ACCEPT CALLBACK to approve exactly this URL: ') != 'ACCEPT CALLBACK': raise PermissionError('Not confirmed')
            control.approve_callback(identifier)
            print('Retry events/subscribe from the same plugin; signed challenge is still required.')
        elif args.action == 'reset-pairing':
            if state.get('binding'): raise PermissionError('Existing pairing cannot be silently replaced')
            if state.get('pairing_deadline',0)>control.clock(): raise ValueError('Current window still active')
            if input('Type RESET PAIRING to request a new 600-second window after WS OPEN: ') != 'RESET PAIRING': raise PermissionError('Not confirmed')
            control.update(lambda s:s.update(pairing_started=False,candidates={},pairing_deadline=0))
        elif args.action == 'revoke':
            if input('Type REVOKE to stop authorizing all Tunnel calls and deliveries: ') != 'REVOKE': raise PermissionError('Not confirmed')
            control.update(lambda s:s.update(personal_audience_confirmed=False,callback_approved=[],callback_pending={}))
        print('Local confirmation saved. No services started.')
    finally: store.close()

if __name__=='__main__':
    try: main()
    except Exception: raise SystemExit('Local action rejected; check status, candidate and confirmation. No secrets logged.')
