# Service deployment

These templates target a user-managed Linux host with systemd. Choose and independently verify your own host, application and Tunnel. Repository examples contain placeholders, not working deployment identifiers. The legacy filename is retained for links; it does not identify a destination.

## Layout and prerequisites

- Source: `/opt/apps/feishu-dot-adapter`; install the locked dependencies into its `.venv`.
- Private config: `/etc/feishu-dot/config.json`. Replace all placeholders in the appropriate OAuth or personal Tunnel example.
- State: `/var/lib/feishu-dot`; preserve encrypted databases and their existing Fernet key.
- Root-owned credentials: `/etc/feishu-dot/credentials` (`0700` directory, `0600` files). systemd `LoadCredential` mounts only the required runtime credentials.
- MCP listener: loopback port 8765; Tunnel health listener: loopback port 8766. Review reverse-proxy, firewall and service-account configuration for your host.

The service templates use the `ubuntu` account and legacy profile name `avalon-feishu`. These are explicit template conventions; adapt the service account before first installation if needed. Preserve the profile name and stable owner label for existing installations unless performing a planned migration. No host address or application/Tunnel ID is supplied by the repository.

For OAuth, configure a compatible external issuer, JWKS and introspection endpoint, exact subject/resource/scope and callback allowlist. For personal Tunnel mode, follow [PERSONAL-TUNNEL.md](PERSONAL-TUNNEL.md) and confirm the entire effective audience is only the owner.

## Credentials and activation

The operator may run the credential helper in their own interactive deployment terminal after reviewing the destination and storage policy:

```sh
sudo /opt/apps/feishu-dot-adapter/.venv/bin/python /opt/apps/feishu-dot-adapter/ops/provision_credentials.py
```

It requests credentials through hidden prompts, creates a state key only if absent and does not activate services. Never put actual credentials in repository files, commands or support messages. Preserve the existing state key when restarting or upgrading.

Install and review the service/drop-in files for your chosen authentication mode. Activation is a separate operator action. Loopback readiness proves transport health only; verify pairing, signed subscription, renewal and one authorized message/reply in your actual client before relying on delivery.

## Rollback and verification

Use source/config backups appropriate to the installed version. Stop only the adapter's own services when applying patches; installers must reject active services and preserve existing state. Do not remove credentials or databases as part of a source rollback. The startup patch preserves the configured Tunnel ID instead of overwriting it with the example profile.

Offline tests exercise protocol, authentication, encrypted state and SDK builders without live credentials. They do not establish deployment success. See [the official Tunnel client releases](https://github.com/openai/tunnel-client/releases) and [Secure MCP Tunnels documentation](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) for the client version you deploy.
