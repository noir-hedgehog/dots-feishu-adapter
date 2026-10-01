# Direct Feishu MCP event adapter

A minimal single-human, single-private-chat bridge using official Feishu SDK APIs and public MCP Events. It does not run another AI bot or depend on OpenClaw. The existing ChatGPT dot must actually support installing this plugin and subscribing to `message.created`; that product-level prerequisite is not confirmed by offline tests.

## Implementation

- `deploy/transport.py`: stateless MCP 2026-07-28 HTTP JSON binding, required metadata/headers, OAuth challenge and origin checks.
- `deploy/auth.py`: external approved OAuth issuer, RS256/JWKS validation, exact audience/subject/scope. No private ChatGPT authentication.
- `deploy/store.py`: encrypted SQLite snapshot, persistent subscription/queue/dedup/reply associations. State key is supplied interactively and must remain available after restart.
- `deploy/network.py`: approved HTTPS hosts, public-only DNS resolution, connection IP pinning and TLS hostname verification; no redirects.
- `deploy/feishu.py`: official lark-oapi WS receive plus message create/reply builders; bounded retries and stable provider UUID.
- `adapter.py`: finite subscription TTL, signed callback challenge, deduplication, bounded retries, one-minute signing-key overlap, single authorized human DM restrictions. Its standalone HTTP mode is mock-only; deployment uses `deploy.run`.

Subscriptions last at most one hour and require renewal. Callback acknowledgement only confirms event receipt; replying uses a separate MCP tool. Message history/idempotency state is bounded at 10,000 entries and fails closed when full. Administrative retention/cleanup, monitoring and crash-after-provider-send reconciliation remain deployment concerns; provider deduplication has a limited window.

## Local verification

Use Python 3.11 or later in an isolated environment:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python verify.py
.venv/bin/python demo.py
.venv/bin/python -m deploy.run
```

`verify.py` fails if any test is skipped. Tests need no live credentials or sockets. On the authoring Mac: all 34 tests passed with zero skips, including real official SDK builders, authenticated encryption and RS256 verification. Compilation and mock lifecycle passed. Official dependencies were installed into the project virtual environment through the formal approval mechanism with the existing proxy configuration preserved. No Feishu/ChatGPT end-to-end session or live OAuth integration was performed.

## Deployment prerequisites (not performed)

1. Verify this exact existing dot supports plugin event subscriptions and reply tools. If it does not, this adapter cannot make that dot accessible from Feishu.
2. Choose an approved public HTTPS endpoint/tunnel and OAuth provider compatible with the plugin client's authorization flow. This repository does not implement an authorization server. Confirm client registration and required scopes through supported product settings.
3. Create the Feishu application through its official console, grant required message permissions, enable its long connection event subscription and limit it to the intended user/chat. Verify current official permissions and app visibility before proceeding.
4. Copy `deploy/config.example.json` to a local ignored configuration file and replace only nonsecret placeholders, including exact OAuth subject, app/chat/user identifiers, issuer/JWKS and callback host allowlist. Default example URLs are reserved placeholders.
5. Supply replacement Feishu App Secret and a newly generated Fernet state key through the hidden interactive prompts of `python -m deploy.run --config deploy/config.local.json --run`. Do not commit keys, put them in CLI arguments or transmit them in chat. Long-running service secret handling needs a separately approved secure integration.
6. The localhost WSGI listener requires an approved TLS reverse proxy; it is not a standalone public production web server. Test callback verification, subscribe/refresh/unsubscribe, an authorized human message and one reply in the existing dot before rollout.

No secrets were configured, no security settings changed, no live subscriptions created and no service deployed during authoring.

## Primary sources

- [Official Feishu OpenClaw reference plugin](https://github.com/larksuite/openclaw-lark) (reviewed only; not a dependency)
- [Official Feishu Python SDK](https://github.com/larksuite/oapi-sdk-python)
- [Public plugin MCP Events](https://developers.openai.com/plugins/build/mcp-events)
- [MCP Streamable HTTP 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)
- [Connecting plugins to ChatGPT](https://developers.openai.com/plugins/deploy/connect-chatgpt)

All source in this repository is newly written adapter code; no upstream plugin source was vendored. `requirements.lock.txt` records the successfully tested dependency versions; use it for reproducible installation and review updates before deployment.
