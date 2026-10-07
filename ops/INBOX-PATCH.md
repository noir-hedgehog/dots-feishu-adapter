# Read-only paired inbox and subscription diagnostics

The patch adds `list_received_messages` with required fixed `chat_id`, optional `limit` (1–50, default20), `cursor` (last retained message ID), and timezone-aware `since`. It returns retained accepted inbound messages oldest first, deduplicated by message ID. No provider history request occurs. Existing persisted messages remain readable; old timestamps are null. New timestamps reflect adapter receipt time. Messages observed before pairing were not retained. A read does not remove messages or alter event delivery.

Human-run on the deployment host, one restart of the adapter only; Tunnel and nginx stay running:

```sh
sudo systemctl stop feishu-dot-health.timer feishu-dot-adapter
sudo /opt/apps/feishu-dot-adapter/.venv/bin/python /opt/apps/feishu-dot-adapter/pending/inbox-patch/ops/apply_inbox_patch.py
sudo systemctl start feishu-dot-adapter feishu-dot-health.timer
```

Installer backs up only changed nonsecret source, validates syntax, refuses running adapter/watchdog, never reads credentials or starts services. If installation fails, do not run the start command until diagnosed.

Refresh/rescan the Feishu adapter MCP metadata in the plugin configuration, then confirm `tools/list` contains `list_received_messages`. Reopening a chat may be needed to reload its callable tool catalog. Call it with the fixed chat ID already present in plugin event schema; do not guess or disclose another chat ID. If empty, send a fresh private message to the paired Feishu app, wait for receipt, and read once again. This is a one-shot diagnostic, not a polling automation.

Loopback `/readyz` adds `retained_messages`, `subscribe_requests`, `subscribe_last_http_status`, `subscribe_last_rpc_code`, allowlisted `subscribe_last_reason`, `challenge_attempts`, `challenge_last_http_status`. Counters reset on adapter restart. Status0 means no HTTP response was recorded, not success. They contain no URL, signing key, message body or traceback. Existing callback approvals and signed verification remain enforced.
