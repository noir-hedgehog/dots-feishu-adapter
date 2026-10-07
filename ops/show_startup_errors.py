#!/usr/bin/env python3
"""Read logs locally but output only verified allowlisted adapter diagnostics."""
import json
from pathlib import Path

STAGES={'startup','config_load','config_validate','credential_load','control_load','audience_check','local_auth_validate','feishu_client_build','http_bind','feishu_ws_connect','binding_load','state_worker'}
CATEGORIES={'unknown','permission_or_authorization_rejected','required_file_missing','dependency_missing','permission_denied','read_only_filesystem','port_in_use','storage_full','os_or_network_error','configuration_json_invalid','configuration_or_credential_validation_failed','encrypted_state_key_mismatch','feishu_endpoint_rejected','feishu_endpoint_unavailable','feishu_network_unreachable','feishu_ws_closed','feishu_handshake_header_missing'}

def accepted(record):
    if not isinstance(record,dict) or not set(record)<= {'event','stage','category','provider_code'}:return False
    if record.get('event')!='adapter_error' or record.get('stage') not in STAGES or record.get('category') not in CATEGORIES:return False
    code=record.get('provider_code')
    return code is None or (type(code) is int and 0<=code<=1000000)

def main():
    path=Path('/var/log/feishu-dot/adapter.log')
    with path.open('rb') as source:
        source.seek(max(0,path.stat().st_size-65536));raw=source.read()
    records=[]
    for line in raw.splitlines():
        try:
            record=json.loads(line)
            if accepted(record):records.append(record)
        except Exception:pass
    for record in records[-3:]:print(json.dumps(record,separators=(',',':')))
    if not records:print('No allowlisted startup error recorded yet.')

if __name__=='__main__':
    try:main()
    except Exception:raise SystemExit('Sanitized diagnostics unavailable; no raw log output.')
