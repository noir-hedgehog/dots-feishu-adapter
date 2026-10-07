"""User-owned interactive local confirmations. No secret or message output."""
import argparse
import json
import os
import time
from pathlib import Path
from deploy.run import read_secret
from deploy.store import Store
from deploy.tunnel import Control, ApprovedCallback

CONFIRM = 'PERSONAL TUNNEL ONLY'

def confirmation(prompt,expected):
    return input(prompt).strip()==expected

class LocalActionError(ValueError):
    def __init__(self,code):
        if code not in {'callback_expired','callback_missing','not_paired'}:code='callback_missing'
        self.code=code
        super().__init__(code)

def valid_pending_callback(control,identifier):
    state=control.snapshot()
    if state.get('personal_audience_confirmed') is not True or not state.get('binding'):
        raise LocalActionError('not_paired')
    item=state.get('callback_pending',{}).get(identifier)
    if item is None:raise LocalActionError('callback_missing')
    if item['expires']<=control.clock():raise LocalActionError('callback_expired')
    return item

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action', choices=['confirm-audience', 'pair', 'callback', 'reset-pairing', 'revoke', 'watch-callback'])
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
        elif args.action == 'watch-callback':
            if config.get('callback_handoff_experiment') is not True:raise ValueError('Experiment not enabled')
            from deploy.handoff import approve_live
            print('READY: now ask ChatGPT to issue ONE CREATE. Waiting up to 300 seconds for a fresh in-flight request. No approval is automatic.')
            deadline=time.monotonic()+300
            while time.monotonic()<deadline:
                current=control.snapshot();handoff=current.get('handoff',{})
                if handoff.get('status')=='waiting' and handoff.get('expires',0)>time.time():break
                time.sleep(.2)
            else:raise ValueError('No live request arrived')
            identifier=handoff['digest'];nonce=handoff['nonce']
            item=valid_pending_callback(control,identifier)
            print('Exact live callback:',identifier,repr(item['url']))
            print('Seconds remaining:',max(0,int(handoff['expires']-time.time())))
            if input('Verify this is your current authenticated ChatGPT request; type ACCEPT CALLBACK: ').strip()!='ACCEPT CALLBACK':raise PermissionError('Not confirmed')
            # Transaction rechecks the nonce and live expiry after human input; stale candidates cannot pass.
            approve_live(control,identifier,nonce)
            print('Exact callback approved for the original request. Do NOT issue a second CREATE. Signed verification is still required.')
        elif args.action == 'callback':
            pending=state.get('callback_pending',{})
            fresh=False
            for identifier,item in pending.items():
                remaining=max(0,int(item['expires']-control.clock()))
                if remaining:
                    fresh=True
                    print(identifier, 'seconds_remaining=',remaining,repr(item['url']))
                else:print(identifier,'EXPIRED: retry authenticated subscription to obtain a fresh candidate')
            if not pending:raise LocalActionError('callback_missing')
            if not fresh:raise LocalActionError('callback_expired')
            identifier=input('Pending callback ID verified as the URL from your current authenticated ChatGPT subscription: ').strip()
            item=valid_pending_callback(control,identifier)
            ApprovedCallback(control).transport(item['url']) # public HTTPS DNS validation again
            if input('Type ACCEPT CALLBACK to approve exactly this URL: ') != 'ACCEPT CALLBACK': raise PermissionError('Not confirmed')
            valid_pending_callback(control,identifier)
            control.approve_callback(identifier)
            print('Retry events/subscribe from the same plugin; signed challenge is still required.')
        elif args.action == 'reset-pairing':
            if state.get('binding'): raise PermissionError('Existing pairing cannot be silently replaced')
            if state.get('pairing_deadline',0)>control.clock(): raise ValueError('Current window still active')
            if not confirmation('Type RESET PAIRING to request a new 600-second window after WS OPEN: ', 'RESET PAIRING'): raise PermissionError('Not confirmed')
            control.update(lambda s:s.update(pairing_started=False,candidates={},pairing_deadline=0))
        elif args.action == 'revoke':
            if input('Type REVOKE to stop authorizing all Tunnel calls and deliveries: ') != 'REVOKE': raise PermissionError('Not confirmed')
            control.update(lambda s:s.update(personal_audience_confirmed=False,callback_approved=[],callback_pending={}))
        print('Local confirmation saved. No services started.')
    finally: store.close()

if __name__=='__main__':
    try: main()
    except LocalActionError as error:
        advice={'callback_expired':'Candidate expired after 600 seconds. Retry the authenticated subscription, then approve the fresh candidate promptly.',
                'callback_missing':'No matching pending candidate. Obtain a fresh authenticated subscription request.',
                'not_paired':'Personal audience and pairing must be confirmed first.'}
        raise SystemExit('Local action rejected: '+advice[error.code])
    except Exception: raise SystemExit('Local action rejected; check status, candidate and confirmation. No secrets logged.')
