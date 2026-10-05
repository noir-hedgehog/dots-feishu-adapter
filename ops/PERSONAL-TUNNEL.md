# Personal Tunnel: user handoff

This opt-in path replaces the OAuth prerequisites in AVALON.md for a strictly personal Tunnel. Default behavior remains OAuth. No OAuth issuer/introspection service is needed here. EVERY effective principal authorized to use this Tunnel acts as owner avalon-personal. The local random MCP secret is not a user identity and provides no multi-user isolation.

Official client v0.0.15 supports mcp.extra_headers and mcp.discovery_extra_headers with file: secret references, scoped to the MCP origin. Connector headers override static values; missing/wrong local authentication is rejected on every request. Local MCP and health listeners bind 127.0.0.1 only; nginx/firewall remain unchanged.

Sources: https://raw.githubusercontent.com/openai/tunnel-client/v0.0.15/docs/configuration.md and https://developers.openai.com/api/docs/guides/secure-mcp-tunnels.

## Confirmation for user

请确认 tunnel_6ac07851f3bc8191b7bef273a6c4b777 的全部有效授权受众只有你本人，包括 Platform 组织角色/主体、关联 ChatGPT 工作区、API key 与其他连接客户端。个人模式无法区分不同使用者。你是否批准将飞书 Secret、Tunnel runtime key、随机本地 MCP 密钥及状态加密密钥保存在 Avalon 的 root-only 文件中，用于持续访问、开机自启动，并由你在自己的终端执行最终启用？配对和实际回调地址仍需你分别本地明确确认。

Agent has not submitted/generated/copied credentials or executed the enable script. The previously rejected service start has not been retried. User executes the following only after approval.

## Single setup command, in user's Mac terminal

```sh
ssh -t avalon 'cd /opt/apps/feishu-dot-adapter && sudo .venv/bin/python -m ops.enable_personal'
```

Read the combined audience/storage/startup explanation and type ENABLE PERSONAL TUNNEL only if approved and true. Default input is the root-only /etc/feishu-dot/.env. Edit it on Avalon with sudo nano; fill FEISHU_APP_SECRET and CONTROL_PLANE_API_KEY without quotes. The nonsecret FEISHU_APP_ID and TUNNEL_ID are prefilled. Parsing is literal, never source/eval; --prompt selects hidden input instead. Generated local MCP secret and Fernet key persist in root-owned 0600 files under /etc/feishu-dot/credentials (0700); systemd LoadCredential supplies only the credentials required by each ubuntu service. This is protected filesystem storage, not a secret vault with encryption at rest. Preserve state key for database recovery.

The script preserves generated keys, refuses reprovisioning while services are active, backs up prior OAuth config, installs explicit personal-mode drop-ins and starts the services after the user's typed confirmation. No credential is printed. Pairing begins only after SDK state OPEN, lasts 600 seconds once and survives reconnect/restart without extension.

## Manual pairing

Configure Feishu bot visibility, long-connection im.message.receive_v1 and message permissions for cli_a92f3828bc5c1cd1. After loopback /readyz reports ws_open:true, send a private text. The pairing text is discarded; only opaque user/chat candidate IDs are retained. Within 600 seconds run:

```sh
ssh -t avalon 'cd /opt/apps/feishu-dot-adapter && sudo .venv/bin/python -m deploy.tunnel_admin pair'
```

Inspect open_id/chat_id, independently recognize your private chat, choose candidate ID and type ACCEPT PRIVATE CHAT. No first-message trust. Groups/bots rejected; at most ten candidates. Closed/stale WS or expiry rejects acceptance. Pairing fixes owner/user/chat and cannot silently change. Interactive reset-pairing permits a new window only after an unpaired window expired.

## Plugin and actual callback

Connect developer-mode plugin through the existing Tunnel after pairing, with the intended personal ChatGPT workspace association and Tunnels Read+Use. Confirm the current dot supports MCP Events. Invoke events/list then authenticated events/subscribe for message.created with paired chat_id and the actual product-provided callback URL/signing secret. First valid request may return -32015 / local_approval_pending: no subscription exists and no callback was contacted.

Approve the actual URL locally without guessing domains or copying it into chat:

```sh
ssh -t avalon 'cd /opt/apps/feishu-dot-adapter && sudo .venv/bin/python -m deploy.tunnel_admin callback'
```

Inspect escaped pending URL from the real request, verify it is your current product callback, choose pending ID and type ACCEPT CALLBACK. Pending approvals expire after 600 seconds and are encrypted at rest without signing keys. Exact approved URL persists, not wildcard domains. HTTPS/443, public-only DNS, pinned connection IP, normal TLS and no redirects are checked before pending storage, approval, signed challenge and delivery.

Retry events/subscribe from the same plugin. Signed verification challenge still must succeed. Renew before refreshBefore using authenticated subscribe; stable subscription IDs and key rotation are retained. The client must renew: the server never grants indefinite access. Restart restores pairing, exact URL approvals, encrypted subscriptions, queue/dedup and reply associations. Bounded retries allow stable event-ID dedup; no exactly-once guarantee.

## Completion gate and revocation

Send a fresh private text AFTER subscription; verify the matching event reaches the intended dot, invoke reply_to_message via Tunnel and have the user verify the Feishu reply. Then verify restart/renewal and unsubscribe. Readiness and counter values alone are not completion.

```sh
ssh -t avalon 'cd /opt/apps/feishu-dot-adapter && sudo .venv/bin/python -m deploy.tunnel_admin revoke'
```

Typed REVOKE disables new MCP calls and new message admission; delivery rechecks audience before each send. In-flight calls cannot be recalled. Revoke before widening Tunnel audience and switch to proper OAuth for shared access. Separately authorized rollback removes personal drop-ins and restores config.oauth.backup.json; preserve encrypted data/keys.
