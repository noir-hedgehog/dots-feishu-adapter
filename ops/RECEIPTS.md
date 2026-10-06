# Durable received-message reaction

Official case-sensitive emoji `Get` means the adapter has durably accepted this message, not that the dot has processed it or completed a task. Sources:
https://github.com/larksuite/cli/blob/7beffb086d7fa3c5b843d8affa7c089f49cfc65e/skills/lark-im/references/lark-im-reactions.md
https://github.com/larksuite/openclaw-lark/blob/dde0be3680d6fd5443cab426c8f4b3216266346a/src/messaging/outbound/reactions.ts

Existing app credentials stay unchanged. Only new accepted text from the paired human owner/private chat gets this receipt. Pairing candidates, history, groups, bots and repeated events don't. Incoming admission and the reaction queue entry are committed together, then a separate worker adds Get asynchronously. Existing webhook delivery does not wait for the reaction.

Persistent deduplication key includes(appid,messageid,Get); backlog<=100,total retained receipt records<=10000. Before provider create, commit sending. Crashed/ambiguous requests become safe_unknown and are never automatically resent. No provider client idempotency token exists. This version deliberately does not use optional read scopes to recover unknowns; it never grants a scope or infers an app reaction from another operator. Permission denial blocks subsequent attempts persistently; other rejections are terminal. No payload/secret/URL logging. API timeout10s. Healthy receipt worker failures do not stop SDK/Webhook worker. Incoming retention still applies.

Needed existing Feishu app permission: im:message.reactions:write_only. User must check/add/publish this permission in Feishu console if missing; the agent does not expand it. Optional im:message.reactions:read is not needed and not used by this version. A permission block requires explicit recovery after console permissions are corrected; restarting alone does not clear it.

Human enable on Avalon (do not continue if installer fails):

```sh
sudo systemctl stop feishu-dot-health.timer feishu-dot-adapter
sudo /opt/apps/feishu-dot-adapter/.venv/bin/python /opt/apps/feishu-dot-adapter/pending/receipt-patch/ops/apply_receipt_patch.py
sudo systemctl start feishu-dot-adapter feishu-dot-health.timer
```

Installer backs up only changed nonsecret sources/config, enables received_reaction_enabled, never reads credentials, never starts services. Tunnel/nginx untouched. Existing subscriptions and exact callback approval state remain in their encrypted databases. No plugin refresh is needed for this internal received receipt. Verify receipt_enabled,receipt_pending,receipt_confirmed,receipt_unknown,receipt_permission_blocked,receipt_rejected from loopback readyz. Send one NEW paired Feishu message; confirm Get appears and event delivery/reply still works. Old messages are not retrospectively reacted to. Disable via same stop/installer--disable/start sequence if necessary.

Feishu capability plan (no generic shell/CLI tool):
- Existing: list_received_messages,send_message,reply_to_message confined to paired private chat.
- Next optional message tools: list_message_reactions and add_message_reaction scoped to retained incoming message IDs, fixed chat and explicit emoji enum. Read requires im:message.reactions:read; writes require im:message.reactions:write_only. Manual writes must be explicitly requested.
- Documents,calendar,tasks,drive/search: separate typed tools with object allowlists and independently verified tenant/user permission requirements before enabling. Existing bot tenant credentials must not silently stand in for user OAuth or grant access to all files/people.
- Official Lark skills/CLI are useful references for API semantics; do not expose arbitrary subprocess commands, paths, access tokens or shell execution through MCP. Inventory and review a concrete requested capability before install/auth/scope changes.

Deployment verification,2026-10-06 05:23 UTC: user explicitly authorized direct Avalon operation from the verified paired private chat. Activation approved and completed with nonsecret source/config rollback protection; Adapter restarted once, Tunnel/nginx unchanged. readyz200,WS open,paired,one active persistent subscription,callback_delivered4,receipt_enabled=true,pending/unknown/rejected/confirmed0. This is activation verification, not proof of reaction permission or visible Get: a NEW owner/private message is still required. Before the manual restart, automatic platform renewal was observed: subscribe_requests2,challenge_attempts2,latest HTTP200,one active subscription. Existing encrypted subscriptions/approvals survived activation. No credentials/scope/remote-control/GitHub publication changes.
