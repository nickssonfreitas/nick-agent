---
type: Pattern
title: LLM Wiki - the pattern this wiki implements
description: A navigation and synthesis layer over a large corpus, readable by a person and by an agent.
tags: [llm-wiki, pattern, documentation, navigation]
sources:
  - id: llms-txt
    resource: https://llmstxt.org/
    title: "llms.txt - an LLM-readable index convention (Jeremy Howard, 2024)"
    last_modified: 2024-09-03
  - id: okf
    resource: okf.md
    title: Open Knowledge Format v0.2 (the format that gives this pattern a body)
    last_modified: 2026-07-28
  - id: diataxis
    resource: https://diataxis.fr/
    title: "Diátaxis - organising documentation by reader intent"
    last_modified: 2024-01-01
generated:
  by: claude-code/opus-5
  at: 2026-07-28T00:00:00Z
status: draft
stale_after: 2027-01-31
---
# LLM Wiki - the pattern this wiki implements

> **Honesty notice:** unlike [OKF](okf.md), "LLM Wiki" is **not a published
> specification**. It is the name we give an organising pattern here, and what follows
> describes how this repo practises it, not an external standard anyone can cite.
> Commentary around OKF's launch traces the pattern to an Andrej Karpathy gist; that
> lineage is reported by secondary sources, not something this page verified. The
> external references in the frontmatter (`llms.txt`, Diátaxis) are real adjacent
> conventions, but none of them defines "LLM Wiki".

## The problem

A mature project accumulates context faster than it can organise it. This fork carries
four surfaces, three plugin systems, a hardened deploy bundle and a security-audit
history, spread across Python, TypeScript, compose files and CI. None of it was
disorganised; each area had its own structure. What was missing was an **entry point**.
Finding where something lived cost a scan, and that cost fell entirely on whoever
arrived later, person or agent.

Traditional documentation handles this badly for two reasons. It is written for linear
reading, and an agent does not read linearly, it filters. And it duplicates content to
provide context, which guarantees divergence on the first commit nobody propagated.

## The four principles

**1. Navigation, not duplication.** Every page points at the canonical document and
summarises only enough for the reader to decide whether to open it. The wiki is the
source of truth for nothing. The authority order is explicit: `AGENTS.md` holds the
rules, `website/docs/` holds the product documentation, the code holds the behaviour,
and the wiki maps how they connect. On conflict the source wins and the page is the
thing that is broken. This is why every page links into `AGENTS.md` instead of quoting
it: a rule that changes there cannot rot here, because no copy was ever taken.

**2. Provenance is explicit.** Every concept document declares who produced it, from
what sources, verified by whom, and with what expiry. This is where the pattern meets
[OKF v0.2](okf.md), which supplies ready-made vocabulary. Without provenance,
agent-generated content is indistinguishable from human-verified content, and the
difference matters precisely when someone is about to decide something on top of it.
This page is a live example: it carries `generated` and no `verified`, so it reads as
`unverified` until a person checks it.

**3. What can be derived, is derived, and the derivation has a gate.** Two things here
are generated rather than written: `llms-wiki.txt`, the whole bundle in one file, and
the reading order, which comes from the `index.md` files rather than a list somebody
maintains by hand. Both are checked by `generate_wiki_llms.py --check`, which fails on
an unreachable page, a broken link or anchor, unparseable frontmatter, a missing `type`,
or a `resource:` naming a file that no longer exists. A generated artifact without a
drift gate is an artifact that lies quietly.

The strongest form of this principle in the repo is not in the wiki tooling at all.
[`extensions/relay-connector-contract.md`](../extensions/relay-connector-contract.md) is
read by `tests/gateway/relay/test_contract_doc_conformance.py`, which asserts the Python
dataclasses match what the page documents. Editing that page can fail CI. A document the
test suite reads cannot drift from the code, which is the ceiling this pattern can reach
and the exception rather than the rule.

**4. Determinism is a prerequisite for the gate.** The bundle carries no build
timestamp, and the generator is a pure function of the pages. If the output were dated,
every run would report drift, the gate would become noise, and somebody would switch it
off. The freshness signal lives in `stale_after` and `verified`, per page, where a human
has to look at it.

## Neighbouring conventions

| Convention | What it solves | Relationship |
|---|---|---|
| [`llms.txt`](https://llmstxt.org/) | An LLM-readable index at the root of a site or repo | **Complementary.** It is an entry file; the wiki is the body. This repo ships `website/static/llms.txt` and `llms-full.txt` for the product docs, and `wiki/llms-wiki.txt` for the code. |
| [OKF v0.2](okf.md) | Frontmatter vocabulary with trust signals | **Adopted.** It gives principle 2 a concrete shape. |
| [Diátaxis](https://diataxis.fr/) | Organising docs by reader intent (tutorial, how-to, reference, explanation) | **Partial.** The split between `website/docs/` ("how do I do this") and the wiki ("why is it like this") is the how-to/explanation boundary. Inside the wiki we do not follow the four quadrants. |

## How this repo implements it

| Principle | Where it lives |
|---|---|
| Navigation, not duplication | [Wiki conventions](../concepts/wiki-conventions.md), [Architecture](../concepts/architecture.md) |
| Explicit provenance | OKF frontmatter on every concept document; [OKF](okf.md) for the field contract |
| Derived half, with a gate | `scripts/generate_wiki_llms.py`, and `--check` in the same run |
| A page the tests enforce | [Relay-to-connector contract](../extensions/relay-connector-contract.md) |
| Determinism | The generator takes no clock reading; ordering comes from the indexes |

## What this pattern does not solve

It does not replace semantic search. For exploratory questions, *what talks to X*, *where
is this used*, the graph in `graphify-out/` answers better, because it carries relations
the prose never writes down. The wiki gives **context and reading order**; the graph
finds **connections**. They coexist.

And it does not stop a page from going stale. It only makes the age **visible**, through
`stale_after`, `verified` and `sources[].last_modified`. Whoever reads still has to
judge.

## Related

[References](index.md) · [OKF v0.2](okf.md) · [Wiki conventions](../concepts/wiki-conventions.md)
