---
type: Process
title: VPS bootstrap and what stays manual
description: "How the deploy procedure splits once it is automated: everything that creates a secret stays manual, everything that moves an image digest goes to CI."
resource: deploy/vps-hardened/remote-deploy.sh
tags: [operations, deployment, vps, ci]
status: stable
sources:
  - id: repo
    resource: git:44d22ee39
    title: hermes-agent @ 44d22ee39 (branch dev)
    last_modified: 2026-07-28
  - id: origin
    resource: deploy/vps-hardened/BOOTSTRAP.md
    title: Original location before the 2026-07-28 wiki migration
    last_modified: 2026-07-28
verified:
  - { by: agent:claude-opus-5, at: 2026-07-28 }
stale_after: 2026-10-28
---
# Bootstrap — the one-shot half of the VPS deploy

[Hardened VPS deployment](vps-deployment.md) describes the whole deployment as one
linear procedure. Once you
want deploys to be automated, that procedure splits cleanly in two, and the
split is not arbitrary: **everything that creates or handles a secret stays
manual, everything that moves an image digest gets automated.**

| Phase | Runs | How | Frequency |
|---|---|---|---|
| Bootstrap | by hand, over SSH | this page | once per VPS |
| Build image | in CI, on request | `.github/workflows/publish-image.yml` | per version deployed |
| Deploy | in CI | `.github/workflows/deploy-vps.yml` | every release |
| Verify | in CI and by hand | `verify.sh` | every deploy |
| Rollback | in CI, automatic on a failed check | `remote-deploy.sh rollback` | on failure |
| Disaster recovery | by hand, interactive | `hostinger-api.sh` | rarely, deliberately |

The reason `hermes login` never moves into CI is the deployment page's step 5: it writes
provider keys *inside* the container so they never appear in `docker inspect`
or `/proc/1/environ`. Piping them through a CI secret would undo exactly the
property that step exists to create. Same for the dashboard password hash —
generating it in a pipeline puts the plaintext in a runner's memory and its
logs' blast radius for no gain, since it changes roughly never.

---

## Where the Hostinger MCP fits (and where it does not)

The Hostinger API MCP server is an **agent** surface: it speaks stdio or HTTP
to Claude, Cursor and similar clients. A GitHub Actions job cannot call it.
Both it and `hostinger-api.sh` hit the same REST API
(`https://developers.hostinger.com/api/vps/v1/...`, `Authorization: Bearer`),
so treat the MCP as the interactive console and the script as the automation.

Use the MCP interactively for the things that are genuinely one-off and benefit
from a conversation — provisioning the VM, shaping firewall rules, reading
metrics and action history when something looks wrong:

```jsonc
{
  "mcpServers": {
    "hostinger-vps": {
      // The modular binary, NOT `hostinger-api-mcp`. The full server exposes
      // 268 tools spanning DNS, domains, billing, email and website deletion;
      // this one exposes the 62 VPS tools. Narrowing the surface also keeps
      // you under the ~100-tool ceiling most clients impose.
      "command": "hostinger-vps-mcp",
      "env": { "HOSTINGER_API_TOKEN": "..." }
    }
  }
}
```

Two things to be deliberate about.

**The token is account-wide.** It reaches DNS, billing and the delete
endpoints, and I could not confirm from Hostinger's published docs that tokens
can be scoped down — assume they cannot until you verify it in hPanel. It is a
root credential for your hosting account.

**Do not run this MCP inside the Hermes instance that lives on the VPS.** That
wires an agent which accepts untrusted input to `VPS_recreateVirtualMachineV1`
and `VPS_setRootPasswordV1` on the box it is running on. Run it from your
laptop, pointed at the VPS from outside.

---

## Step 0a — Publish the image, once

The deploy needs an image that contains **this fork's** code. Upstream's
`nousresearch/hermes-agent` does not: the fork carries the dashboard CSP
(`hermes_cli/web_server.py`) and the credential-mode clamp
(`hermes_cli/config.py`), and the deployment page's whole posture assumes both are
present. Deploying upstream's image would give you hardened configuration around
unhardened code, and `verify.sh` check 6 would reject it — correctly.

