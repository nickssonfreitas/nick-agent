---
okf_version: "0.2"
---
# Hermes Agent Wiki

An LLM-first map of this codebase, published as an [Open Knowledge Format](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
0.2 bundle. Every page answers the same three questions for one subsystem: **what
files own it**, **how the pieces connect**, and **what breaks if you touch it wrong**.

This wiki is a map, not a rulebook. The contribution rubric, the Footprint Ladder,
the review standards and every "we don't want this" decision live in
[`AGENTS.md`](../AGENTS.md), which wins on any conflict. User-facing product
documentation lives in [`website/docs/`](../website/docs) and is published at
<https://hermes-agent.nousresearch.com/docs>.

New to the codebase, or an agent picking up a task cold? Read
[Architecture](concepts/architecture.md) end to end. It is the only page that assumes
no prior knowledge.

# Concepts

* [Architecture](concepts/architecture.md) - The whole system in one page: four surfaces, one agent core, the import chain, the lifecycle of a single message.
* [The five invariants](concepts/invariants.md) - The five properties that must hold across the whole codebase, and the pages that own each one.
* [Glossary](concepts/glossary.md) - Terms this codebase uses in a specific way, where the local meaning beats the generic industry one.
* [Wiki conventions](concepts/wiki-conventions.md) - How this bundle is structured, what OKF 0.2 requires of every page, and the three rules that keep the wiki from rotting.

# Core

* [Core](core/index.md) - The agent loop and the tool machinery that every surface drives.

# Surfaces

* [Surfaces](surfaces/index.md) - The five ways a human or a platform talks to the core.

# State and configuration

* [State](state/index.md) - Where durable state lives: sessions, memory, context and config.

# Extension surfaces

* [Extensions](extensions/index.md) - The edges capability is added at, instead of the middle.

# Working on the repo

* [Operations](operations/index.md) - Testing, CI, packaging, release, and deploying to a hardened VPS.

# Investigation and history

* [Research](research/index.md) - Investigations with a question, the evidence gathered, and a conclusion. Seven recorded, from the VPS comparison to the security-audit and static-analysis triage pages.
* [Decisions](decisions/index.md) - Decisions taken, the alternatives rejected, and the context that made them right. Four recorded.

# References

* [References](references/index.md) - The formats this wiki adopts: the OKF 0.2 specification it conforms to, and the LLM Wiki pattern it implements.

# Task routing

Skip the map and jump straight to the page that owns your task.

* [Add a capability of any kind](concepts/architecture.md#the-footprint-ladder-read-before-adding-anything) - Start at the Footprint Ladder; the highest rung that works wins.
* [Add a model tool](core/tools.md#adding-a-core-tool) - Two files: the tool module and the toolset wiring.
* [Add a slash command](surfaces/cli.md#slash-command-registry) - The CLI registry, not a new core tool.
* [Add a messaging platform](surfaces/gateway.md#adding-a-platform) - One `BasePlatformAdapter` subclass.
* [Add an inference provider](extensions/providers-and-models.md#adding-a-provider) - Prefer a model-provider plugin over a core adapter.
* [Add a config setting](state/config-and-profiles.md#adding-a-setting) - `config.yaml`, never a new `HERMES_*` env var.
* [Add or fix a skill](extensions/skills-and-curator.md) - Authoring standards are HARDLINE and live in AGENTS.md.
* [Change how the agent loop behaves](core/agent-core.md#the-loop) - Read the invariants first.
* [Change what the model sees in its prompt](core/agent-core.md#prompt-assembly-and-why-it-is-frozen) - Prompt caching is sacred.
* [Change the TUI or desktop chat UI](surfaces/tui.md) - The TUI and the desktop app are independent, not layered.
* [Debug "works in the CLI, broken in the gateway"](state/config-and-profiles.md#the-three-config-loaders) - Almost always one of the three loaders.
* [Debug "my tool never appears"](core/tools.md#why-a-tool-is-invisible) - Registered is not the same as exposed.
* [Deploy to a VPS](operations/vps-deployment.md) - The hardened bundle, top to bottom; automate it afterwards with vps-bootstrap.md.
* [Write or place a test](operations/testing-and-ci.md) - Never call pytest directly.
