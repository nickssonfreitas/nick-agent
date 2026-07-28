---
type: Invariant
title: The five invariants
description: The five properties that must hold across the whole codebase, and the pages that own each one.
tags: [orientation, invariants, prompt-caching]
status: stable
sources:
  - id: repo
    resource: git:5b69d1e99
    title: hermes-agent @ 5b69d1e99 (branch dev)
    last_modified: 2026-07-28
verified:
  - { by: human:nickssonfreitas, at: 2026-07-28 }
stale_after: 2026-10-28
---
# The five invariants

Five properties hold everywhere in this codebase. Every subsystem page repeats the
ones relevant to it; they are collected here because breaking any of them is the
most expensive class of mistake in this repo.

**Map, not policy.** Rules and rationale live in [`AGENTS.md`](../../AGENTS.md),
which wins on any conflict.

## 1. Prompt caching is sacred

A conversation reuses a cached prefix every turn. Never mutate past context, swap
toolsets, or rebuild the system prompt mid-conversation. Context compression is the
sole exception.

See [Agent core § Prompt assembly](../core/agent-core.md#prompt-assembly-and-why-it-is-frozen).

## 2. Role alternation is strict

No two same-role messages in a row, and no synthetic user message injected mid-loop.
This is a consequence of invariant 1: a broken alternation forces a prefix rebuild.

See [Agent core § The loop](../core/agent-core.md#the-loop).

## 3. Every core tool ships on every API call

There is no lazy loading of core tools. A tool added to `_HERMES_CORE_TOOLS` costs
tokens on every request for every user, forever. That is why the Footprint Ladder
exists and why a new core tool is its last rung.

See [Architecture § The Footprint Ladder](architecture.md#the-footprint-ladder-read-before-adding-anything)
and [Tools](../core/tools.md).

## 4. Paths are profile-aware

Use `get_hermes_home()` and `display_hermes_home()`, never a hardcoded `~/.hermes`
or `Path.home() / ".hermes"`. A hardcoded path silently writes outside the active
profile, which is how profile isolation leaks.

See [Config and profiles](../state/config-and-profiles.md).

## 5. Plugins never touch core files

A plugin that needs a core edit is a plugin that has outgrown the plugin surface.
The fix is to widen the generic surface, not to special-case one plugin.

See [Plugins](../extensions/plugins.md).