```bash
gh workflow run publish-image.yml
gh run watch                     # builds, runs tests/docker/, then pushes
```

It publishes to `ghcr.io/nickssonfreitas/nick-agent` and prints a `sha-<short>`
tag in the run summary. That tag is what you feed to the deploy. It authenticates
with the workflow's own `GITHUB_TOKEN`, so there is no registry secret to create.

The image is gated on the `tests/docker/` suite passing against the exact bytes
that get pushed, which is why the run takes a while. Publishing first and testing
after would ship a broken image and then tell you about it.

## Step 0b — Make the package pullable from the VPS

On the first publish, check the package's visibility at
`https://github.com/nickssonfreitas/nick-agent/pkgs/container/nick-agent`.

**Public** is the simple path and is safe here: the image is built from public
source and holds no credentials — provider keys live in the volume at runtime
(step 5), never in a layer. The VPS then pulls with no login at all.

**Private** works too, but the VPS needs a read-only credential:

```bash
# on the VPS, with a PAT scoped to read:packages only
echo "$GHCR_READ_TOKEN" | docker login ghcr.io -u nickssonfreitas --password-stdin
```

That writes `~/.docker/config.json` on the box. It is a fourth place holding a
secret, on the machine you are trying to keep tight — which is the argument for
public unless you have a reason.

## Step 0 — Prerequisites

- VPS with Docker Engine and the compose plugin.
- Domain with an A/AAAA record on the VPS; ports 80 and 443 open, nothing else.
- The encrypted volume from the deployment page's step 1. Do this first — Hermes stores
  conversations as plaintext SQLite and nothing in the app encrypts at rest.

> **Docker Manager is a separate question.** The MCP's Docker Compose tools
> (`VPS_createNewProjectV1` and friends) need Hostinger's Docker Manager
> enabled on the VM. This bundle does not use them, and deliberately: those
> tools ship the compose file *contents* through Hostinger's API, and this
> compose file interpolates `${HERMES_DASHBOARD_PW_HASH}` and
> `${HERMES_DASHBOARD_SECRET}`. The SSH path keeps those on your box.

## Step 1 — Place the bundle on the VPS

```bash
sudo mkdir -p /opt/hermes/vps-hardened /opt/hermes/backups
sudo chown -R "$USER" /opt/hermes
scp -r deploy/vps-hardened/* user@vps:/opt/hermes/vps-hardened/
```

The CI workflow pipes `remote-deploy.sh` and `verify.sh` from the repo checkout
on every run, so those two do not need to be current on the VPS. The compose
file, `Caddyfile` and `.env` do — they are the machine's own state.

Override the location with `HERMES_DEPLOY_DIR` if you put it elsewhere.

## Step 2 — Pin the first digest

CI swaps digests, but it cannot create the first one; `remote-deploy.sh`
refuses to run against a compose file that still says
`REPLACE_WITH_PINNED_DIGEST`. Do the deployment page's step 2 by hand once.

## Step 3 — Generate the dashboard password hash

the deployment page's step 3, unchanged. Interactive `getpass`, and it stays that way.

## Step 4 — Fill in `.env` and the Caddyfile

the deployment page's step 4. Two invariants CI will check but cannot fix for you:

- `.env` is `0600` and never committed.
- The `Caddyfile` has **no** `header_up Host` line. That single line collapses
  the auth gate — `verify.sh` check 2 exists to catch it.

## Step 5 — Provider credentials, inside the container

the deployment page's step 5: `docker exec -it hermes hermes login`. This is the step
that must never be automated. `verify.sh` check 6 confirms the result
(`/opt/data/.env` at `600 hermes`) without ever reading the file.

## Step 6 — Retention and MCP settings

the deployment page's step 6, then hand-edit the `mcp_servers` block for your actual
servers, keeping `sampling.enabled: false` and a minimal `tools.include` on
each. `verify.sh` checks 7 and 8 report on this as warnings rather than
failures, precisely because the block is yours to shape.

## Step 7 — TLS, then prove it

```bash
docker compose up -d caddy
./verify.sh all "$YOUR_DOMAIN"
```

