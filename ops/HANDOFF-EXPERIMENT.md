# One bounded subscription handoff experiment

Not a polling automation. MCP Events already delivers messages to a signed HTTPS hook. Do not create a new API, domain allowance, automatic approval, or paid service.

Latest baseline: SDK paired/open,6 retained messages,0 subscriptions; latest subscribe rejected before challenge with local_approval_pending. Platform has demonstrated one-shot inbox reading but no event delivery.

Opt-in experimental config(callback_handoff_experiment=true): at most45s for human precise URL approval, whole original adapter request budget60s, outbound operations also limited to remaining deadline and10s socket/DNS bounds. Authentication, pairing, URL/DNS/TLS restrictions and signed challenge remain. One admitted in-flight subscription only. Signing secret remains in request memory, never in pending control state. Watch requires request nonce, exact digest, live expiry and human ACCEPT CALLBACK. Revocation/disconnection checked throughout and immediately before subscription commit. Local TCP close is detected; platform CREATE cancellation may not propagate to that connection. Do not claim known platform deadline or cancellation propagation.

Human enable on Avalon:

```sh
sudo systemctl stop feishu-dot-health.timer feishu-dot-adapter
sudo /opt/apps/feishu-dot-adapter/.venv/bin/python /opt/apps/feishu-dot-adapter/pending/handoff-patch/ops/apply_handoff_patch.py
sudo systemctl start feishu-dot-adapter feishu-dot-health.timer
cd /opt/apps/feishu-dot-adapter
sudo .venv/bin/python -m deploy.tunnel_admin watch-callback
```

Only continue past installation if it succeeded. Tunnel/nginx remain running. Watch opens a five-minute window waiting for a NEW live request; it does not approve anything during that window. Once READY appears, tell the parent to issue exactly one CREATE. The parent must keep the request running while the user inspects the exact URL and confirms ACCEPT CALLBACK. Approval must finish in the displayed45s window. No need to copy URL, secret or candidate into chat. Watch rejects an expired or ended request. Do not issue another CREATE after approval. Wait for the original result.

The parent then checks platform task save status, subscribe RPC/HTTP/reason, challenge count/status and adapter subscription count. Success requires the original CREATE saved plus challenge2xx and active subscription. If adapter subscription exists but no saved task, that is a possible orphan, not success: stop further CREATE attempts and perform explicit cleanup of this precise subscription before trying again. If platform timeout occurs before approval, stop the experiment; do not loop new callback approvals. Trigger a NEW Feishu private message only after original task is saved, then confirm callback2xx and correct dot arrival/reply.

Diagnostics add only approved/pending counts and waiting seconds/boolean, not URLs or signing keys. Existing exact approvals persist in encrypted control SQLite; enabling the experiment does not clear or broaden them. New source is backed up; no credentials changed. Disable with the same stop/start handoff and installer --disable if needed.
