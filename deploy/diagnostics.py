"""Allowlisted diagnostics only: never serialize exception text, arguments or paths."""
from contextlib import contextmanager
import errno
import json

STAGES=frozenset({'startup','config_load','config_validate','credential_load','control_load','audience_check','local_auth_validate','feishu_client_build','http_bind','feishu_ws_connect','binding_load','state_worker'})

def safe_error(error, stage):
    result={'event':'adapter_error','stage':stage if stage in STAGES else 'startup','category':'unknown'}
    if isinstance(error,PermissionError):result['category']='permission_or_authorization_rejected'
    elif isinstance(error,FileNotFoundError):result['category']='required_file_missing'
    elif isinstance(error,ModuleNotFoundError):result['category']='dependency_missing'
    elif isinstance(error,OSError):
        result['category']={errno.EACCES:'permission_denied',errno.EROFS:'read_only_filesystem',errno.EADDRINUSE:'port_in_use',errno.ENOSPC:'storage_full'}.get(error.errno,'os_or_network_error')
    elif isinstance(error,json.JSONDecodeError):result['category']='configuration_json_invalid'
    elif isinstance(error,(ValueError,KeyError,TypeError)):result['category']='configuration_or_credential_validation_failed'
    cls=type(error)
    if cls.__module__=='cryptography.fernet' and cls.__name__=='InvalidToken':
        result['category']='encrypted_state_key_mismatch'
    if cls.__module__=='lark_oapi.ws.exception' and cls.__name__ in {'ClientException','ServerException','ServerUnreachableException','ConnectionClosedException','HeaderNotFoundException'}:
        result['category']={'ClientException':'feishu_endpoint_rejected','ServerException':'feishu_endpoint_unavailable','ServerUnreachableException':'feishu_network_unreachable','ConnectionClosedException':'feishu_ws_closed','HeaderNotFoundException':'feishu_handshake_header_missing'}[cls.__name__]
        code=getattr(error,'code',None)
        if type(code) is int and 0<=code<=1000000:result['provider_code']=code
    return result

def report(error,stage):
    if getattr(error,'_safe_reported',False):return
    print(json.dumps(safe_error(error,stage),separators=(',',':')),flush=True)
    try:error._safe_reported=True
    except Exception:pass

@contextmanager
def phase(name):
    try:yield
    except Exception as error:
        report(error,name)
        raise
