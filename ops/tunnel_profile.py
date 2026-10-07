"""Nonsecret personal service profile; no env resolution before CLI overrides."""
import re
from deploy.literal_env import validate_identifier

def profile_tunnel_id(profile):
    matches=re.findall(r'^  tunnel_id: (.+)$',profile,re.MULTILINE)
    if len(matches)!=1:raise ValueError('Expected one explicit Tunnel ID in the existing profile')
    value=matches[0].strip()
    if len(value)>=2 and value[0]==value[-1] and value[0] in ('"',"'"):value=value[1:-1]
    return validate_identifier(value,'tunnel_')

def render_profile(tunnel_id):
    validate_identifier(tunnel_id,'tunnel_')
    return f'''config_version: 1
control_plane:
  base_url: "https://api.openai.com"
  tunnel_id: "{tunnel_id}"
  api_key: "file:/run/credentials/feishu-dot-tunnel.service/control_plane_api_key"
health:
  listen_addr: "127.0.0.1:8766"
admin_ui:
  open_browser: false
log:
  level: warn
  format: json
mcp:
  server_urls:
    - channel: main
      url: "http://127.0.0.1:8765/mcp"
'''
