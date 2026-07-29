# nick-agent

A personal, self-hosted fork of **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** by
[Nous Research](https://nousresearch.com), packaged to run on an internet-facing
VPS with personal data and MCP servers attached.

Upstream builds the agent. This fork answers a narrower question: *what does it
take to run it on a box that holds my conversation history and my provider
credentials, and keep it updated without hand-deploying every time?*

If you want the agent itself — install it, use it, learn what it does — go
upstream. The [documentation](https://hermes-agent.nousresearch.com/docs/) there
is maintained and thorough, and nothing here replaces it.

---

## What this fork adds

**A deployment that is hardened by default.** `deploy/vps-hardened/` is a compose
bundle with no `network_mode: host`, the image pinned by digest, `cap_drop: ALL`
plus only the capabilities s6-overlay actually needs, a read-only rootfs, resource
limits on every service, and a TLS reverse proxy that preserves the `Host` header.
Only the proxy publishes a port.

**A security checklist you can execute.** `verify.sh` is the deployment's
acceptance test: it asserts the auth gate is engaged, that no session token
reaches an anonymous client, that no app port is exposed, that the image is
pinned, and that credentials on disk are owner-only. It runs by hand and as the
gate in CI, so what blocks a deploy and what you check manually cannot drift apart.

**Continuous deployment with a rollback that fires on its own.** A push to `main`
builds an image from that commit, gates it on the docker integration suite,
deploys it, runs the checklist, and rolls back to the previous digest if a hard
check fails.

**An architecture wiki written for LLMs.** [`wiki/`](wiki/index.md) maps the
codebase one page per subsystem — entry points with file references, how the pieces
connect, and what breaks if you touch them wrong — and links into `AGENTS.md` for the
rules rather than restating them. It is an [Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
0.2 bundle: pages carry YAML frontmatter and are grouped into `concepts/`, `core/`,
`surfaces/`, `state/`, `extensions/` and `operations/`, alongside `research/` and
`decisions/`. `wiki/llms-wiki.txt` is the whole thing in one file for dropping into a
model's context. Upstream's `llms.txt` covers the product documentation; this covers
the code.

**Code-layer security fixes** that upstream's published image does not carry —
dashboard CSP, a credential-mode clamp keeping `.env` at `0600` across rewrites,
SSRF flooring on provider validation, deep-link validation and log sanitisation in
the desktop app, path-traversal containment in the gitnexus proxy. This is why the
deploy runs `ghcr.io/nickssonfreitas/nick-agent` and not the upstream image: the
hardened configuration assumes those fixes are present, and `verify.sh` check 6
tests one of them directly.

---

## Deploying

Read **[`wiki/operations/vps-bootstrap.md`](wiki/operations/vps-bootstrap.md)**
first. It splits the procedure along the line that matters: everything that
creates or handles a secret stays manual, everything that moves an image digest is
automated. [`wiki/operations/vps-deployment.md`](wiki/operations/vps-deployment.md)
is the full hardening walkthrough behind it.

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Bridge network, pinned digest, caps/read-only/limits, healthchecks |
| `Caddyfile` | TLS reverse proxy that preserves the `Host` header |
| `verify.sh` | The security checklist, executable — the CI gate and your manual check |
| `remote-deploy.sh` | Digest swap, pre-deploy database backup, health wait, rollback |
| `hostinger-api.sh` | Hostinger VPS REST API wrapper, disaster recovery only |
| `config-snippet.yaml` | Retention and MCP sampling settings to merge into the container config |

### Branch model

`dev` is the integration branch and has **no deploy trigger at all**, so work in
progress cannot reach the box by accident. `main` **is** the VPS: merging into it
is the release action. Build a `dev` image on demand when you want to test one:

```bash
gh workflow run publish-image.yml --ref dev
```

### Pipeline

```
push to main
  ├─ build    publish-image.yml   build → tests/docker/ → push to GHCR
  ├─ deploy   deploy-vps.yml      backup DBs → swap digest → health → verify.sh
  │                               → roll back if a hard check fails
  └─ summary                      tag, digest, outcome
```

The deploy receives a **digest**, never a tag. The tag is for humans; the digest
names the exact bytes the tests passed against and cannot be repointed in between.

### One-time setup

```bash
cp deploy/vps-hardened/.env.deploy.example .env.deploy
$EDITOR .env.deploy && chmod 600 .env.deploy
scripts/setup-deploy-secrets.sh --check   # validate without writing
scripts/setup-deploy-secrets.sh           # write into the production environment
```

The credentials go from your disk into GitHub and nowhere else. The script streams
every value over stdin rather than argv, so nothing lands where `ps` can read it,
and `.env.deploy` is gitignored — a rule the bare `.env` pattern does **not** cover.

---

## What the deploy does not close

Stated plainly, because a hardened-sounding deployment invites the wrong
assumptions:

- **`shell.exec` and `/api/pty` are RCE by design** for anyone holding a valid
  session token, and sessions cannot be revoked without rotating the signing
  secret and restarting. Give dashboard access only to people you would give a
  shell on the box.
- **Conversations are plaintext SQLite.** Nothing in the app encrypts at rest, so
  a provider disk snapshot captures everything in the clear. Use an encrypted
  volume. This is the one risk the application cannot close for you.
- **Rollback restores the image, not the database.** Neither `state.db` nor
  `kanban.db` has a schema-downgrade guard, which is why every deploy takes a
  backup first and why restoring it stays a human decision.
- **MCP stdio servers run unsandboxed** with your user's filesystem reach.

This is a defensible posture for personal, single-operator use. It is not a
multi-tenant or third-party-data posture.

---

## Development

Python — activate the venv first:

```bash
uv pip install -e ".[all,dev]"
scripts/run_tests.sh                 # full suite, CI-parity
scripts/run_tests.sh tests/gateway/  # one directory
```

**Never call `pytest` directly.** `scripts/run_tests.sh` runs each test file in
its own subprocess and enforces the hermetic environment CI uses.

On Windows, use the PowerShell installer instead of the shell path above:

```powershell
scripts/install.ps1
```

JS/TS — install at the repo root, workspaces assume it:

```bash
npm install
npm run check    # typecheck + test across all workspaces
npm run fix      # eslint --fix + prettier
```

[`AGENTS.md`](AGENTS.md) is the canonical development guide: contribution rubric,
the Footprint Ladder, plugin and skill authoring, and the full testing policy.

---

## Upstream

This fork tracks [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent).
For the agent's own features, configuration, messaging gateway, skills, memory and
MCP integration, use their [documentation](https://hermes-agent.nousresearch.com/docs/)
— those pages describe the software this is built on, and they stay current.

## License

MIT — see [LICENSE](LICENSE). Hermes Agent is built by
[Nous Research](https://nousresearch.com); this fork keeps that license and that
credit.
