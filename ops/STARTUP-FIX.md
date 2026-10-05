# Staged startup fix, awaiting user application

Confirmed Tunnel failure: profile still references env:CONTROL_PLANE_API_KEY. Profile parsing resolves that before the CLI file reference override, so the absent environment variable prevents startup. The fixed nonsecret profile references file:/run/credentials/feishu-dot-tunnel.service/control_plane_api_key. The personal setup generator writes the corrected profile before starting services.

Current auto-retry means editing live code/profile could start external connections without the requested handoff. The patch is therefore staged under /opt/apps/feishu-dot-adapter/pending/startup-fix and is not loaded by the current services. No credential is read, copied, rotated or replaced during staging or patch application.

In the user's Avalon terminal, when ready to apply and resume the previously enabled setup:

```sh
sudo systemctl stop feishu-dot-health.timer feishu-dot-adapter feishu-dot-tunnel
sudo /opt/apps/feishu-dot-adapter/.venv/bin/python /opt/apps/feishu-dot-adapter/pending/startup-fix/ops/apply_startup_fix.py
sudo systemctl start feishu-dot-adapter feishu-dot-tunnel feishu-dot-health.timer
```

The apply script refuses any active/activating service, installs only named nonsecret source files and fixed profile, backs up the old profile, and never starts a service. No daemon-reload is needed because units are unchanged. Do not rerun enable_personal or resubmit .env merely to apply this patch.

Check:

```sh
systemctl is-active feishu-dot-adapter feishu-dot-tunnel
curl -sS http://127.0.0.1:8765/readyz
curl -sS http://127.0.0.1:8766/readyz
```

Adapter diagnostics serialize only event, an allowlisted stage, category and (for actual SDK endpoint exceptions) a bounded numeric provider code. Never serialize exception messages, args, paths, URLs, headers, keys, message bodies, personal IDs or traceback. SDK logger remains disabled. Root-only parsing of adapter.log can select only validated adapter_error records; never paste unrestricted logs. Startup stage categories identify configuration, credential loading, encrypted control state, audience validation, client build, loopback bind or Feishu connection failures.

Adapter root cause identified without reading credentials: actual systemd credential files are 0440 in a protected read-only 0550 runtime directory, while read_secret rejected every group-read bit. The patch permits exactly 0440 only when CREDENTIALS_DIRECTORY matches the expected /run/credentials/feishu-dot-adapter.service, with trusted ownership, no symlink, no write permissions or world access. Ordinary files retain private permission requirements. No chmod/security configuration changes are made. Additional failures after this correction will be classified by stage/category. Staging alone does not execute a credential-bearing live probe. Future health telemetry includes SDK event count/recent time, pairing candidate count/window, owner/binding booleans, active subscription count, callback delivered count and authenticated MCP request/tool-call request counts. Counters do not prove this particular dot received a message or that a reply arrived in Feishu.

If ready and WS OPEN, use existing local pair command, plugin connection, authenticated subscribe/callback approval, then user send/reply test. No completion claim from readiness alone.

Safe startup error command after user applies/starts:

```sh
sudo /opt/apps/feishu-dot-adapter/.venv/bin/python /opt/apps/feishu-dot-adapter/pending/startup-fix/ops/show_startup_errors.py
```

This outputs only three recent records that match the exact allowlisted schema/values; never raw log text.
