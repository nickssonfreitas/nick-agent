# Skills and curator

`skills/`, `optional-skills/`, `tools/skills_hub.py`, `agent/curator.py` — on-demand
knowledge documents and their lifecycle.

**Map, not policy.** The authoring standards are HARDLINE and live in
[`AGENTS.md` § Skills](../AGENTS.md#skills). Verified against `5b69d1e99`
(2026-07-28).

## What a skill is

A directory with a `SKILL.md` the agent loads on demand, plus optional `scripts/`,
`references/` and `templates/`. Skills are the **second rung** of the
[Footprint Ladder](architecture.md#the-footprint-ladder-read-before-adding-anything):
a CLI command plus a skill costs zero model-tool footprint, which is why so much
capability lands here instead of as a tool.

## Two in-repo surfaces

| Directory | Loaded | Categories |
|---|---|---|
| `skills/` | By default | `apple`, `autonomous-ai-agents`, `computer-use`, `creative`, `data-science`, `dogfood`, `email`, `github`, `hermes-desktop-plugins`, `hermes-themes`, `index-cache`, `media`, `mlops`, `note-taking`, `productivity`, `research`, `smart-home`, `social-media`, `software-development`, `yuanbao` |
| `optional-skills/` | Only after `hermes skills install official/<category>/<skill>` | `autonomous-ai-agents`, `blockchain`, `communication`, `creative`, `devops`, `dogfood`, `email`, `finance`, `gaming`, `health`, `mcp`, `migration`, `mlops`, `payments`, `productivity`, `research`, `security`, `software-development`, `web-development` |

When reviewing a skill PR, check which directory it targets: heavy-dependency or
niche skills belong in `optional-skills/`. The adapter for those is
`OptionalSkillSource` in `tools/skills_hub.py`.

User-installed and agent-created skills live under `get_hermes_home()/skills/`.

## The hub

`tools/skills_hub.py` (~4.3k lines) is a source-abstraction layer: `SkillSource` is
an ABC and each catalog is a subclass.

| Source | Fetches from |
|---|---|
| `GitHubSource` | GitHub taps (`owner/repo`, optional `--path`) |
| `OptionalSkillSource` | This repo's `optional-skills/` |
| `WellKnownSkillSource`, `UrlSource` | Well-known locations and direct URLs |
| `SkillsShSource`, `BrowseShSource`, `ClawHubSource`, `ClaudeMarketplaceSource`, `LobeHubSource` | Third-party catalogs |

`HubLockFile` pins what was installed. `hermes skills` is the user surface
(`hermes_cli/subcommands/skills.py`, `hermes_cli/skills_hub.py`).

### Taps

Third-party skill repos are **taps**, not vendored copies. Defaults live in
`GitHubSource.DEFAULT_TAPS`; users add their own with
`hermes skills tap add owner/repo [--path skills/sub/]`.

Three facts that cause real bugs:

- **The scanner lists exactly one directory level** at the tap path. A repo that
  nests skills by category needs one tap per category. A single tap at `skills/` on a
  nested repo silently finds nothing, because the category directories carry no
  `SKILL.md` of their own.
- **A bare `owner/repo` resolves to a root-level `SKILL.md`**, which is the
  single-skill repo shape. Collection repos have no root `SKILL.md` and fall through
  to path lookup.
- **Size gate.** `_list_skills_in_repo` fetches one `SKILL.md` per skill directory,
  so a tap costs one GitHub API call per skill on every cache miss. At ~800 skills a
  single browse burns roughly a sixth of the authenticated 5000/hour budget and
  cannot complete at all on the unauthenticated 60/hour limit. Rough ceiling for a
  default tap: a few dozen skills. Big catalogues stay opt-in.

Adding a default tap means **two** edits that must be kept in sync by hand: a label
in `GITHUB_TAP_PROVIDERS` (`tools/skills_hub.py`) and its twin in `GITHUB_TAP_LABELS`
(`website/scripts/extract-skills.py`). Labels must be single tokens, because each
becomes a `--source` filter value.

### Trust

`tools/skills_guard.py` scans a skill before install and scores it. Do **not** add
third-party repos to `TRUSTED_REPOS`. Community trust is the correct posture:
`INSTALL_POLICY` then blocks anything scored `caution` or `dangerous`.
`should_allow_install()` is the decision function; `format_scan_report()` is what the
user sees.

## SKILL.md frontmatter

Standard fields: `name`, `description`, `version`, `author`, `license`, `platforms`
(OS gating: `[macos]`, `[linux, macos]`, …), `metadata.hermes.tags`,
`metadata.hermes.category`, `metadata.hermes.related_skills`,
`metadata.hermes.config` (config keys the skill needs, stored under
`skills.config.<key>`, prompted at setup, injected at load). Top-level `tags:` and
`category:` are accepted and mirrored from `metadata.hermes.*` by the loader.

## Authoring standards (summary — the authority is AGENTS.md)

Reviewers reject PRs that violate these:

1. **`description` ≤ 60 characters**, one sentence, ends with a period, no marketing
   words, does not repeat the skill name.
2. **Reference native Hermes tools, not shell utilities.** `search_files` not `grep`,
   `read_file` not `cat`, `patch` not `sed`. Name MCP servers explicitly and document
   setup under `## Prerequisites`.
3. **`platforms:` audited against actual script imports.** POSIX-only primitives
   (`fcntl`, `termios`, `os.setsid`, `/proc`, `osascript`, `systemctl`) must be
   declared. Prefer fixing cross-platform first.
4. **`author` credits the human contributor first.**
5. **Modern section order**: title, 2-3 sentence intro, `## When to Use`,
   `## Prerequisites`, `## How to Run`, `## Quick Reference`, `## Procedure`,
   `## Pitfalls`, `## Verification`. ~200 lines for a complex skill, ~100 for a
   simple one.
6. **Scripts in `scripts/`, references in `references/`, templates in
   `templates/`.** Ship a helper script rather than expecting the model to re-derive
   a parser every call.
7. **Tests at `tests/skills/test_<skill>_skill.py`**, stdlib + pytest +
   `unittest.mock` only, no live network.
8. **`.env.example` additions isolated to a clearly delimited block.**

[`AGENTS.md`](../AGENTS.md#skill-authoring-standards-hardline) points at a full
salvage checklist for external skill PRs, in the `hermes-agent-dev` skill at
`references/new-skill-pr-salvage.md`. **That skill is not in this tree** and is not
installed under `~/.hermes/skills/` either, so treat the checklist as unavailable
here and work from the eight standards above.

## Skills as slash commands

`agent/skill_commands.py` scans the skills directory and exposes each skill as a
slash command, shared by the CLI and the gateway. The invocation is injected as a
**user message**, never into the system prompt, to preserve prompt caching. The TUI
and desktop surface the same commands through `commands.catalog` and
`complete.slash`.

## Curator

Background maintenance for **agent-created** skills. Users never lose a skill:
archives go to `~/.hermes/skills/.archive/` and are restorable.

| Piece | File |
|---|---|
| Review loop, auto-transitions, LLM review prompt | `agent/curator.py` |
| Pre-run tar.gz snapshots | `agent/curator_backup.py` |
| CLI (`hermes curator status/run/pause/resume/pin/unpin/archive/restore/prune/backup/rollback`) | `hermes_cli/curator.py` |
| Usage telemetry sidecar `~/.hermes/skills/.usage.json` | `tools/skill_usage.py` |

Telemetry per skill: `use_count`, `view_count`, `patch_count`, `last_activity_at`,
`state` (active / stale / archived), `pinned`.

**Invariants:**

- Only touches skills with `created_by: "agent"` provenance. Bundled and
  hub-installed skills are off-limits.
- Never deletes. The maximum destructive action is archive.
- Pinned skills are exempt from every auto-transition and from the LLM review pass.
- `skill_manage(action="delete")` refuses pinned skills, while patch, edit,
  `write_file` and `remove_file` still go through so the agent can keep improving a
  pinned skill.

Config under `curator:`: `enabled`, `interval_hours`, `min_idle_hours`,
`stale_after_days`, `archive_after_days`, `backup.*`. User-facing documentation:
`website/docs/user-guide/features/curator.md`.

## Pitfalls

- **No pagination on instructional tools.** Skill-loading tools must never grow
  `offset`/`limit`; models read page one and skip the rest.
- **The two tap-label maps drift.** They are duplicated across the Python runtime and
  the docs-site build on purpose; there is no generator.
- **A skill install that mutates prompt state is cache-aware.** `--now` is opt-in;
  the default takes effect next session.

## Where to touch for…

| Task | Start at |
|---|---|
| Write a skill | `skills/<category>/<name>/SKILL.md` |
| Add a heavy or niche skill | `optional-skills/` |
| Add a skill catalog | a `SkillSource` subclass in `tools/skills_hub.py` |
| Add a default tap | `DEFAULT_TAPS` + both label maps |
| Change install safety | `tools/skills_guard.py` |
| Change lifecycle behavior | `agent/curator.py`, `tools/skill_usage.py` |

## Related

[Tools](tools.md) · [Memory and context](memory-and-context.md) · [CLI](cli.md) · [Plugins](plugins.md) · [Index](index.md)
