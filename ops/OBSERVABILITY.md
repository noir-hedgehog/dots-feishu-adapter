# Health, status and safe troubleshooting

This document describes the monitoring implementation and how an operator can verify it in their own deployment. Repository tests do not establish live delivery or availability.

`get_message_status(chat_id,message_id)` is a new readOnly MCP tool confined to retained messages in the currently paired private chat. HTTP authentication is checked for each call; personal Tunnel mode also rechecks the current local binding. It returns durable admission, queued webhook count, terminal webhook outcomes, reply confirmation/unknown counters, Get categorical outcome plus numeric HTTP/provider codes. No body, destinations, signing keys, principal IDs or outgoing text. Legacy records explicitly say historical_status_available=false; their previously unrecorded delivery/reply history is not reconstructed. Existing durable Get records can still be queried. Cached idempotent reply success is not counted twice.

Loopback readyz distinguishes process/WS/pairing, state_worker_alive,receipt_worker_alive, events_ready, subscription_remaining_seconds (nearest active expiry), expired_subscriptions, queue_oldest_age_seconds/queue_delayed (>60s), delivery failure outcomes, reply confirmed/unknown and receipt results. HTTP200 continues to mean transport readiness, not that every task/event/reply succeeded. A new subscribe request with HTTP200 and active lease means renewal succeeded; failure to renew reaches events_ready=false after expiry. Counters specific to HTTP/challenge reset on restart; message and provider outcomes persist. No event inactivity alarm is inferred simply from a quiet private chat.

Structured operational logs use strict event/outcome enums and bounded numeric fields only; no message IDs or payloads, URLs, secret hashes, stack traces or exception strings. Logging IO failure must not undo a committed receive/delivery. Safe reader:

```sh
cd /opt/apps/feishu-dot-adapter
sudo .venv/bin/python -m ops.read_safe_logs --limit 50
```

The reader filters source records and emits only allowed fields, never arbitrary raw lines. Newly added features need a source deployment and plugin Refresh/Rescan before this new status tool is callable; no changes to scope or credentials are required. Publishing source does not deploy it or authorize service changes.

The supplied logrotate template expects: logs directory700 ubuntu:ubuntu, files600; logrotate daily or >=10MiB at its next run,7 retained rotations,compression,copytruncate, ubuntu user/group. maxsize is evaluated when logrotate runs, not a continuous hard cap. copytruncate preserves systemd append descriptors but has a small copy/truncate race; rotated logs are troubleshooting records, not the source of truth. Encrypted SQLite remains authoritative. No external monitoring, log shipping or new paid service.

Troubleshooting sequence: check WS/pairing/worker alive; then active lease+subscribe reason; then queue age+delivery outcomes; finally scoped message status to distinguish webhook accepted, API reply confirmed, and Get received. API confirmation means provider accepted the action, not that the user's client rendered it; user visual verification is separate. A platform tool cancellation does not prove the call reached the adapter host; never resend with a new idempotency key merely because UI reports cancellation. Reaction safe_unknown is not auto-retried without operator/app verification.
