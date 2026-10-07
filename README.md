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

`verify.py` fails if any test is skipped. Tests need no live credentials or sockets and cover official SDK builders, authenticated encryption and RS256 verification. Use `requirements.lock.txt` for the tested dependency versions. Offline success does not establish a live Feishu/ChatGPT session or validate an OAuth provider integration.

## Deployment prerequisites (not performed)

1. Verify this exact existing dot supports plugin event subscriptions and reply tools. If it does not, this adapter cannot make that dot accessible from Feishu.
2. Choose an approved public HTTPS endpoint/tunnel and OAuth provider compatible with the plugin client's authorization flow. This repository does not implement an authorization server. Its current implementation requires RS256 JWTs, same-issuer-host JWKS/introspection endpoints, and Bearer resource-server authorization to RFC 7662 introspection. These are adapter compatibility limits, not MCP requirements. No real provider has been selected or verified. Confirm client registration and required scopes through supported product settings.
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

## Delivery and authorization boundaries

Callback work is isolated per event, committed separately, with a maximum of three attempts. Permanent destination/certificate rejection is terminal. Encrypted aggregate outcome counters and per-queued-event error categories contain no exception text or URLs. A later failure cannot roll back a previously committed callback acknowledgement. Network calls, challenge verification and provider sends run outside the shared storage lock, allowing incoming SDK events to persist promptly.

Deployment now requires an approved RFC 7662 token introspection endpoint and a separately issued resource-server credential, entered at the hidden startup prompt. The provider must return active status plus the issuer/audience/subject/expiry/scope claims used by this resource. Every delivery rechecks authorization, including queued work after restart. Revoked authorization or actual subscription expiry cancels pending delivery; reconnect requires an authenticated new subscription. `revoke(owner)` provides an explicit local disconnect operation. JWKS/introspection outages pause delivery while retaining subscriptions and queued events, with exponential backoff capped at 60 seconds until recovery or actual expiry. No revocation listener, endpoint, credential or service has been configured here.

Delivery is bounded at-least-once with retry exhaustion: failures can terminate undelivered events. A crash or storage failure after a remote endpoint accepts an event but before the local commit can cause repetition; receivers must deduplicate stable eventId. Provider send ambiguity relies on Feishu's limited UUID deduplication window. Revocation/unsubscribe cannot recall an HTTP request already in flight. No exactly-once guarantee is made.

Subscription refreshBefore is capped at the earlier verified token/introspection expiry minus 30 seconds. Tokens with less than 40 seconds remaining are rejected; a challenge that consumes the remaining window also causes rejection. Renewal stores the newly verified token and its expiry. No extra expiresAt field is invented; clients use the documented refreshBefore field.

## Service deployment and personal Tunnel mode

[Service deployment](ops/AVALON.md) describes the protected systemd credential setup and prerequisites. Templates in `ops/` use generic paths and identifier placeholders; configure your own deployment before running them. Health endpoints report transport state and aggregate counts, not end-to-end success.

[Personal Tunnel setup](ops/PERSONAL-TUNNEL.md) is opt-in and requires the Tunnel's entire effective audience to be only the owner. It has no multi-user identity isolation. Default OAuth remains available. Personal mode adds local MCP authentication, a bounded manual pairing window, exact callback approvals, signed challenges and encrypted restart state. No live service is enabled by checking out this repository.

The retained inbox (`list_received_messages`) and message diagnostics (`get_message_status`) authenticate every HTTP request. OAuth reads use the request's verified principal; queued webhook deliveries separately recheck their saved token. Personal Tunnel reads additionally recheck the local audience and pairing. Deployments must refresh their tool catalog after an upgrade.

Run `verify.py` with the locked dependencies for the complete offline suite, including real JWT signatures and encryption with mock network endpoints. These checks do not validate a real OAuth provider, tenant permissions, live Feishu delivery, or a specific client's Tunnel behavior.
