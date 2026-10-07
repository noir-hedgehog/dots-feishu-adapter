"""No message bodies, destination URLs, principal IDs or exception text in operational logs."""
import json
from datetime import datetime
EVENTS={'message_admitted','subscription','event_delivery','reply','receipt','worker'}
OUTCOMES={'accepted','confirmed','delivered','pending','safe_unknown','permission_denied','permission_blocked','rejected','authorization_revoked','queue_full','timeout','local_approval_pending','destination_rejected','connection_failed','challenge_failed','handoff_busy','handoff_timeout','handoff_abandoned','none','expired','unknown'}
def emit(event,outcome,**numbers):
    record={'event':event if event in EVENTS else 'worker','outcome':outcome if outcome in OUTCOMES else 'unknown'}
    for key in ('http_status','rpc_code','count','pending','latency_ms'):
        value=numbers.get(key)
        if type(value) is int and -100000<=value<=1000000000:record[key]=value
    try:print(json.dumps(record,separators=(',',':')),flush=True)
    except (OSError,ValueError):pass # Diagnostic IO must not fail committed admission/delivery.

def health_metrics(adapter,now):
    with adapter.lock:
        expirations=[s['expires'] for s in adapter.subscriptions.values() if s.get('expires',0)>now]
        oldest=0
        for item in adapter.outbox.values():
            try:age=max(0,now-datetime.fromisoformat(item['event']['timestamp']).timestamp());oldest=max(oldest,age)
            except (KeyError,ValueError,TypeError):pass
        replies=[s.get('reply',{}) for s in adapter.message_status.values()]
        return {'subscription_remaining_seconds':max(0,int(min(expirations)-now)) if expirations else 0,
                'expired_subscriptions':len(adapter.subscriptions)-len(expirations),
                'events_ready':bool(expirations),'queue_oldest_age_seconds':int(oldest),
                'queue_delayed':oldest>60,'reply_confirmed':sum(s.get('confirmed_count',0) for s in replies),
                'reply_unknown':sum(s.get('unknown_count',0) for s in replies),
                'reply_rejected':sum(s.get('rejected_count',0) for s in replies),
                'delivery_failed':sum(v for k,v in adapter.delivery_counts.items() if k!='delivered')}
