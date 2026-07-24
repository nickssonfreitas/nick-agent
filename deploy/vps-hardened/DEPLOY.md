# Hardened VPS deployment — Hermes Agent

A deploy bundle for running Hermes on an internet-facing VPS with personal data
and MCP servers connected. It closes the configuration-layer risks from the
hardening review; the code-layer fixes shipped separately in the security PR.

**Read this first.** The single most important step is the reverse proxy (step
7): do **not** let the dashboard run in loopback mode behind the proxy, and do
**not** rewrite the `Host` header to localhost. That combination serves the
session token to any internet client with no authentication (finding R1). The
compose file and Caddyfile here are already set up correctly; the warning is so
you don't "fix" them into the vulnerable shape.

Files in this bundle:

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Bridge network, pinned image, caps/read-only/limits, healthchecks |
| `Caddyfile` | TLS reverse proxy that preserves the Host header |
| `.env.example` | Compose substitution vars (dashboard auth, UID/GID) — copy to `.env` |
| `config-snippet.yaml` | Retention + MCP sampling settings to merge into the container's config |

---

## Prerequisites

- A VPS with Docker Engine + the compose plugin.
- A domain name with an A/AAAA record pointing at the VPS (Caddy needs it for TLS).
- Ports 80 and 443 reachable from the internet; nothing else needs to be open.

## Step 1 — Encrypt the disk (infrastructure layer)

Hermes stores conversations as **plaintext SQLite**. Nothing in the app encrypts
at rest, so a provider disk snapshot or filesystem backup captures everything in
the clear. Use an encrypted volume (LUKS / your provider's encrypted block
storage) for wherever Docker keeps its volumes. This is the one risk the app
cannot close for you.

## Step 2 — Pin the image digest

`:latest` is mutable. Resolve a real digest for a versioned release and pin it:

```bash
docker buildx imagetools inspect nousresearch/hermes-agent:<release-tag>
# copy the top-level digest, then in docker-compose.yml replace both
# REPLACE_WITH_PINNED_DIGEST occurrences with:  sha256:<digest>
```

## Step 3 — Generate the dashboard password hash

Never ship a plaintext password. Generate a scrypt hash offline and put it in
`.env`:

```bash
docker run --rm nousresearch/hermes-agent@sha256:<digest> \
  python3 -c "from plugins.dashboard_auth.basic import hash_password; import getpass; print(hash_password(getpass.getpass('password: ')))"
```

Copy the `scrypt$...` line into `HERMES_DASHBOARD_PW_HASH`.

## Step 4 — Fill in `.env`

```bash
cd deploy/vps-hardened
cp .env.example .env
chmod 600 .env
openssl rand -base64 32   # paste into HERMES_DASHBOARD_SECRET
# set HERMES_DASHBOARD_USER and HERMES_DASHBOARD_PW_HASH (step 3)
```

Edit `Caddyfile`: replace `hermes.example.com` with your domain.

## Step 5 — First boot and provider credentials

```bash
docker compose up -d gateway dashboard   # bring up the app (not caddy yet)
```

Add your provider API keys **inside** the container so they go through the
hardened credential path, not into `docker inspect`:

```bash
docker exec -it hermes hermes login
```

This writes `~/.hermes/.env` and `auth.json` with owner-only permissions on the
named volume. (The security PR's clamp keeps `.env` at 0600 even across rewrites,
which is why the volume must be a POSIX filesystem — not a NAS/SMB mount where
chmod is refused.)

## Step 6 — Apply retention and MCP settings

Merge `config-snippet.yaml` into the container's config:

```bash
docker exec -it hermes sh -lc 'cat >> /opt/data/config.yaml' < config-snippet.yaml
# then reconcile / restart:
docker compose restart gateway dashboard
docker exec -it hermes hermes sessions optimize-storage   # one-time VACUUM
```

Review the merged `config.yaml` by hand — adjust the `mcp_servers` block to your
actual servers, keeping `sampling.enabled: false` and a minimal `tools.include`
on each.

## Step 7 — Bring up TLS and go live

```bash
docker compose up -d caddy
```

Caddy fetches a certificate on first request. Give it a minute, then verify.

---

## Verification checklist — run before connecting personal data

```bash
# 1. Auth gate is ENGAGED (the whole ballgame). Must print: true
curl -s https://YOUR_DOMAIN/api/status | jq '.auth_required'

# 2. The session token is NOT handed to anonymous clients. Must print: 0
curl -s https://YOUR_DOMAIN/ | grep -c __HERMES_SESSION_TOKEN__

# 3. Security headers present (CSP is Report-Only until you enforce it).
curl -sI https://YOUR_DOMAIN/api/status | grep -iE 'content-security-policy|x-frame-options|strict-transport'

# 4. No app port is published to the host except caddy's 80/443.
docker compose ps --format '{{.Service}} {{.Ports}}'
#    gateway and dashboard must show NO 0.0.0.0:*->* mapping.

# 5. Image is pinned by digest, resource limits set.
docker inspect hermes | jq '.[0].Config.Image, .[0].HostConfig.Memory'

# 6. Credential file is owner-only inside the container.
docker exec hermes stat -c '%a %U' /opt/data/.env         # expect: 600 hermes

# 7. Retention is on.
docker exec hermes sh -lc 'grep -A2 "^sessions:" /opt/data/config.yaml'

# 8. Each MCP server has sampling disabled.
docker exec hermes sh -lc 'grep -A3 "mcp_servers:" /opt/data/config.yaml'
```

If item 1 prints `false` or item 2 prints anything but `0`, **stop** — the
dashboard is reachable without authentication. The usual cause is a `header_up
Host` line in the Caddyfile or the dashboard bound to `127.0.0.1`; fix that
before going further.

---

## Updates and rollback

Neither `state.db` nor `kanban.db` has a schema-downgrade guard, so treat every
image change as a database operation:

```bash
docker compose stop
docker run --rm -v hermes-data:/data -v "$PWD/backup":/backup alpine \
  sh -c 'cp -a /data/state.db* /data/kanban*.db* /backup/'   # back up FIRST
# edit docker-compose.yml to the new (or previous) pinned digest
docker compose up -d
```

Backups are not covered by retention and contain both `.env` secrets and full
conversation history — store and rotate them as carefully as the live DB.

---

## Residual risk — what this deploy does NOT close

Stated plainly so nothing is a surprise later:

- **`shell.exec` / `/api/pty` are RCE by design** for anyone holding a valid
  session token, and sessions cannot be revoked without rotating
  `HERMES_DASHBOARD_BASIC_AUTH_SECRET` and restarting. Give dashboard access
  only to people you'd give a shell on the box.
- **File-read exfiltration** (`cat ~/.hermes/.env`, `curl -d @...`) is not
  stopped by the env sanitization — that only covers the child process
  environment. The boundary is who can reach `/api/ws`.
- **MCP stdio servers run unsandboxed** with your user's filesystem access. For
  genuine containment of a malicious MCP server, adopt the two-network egress
  isolation described in `docs/security/network-egress-isolation.md` (not part
  of this bundle).
- **The desktop app's CSP is still Report-Only** and has prerequisites before it
  can be enforced (see the security PR / `apps/desktop/electron/csp.ts`). The
  dashboard CSP can be enforced independently by setting `HERMES_CSP_ENFORCE=1`
  on the dashboard service after a clean Report-Only cycle.

This is a defensible posture for personal, single-operator use. It is not a
multi-tenant or third-party-data posture, because the session token is a
shell-equivalent credential with no revocation.
