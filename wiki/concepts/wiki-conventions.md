---
type: Convention
title: Wiki conventions
description: How this bundle is structured, what OKF 0.2 requires of every page, and the three rules that keep the wiki from rotting.
tags: [orientation, okf, authoring]
status: stable
verified:
  - { by: human:nickssonfreitas, at: 2026-07-28 }
stale_after: 2026-10-28
---
# Wiki conventions

This wiki is an [Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
0.2 bundle: a directory tree of Markdown files with YAML frontmatter, readable by a
human with `cat` and by an agent without any tooling. `wiki/index.md` is the bundle
root and the only file allowed to declare `okf_version`.

This page is the local contract, kept short. The format itself and the pattern behind
it have their own pages: [OKF v0.2](../references/okf.md) for the field-by-field
specification and every local decision taken against it, and
[LLM Wiki](../references/llm-wiki.md) for why the wiki is shaped this way at all.

**Map, not policy.** The contribution rubric lives in [`AGENTS.md`](../../AGENTS.md).

## Directory layout

| Folder | Holds | `type` |
|---|---|---|
| `concepts/` | What is true everywhere: architecture, invariants, glossary | `Concept`, `Invariant`, `Glossary`, `Convention` |
| `core/` | The agent loop and the tool machinery every surface drives | `Subsystem` |
| `surfaces/` | The five ways a human or a platform talks to the core | `Surface` |
| `state/` | Where durable state lives: sessions, memory, config | `Subsystem` |
| `extensions/` | The edges capability is added at, instead of the middle | `Subsystem` |
| `operations/` | Working on the repo: testing, CI, packaging, release | `Process` |
| `research/` | Investigations with a question, evidence and a conclusion | `Research` |
| `decisions/` | Decisions taken and the context that made them right | `Decision` |
| `references/` | The external formats this wiki adopts, per OKF §6 | `Specification`, `Pattern` |

## What every page must carry

OKF requires exactly one thing: parseable YAML frontmatter containing a non-empty
`type`. Everything below `type` is optional under the spec but expected here.

```yaml
---
type: Subsystem                  # required by OKF; the only hard requirement
title: Agent core                # human-readable display name
description: One sentence.       # what the page owns, used in index listings
resource: run_agent.py           # the code this page documents, repo-relative
tags: [core, agent-loop]         # cross-cutting categorisation
status: stable                   # draft | stable | deprecated
sources:                         # provenance: what the page was verified against
  - id: repo
    resource: git:5b69d1e99
    title: hermes-agent @ 5b69d1e99 (branch dev)
    last_modified: 2026-07-28
verified:                        # trust: `human:` prefix means a person checked it
  - { by: human:nickssonfreitas, at: 2026-07-28 }
stale_after: 2026-10-28          # after this date, treat the page as suspect
---
```

`verified` replaces the `Verified against <sha>` prose line the flat wiki carried in
every page. A field a script can read beats a sentence a script cannot.

## Reserved files

`index.md` and `log.md` are reserved by OKF and are not concept documents.

- **`index.md`** exists at the bundle root and in every folder. It is a grouped list
  of links with descriptions (`* [Title](path) - description`), carrying no
  frontmatter except `okf_version` at the root. It is also the ordering authority:
  `scripts/generate_wiki_llms.py` reads the bundle in the order the index files link,
  so a page absent from an index is a page absent from the bundle.
- **`log.md`** at the bundle root records changes newest-first under ISO 8601 date
  headings.

## Links

Relative (`../core/tools.md`), not bundle-relative (`/core/tools.md`). The spec
recommends bundle-relative paths because they survive a move, but GitHub resolves a
leading `/` against the repository root rather than the bundle root, which would
break every link when browsing the repo. Portability loses to navigability here.

OKF tells *consumers* to tolerate broken links, because a broken link marks knowledge
not yet written rather than an error. That tolerance does not bind us as authors:
`generate_wiki_llms.py --check` still fails on a link or heading anchor that does not
resolve.

## Keeping this wiki honest

A wiki that drifts is worse than no wiki, because it lies with authority. Three rules
keep the drift bounded.

- **Never restate a rule.** Link to `AGENTS.md`. If a rule changes there, this wiki
  stays correct because it never held a copy.
- **Prefer symbols to line numbers.** Name the function; the line number is a
  courtesy for humans scrolling.
- **Describe the seam, not the implementation.** "The gateway hands the agent a
  session key and waits" survives a refactor. A paraphrase of a 40-line function does
  not.

When you change a subsystem's shape (a new file that owns something, a moved seam, a
removed invariant), update its page in the same commit and bump `verified`.

## The machine-readable bundle

`wiki/llms-wiki.txt` is every page concatenated in reading order, for dropping into a
model's context whole. Regenerate it after editing any page:

```bash
python scripts/generate_wiki_llms.py            # write the bundle
python scripts/generate_wiki_llms.py --check    # verify, write nothing
```

It is generated, not authored. Edit the pages; never edit the bundle. Two related
bundles cover product documentation rather than architecture:
`website/static/llms.txt` (curated index) and `website/static/llms-full.txt` (all of
`website/docs/` concatenated), both produced by `website/scripts/generate-llms-txt.py`
during the docs-site build.
