#!/usr/bin/env python3
"""Read existing logs but emit only structured allowlisted fields, never raw lines."""
import argparse
from collections import deque
import json
from pathlib import Path
from deploy.observability import EVENTS,OUTCOMES
from deploy.diagnostics import STAGES
CATEGORIES={'unknown','permission_or_authorization_rejected','required_file_missing','dependency_missing','permission_denied','read_only_filesystem','port_in_use','storage_full','os_or_network_error','configuration_json_invalid','configuration_or_credential_validation_failed','encrypted_state_key_mismatch','feishu_endpoint_rejected','feishu_endpoint_unavailable','feishu_network_unreachable','feishu_ws_closed','feishu_handshake_header_missing'}
def sanitize(record):
    if not isinstance(record,dict):return None
    if record.get('event')=='adapter_error':
        if record.get('stage') not in STAGES or record.get('category') not in CATEGORIES:return None
        result={k:record[k] for k in ('event','stage','category')}
        code=record.get('provider_code')
        if type(code) is int and 0<=code<=1000000:result['provider_code']=code
        return result
    if record.get('event') not in EVENTS or record.get('outcome') not in OUTCOMES:return None
    result={k:record[k] for k in ('event','outcome')}
    for key in ('http_status','rpc_code','count','pending','latency_ms'):
        value=record.get(key)
        if type(value) is int and -100000<=value<=1000000000:result[key]=value
    return result

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--limit',type=int,default=50);args=parser.parse_args()
    if not 1<=args.limit<=200:raise ValueError('Bounded limit required')
    records=deque(maxlen=args.limit)
    with Path('/var/log/feishu-dot/adapter.log').open() as source:
        for line in source:
            if len(line)>4096:continue
            try:record=sanitize(json.loads(line))
            except (ValueError,UnicodeError):continue
            if record:records.append(record)
    for record in records:print(json.dumps(record,separators=(',',':')))
if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('Safe log read unavailable; no raw data output.')