Do not connect personal data until the hard checks pass.

---

## Step 8 — Hand the deploy to CI

Create a deploy user and key on the VPS (do not reuse a personal key):

```bash
# on the VPS
sudo adduser --disabled-password --gecos '' hermes-deploy
sudo usermod -aG docker hermes-deploy      # docker group == root-equivalent
sudo mkdir -p ~hermes-deploy/.ssh && sudo chmod 700 ~hermes-deploy/.ssh
sudo chown -R hermes-deploy /opt/hermes

# on your laptop
ssh-keygen -t ed25519 -f ./hermes-deploy-key -C 'github-actions deploy'
ssh-copy-id -i ./hermes-deploy-key.pub hermes-deploy@VPS_HOST
ssh-keyscan -H VPS_HOST                   # capture for VPS_SSH_KNOWN_HOSTS
```

> Membership in `docker` is equivalent to root on that host. This key deploys
> and nothing else; it does not belong on a laptop that also browses the web.

Repository secrets:

| Secret | Value |
|---|---|
| `VPS_SSH_HOST` | VPS hostname or IP |
| `VPS_SSH_USER` | `hermes-deploy` |
| `VPS_SSH_PORT` | SSH port (optional, defaults to 22) |
| `VPS_SSH_KEY` | contents of the private key |
| `VPS_SSH_KNOWN_HOSTS` | `ssh-keyscan` output — pinned, never `StrictHostKeyChecking=no` |
| `HERMES_DOMAIN` | the domain in the Caddyfile |

Do not paste those values into the GitHub UI by hand. The repo ships
`scripts/setup-deploy-secrets.sh`, which reads a local `.env.deploy` and writes
straight into the `production` environment, so the credentials go from your disk
to GitHub and nowhere else — not into a commit, not onto a command line where
`ps` could read them, not through a chat transcript:

```bash
cp deploy/vps-hardened/.env.deploy.example .env.deploy
$EDITOR .env.deploy && chmod 600 .env.deploy
scripts/setup-deploy-secrets.sh --check   # validate and report, writing nothing
scripts/setup-deploy-secrets.sh           # write them
```

`.env.deploy.example` deliberately carries neither the provider API keys (those go
inside the container, step 5) nor `HOSTINGER_API_TOKEN`, which is an account-wide
credential and does not belong in CI.

Gate the `production` environment with required reviewers, then dry-run before
trusting it:

```bash
gh workflow run deploy-vps.yml -f image_tag=v1.2.3 -f dry_run=true
```

A dry run resolves the digest and runs the full checklist against what is
already live without changing anything — it is also the cheapest way to find
out that a check broke for an unrelated reason.

---

## What rollback actually restores

`remote-deploy.sh rollback` re-pins the previous digest and brings the stack
back up. It does **not** restore the database, and CI says so loudly when it
fires. That is the honest behaviour: reverting the schema underneath a database
the newer image may have already migrated is not something a pipeline should
decide unattended.

Each deploy writes a pre-deploy copy of `state.db` and `kanban.db` to
`/opt/hermes/backups/<timestamp>/`, with the path to the newest in
`/opt/hermes/backups/.latest`. If a rolled-back image had migrated the schema,
restore that copy by hand.

Those backups contain full conversation history and are not covered by
retention. Rotate and protect them exactly as carefully as the live database —
The deployment page makes the same point and it is worth repeating here, because a
pipeline that writes a new one on every deploy will quietly accumulate them.

## Residual risk

Automating the deploy does not change the risk posture on the deployment page, and it
adds one item. Re-read that section — `shell.exec` and `/api/pty` are RCE by
design for anyone holding a session token, and sessions cannot be revoked
without rotating `HERMES_DASHBOARD_BASIC_AUTH_SECRET` and restarting.

The new item: **the deploy key is a root-equivalent credential held by GitHub
Actions.** Anyone who can trigger this workflow, or who can land a commit that
changes `remote-deploy.sh`, can run arbitrary code on the VPS. That is why the
workflow is dispatch-only and bound to a reviewable `production` environment,
and why branch protection on this repo is part of the deployment's security
boundary rather than a nicety.
