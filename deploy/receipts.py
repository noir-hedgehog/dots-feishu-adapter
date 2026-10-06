"""Persistent asynchronous Get receipt, not task completion. No automatic ambiguous retries."""
import hashlib
import threading
from adapter import encoded

EMOJI='Get'
class ReactionOutcome:
    def __init__(self,state,reaction_id=None):self.state,self.reaction_id=state,reaction_id

class ReceiptWorker:
    def __init__(self,adapter,app_id,stop):
        self.adapter,self.app_id,self.stop=adapter,app_id,stop
        self.thread=threading.Thread(target=self.run,daemon=True)
    def step(self):
        a=self.adapter
        with a.lock:
            if a.receipt_counters.get('permission_blocked'):return
            pending=next(((key,item) for key,item in a.receipts.items() if item['app_id']==self.app_id and item['state']=='pending'),None)
            if pending is None:return
            key,item=pending
            if a.check_authorization:
                try:authorized=a.check_authorization('')==a.owner
                except Exception:authorized=False
                if not authorized:
                    a.mutate(lambda:item.update(state='authorization_revoked'));return
            # Commit sending before API. Restarted sending becomes safe_unknown, never resent.
            a.mutate(lambda:item.update(state='sending'))
            mid=item['message_id']
        try:outcome=a.feishu.receipt(mid,EMOJI)
        except Exception:outcome=ReactionOutcome('safe_unknown')
        if outcome.state not in {'confirmed','permission_denied','rejected','safe_unknown'}:outcome=ReactionOutcome('safe_unknown')
        def commit():
            item.update(state=outcome.state)
            if outcome.state=='confirmed' and outcome.reaction_id:item['reaction_id']=outcome.reaction_id
            a.receipt_counters[outcome.state]=a.receipt_counters.get(outcome.state,0)+1
            if outcome.state=='permission_denied':
                a.receipt_counters['permission_blocked']=True
                for other in a.receipts.values():
                    if other['state']=='pending':other['state']='permission_blocked'
        with a.lock:a.mutate(commit)
    def run(self):
        while not self.stop.wait(.1):
            try:self.step()
            except Exception:
                # Storage failure: no text/exception output, don't flood persistence.
                if self.stop.wait(30):return

def queue_receipt(adapter,message_id):
    app_id=getattr(adapter,'receipt_app_id',None)
    if not app_id:return
    key=hashlib.sha256(encoded([app_id,message_id,EMOJI])).hexdigest()
    if key in adapter.receipts:return
    if adapter.receipt_counters.get('permission_blocked'):
        adapter.receipt_counters['skipped_permission']=adapter.receipt_counters.get('skipped_permission',0)+1;return
    if len(adapter.receipts)>=10000 or sum(x['state']=='pending' for x in adapter.receipts.values())>=100:
        adapter.receipt_counters['queue_full']=adapter.receipt_counters.get('queue_full',0)+1;return
    adapter.receipts[key]={'app_id':app_id,'message_id':message_id,'emoji':EMOJI,'state':'pending'}
