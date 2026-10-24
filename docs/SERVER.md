# Fernruf MCP server (remote access)

Fernruf listens on `127.0.0.1:8787` by default. Put **nginx** (or another reverse proxy) with TLS in front for remote MCP clients.

## Example nginx

See `deploy/examples/nginx-fernruf.conf`:

- HTTP → HTTPS redirect
- `server_name fernruf.example.com`
- `proxy_pass http://127.0.0.1:8787` for `/healthz`, `/mcp`, OAuth (`/.well-known/`, `/register`, `/authorize`, `/token`), and `/oauth/github/callback`
- `X-Forwarded-For $remote_addr`
- Rate limit on `/mcp` (`30r/m` in the example)
- TLS via Let's Encrypt (`/etc/letsencrypt/`)

MCP DNS rebinding protection allows hosts derived from `FERNRUF_OAUTH_RESOURCE`, `FERNRUF_OAUTH_ISSUER`, and `FERNRUF_GITHUB_CALLBACK_URL`, plus loopback and any extra names in `FERNRUF_ALLOWED_HOSTS` (comma-separated).

When Fernruf binds to `127.0.0.1` and nginx is on the same host, `FERNRUF_TRUSTED_PROXIES` defaults to `127.0.0.1` and `::1` so auth lockouts use the real client IP from `X-Forwarded-For`. Set `FERNRUF_TRUSTED_PROXIES` explicitly when the proxy is elsewhere.

## Authentication

- Static bearer: `FERNRUF_MCP_TOKEN` (generate with `fernruf gen-token`)
- Or GitHub OAuth (`FERNRUF_GITHUB_*`, `FERNRUF_OAUTH_*`)

An unauthenticated `POST /mcp` returns **401** with OAuth metadata pointing at  
`https://fernruf.example.com/.well-known/oauth-protected-resource/mcp`.

## Settings the MCP token cannot change

Allowlist membership, international dialing, and mobile flat must be changed on the host:

```bash
fernruf settings allow-number +493023125123
```

Attempts to patch these via MCP return `setting_requires_cli`.

## Bind address

- Default `FERNRUF_BIND_HOST=127.0.0.1`, `FERNRUF_BIND_PORT=8787`
- To listen on another address, set `FERNRUF_BIND_OVERRIDE` to the **exact** same host string

## fail2ban

Example filters and jails: `deploy/examples/fail2ban/`. They match nginx **401** lines and journal lines  
`fernruf auth failed ip=… reason=…`.

## Optional startup mail

When `FERNRUF_NOTIFY_ENABLED=true`, configure SMTP (`FERNRUF_NOTIFY_*`) with a TLS certificate name  
(`FERNRUF_NOTIFY_SMTP_TLS_NAME`) that matches your mail server certificate.
