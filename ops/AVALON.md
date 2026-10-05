# Avalon deployment handoff

Target: existing SSH alias `avalon`, ubuntu@43.160.198.172, VM-0-6-ubuntu. Never substitute Virtualink backend 106.54.36.175.

Source base and GitHub HEAD verified: 22ae487973aa996777115d882118b56ab7bcb9d4. Code deployed under /opt/apps/feishu-dot-adapter. Python 3.12.3, Node 22.22.0 (not needed by this service). Official tunnel-client 0.0.15+a390c168ff1b2d14e73a95991c186c6aba3ff5a0 downloaded from openai/tunnel-client GitHub release and ZIP checked against its SHA256SUMS.txt. Binary stays on Avalon, outside git. Final Python environment installed from HTTPS pypi.org with requirements.lock.txt; initial temporary dependency check used host default mirror, and that temporary environment was subsequently removed.

App ID: cli_a92f3828bc5c1cd1. Tunnel ID: tunnel_6ac07851f3bc8191b7bef273a6c4b777. Official profile /etc/feishu-dot/tunnel/avalon-feishu.yaml uses local HTTP MCP 127.0.0.1:8765/mcp and loopback health UI 127.0.0.1:8766. No external listener or nginx changes.

Services enabled for reboot but deliberately inactive until /etc/feishu-dot/approved exists. Health timer checks active services each minute and restarts after three consecutive readiness failures. Adapter /healthz indicates process only; /readyz indicates SDK socket OPEN and worker alive. Counts are not delivery proof. Tunnel exposes separate health/ready endpoints. SDK automatic reconnect remains enabled; systemd restarts crashes after 10 seconds. Logs /var/log/feishu-dot/{adapter,tunnel}.log rotate daily or over 10MB, retain seven compressed rotations. Existing nginx remains active.

## User action and approval required

Approve storing three runtime secrets plus a generated encryption key on this existing server for unattended operation. Credential files are root-owned 0600 in /etc/feishu-dot/credentials (0700 directory); systemd LoadCredential supplies runtime copies to ubuntu services. This is protected filesystem storage, not encrypted-at-rest credential vault storage. The encryption key persists alongside the host; it protects database snapshots from independent disclosure, not a compromised host. No secrets have been submitted or stored. Do not send secrets in chat, arguments, git, or logs.

After approval, in the user's own interactive Mac terminal:

```sh
ssh -t avalon 'sudo /opt/apps/feishu-dot-adapter/.venv/bin/python /opt/apps/feishu-dot-adapter/ops/provision_credentials.py'
```

This locally prompts on Avalon for Feishu App Secret, an approved OAuth introspection credential, and Tunnel runtime key. It generates the state key only if absent, does not print secrets, and does not activate services.

Complete nonsecret /etc/feishu-dot/config.json: OAuth issuer, same-host JWKS and introspection URLs, exact authorized OAuth subject, authorized Feishu open_id/private chat_id, canonical HTTPS resource identifier and verified ChatGPT callback host allowlist. Existing adapter requires RS256 JWT and online token introspection. An approved compatible OAuth provider is still missing. Do not replace it with allow-all auth or assume Tunnel control-plane authorization satisfies app OAuth. Clarify the canonical resource identifier through the actual Tunnel/plugin OAuth flow.

In Feishu console, verify bot visibility, message receive event over long connection and required message send/reply permissions for this app. Then activate only after valid configuration and credentials:

```sh
sudo touch /etc/feishu-dot/approved
sudo systemctl start feishu-dot-adapter feishu-dot-tunnel
```

Keep tunnel-client running while creating a developer-mode ChatGPT plugin via Tunnel. Check tunnel association with the intended ChatGPT workspace and Platform organization, Tunnels Read+Use, and developer-mode access. No new paid service is needed.

## Completion gate

Confirm actual current dot supports MCP Events. Connect the plugin, inspect events/list, subscribe to message.created with the product-provided signed callback, verify callback challenge, then user sends a unique private Feishu message. Confirm matching event reaches the intended dot and invoke reply_to_message through Tunnel; user verifies reply on Feishu. Record stable correlation IDs without message payload/secrets. Repeat after service restart to verify encrypted subscription/queue persistence; verify renewal before refreshBefore and unsubscribe stops subsequent events. No live Feishu handshake, authenticated Tunnel polling, plugin connection, signed callback subscription, or end-to-end send/reply has been performed yet.

Official references:
- https://developers.openai.com/plugins/build/mcp-events
- https://developers.openai.com/api/docs/guides/secure-mcp-tunnels
- https://github.com/openai/tunnel-client/releases/latest

Rollback: disable/stop only feishu-dot-adapter, feishu-dot-tunnel and feishu-dot-health.timer. Preserve credentials and /var/lib/feishu-dot until explicit retention/deletion approval. Do not change other host services.
