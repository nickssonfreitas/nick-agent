# Extension surfaces

The edges capability is added at, instead of the middle. Every rung of the Footprint
Ladder above "new core tool" lands in one of these.

* [Providers and models](providers-and-models.md) - Inference backends, credential pools and model routing.
* [Plugins](plugins.md) - The three plugin discovery systems and the boundaries between them.
* [Skills and curator](skills-and-curator.md) - Bundled and agent-created skills, the skills hub, and the curator that keeps them alive.
* [MCP and ACP](mcp-and-acp.md) - Hermes as MCP client, as MCP server, and as an editor agent over ACP.
* [Scheduling](scheduling.md) - Scheduled jobs and the multi-agent kanban work queue.
* [Middleware](middleware.md) - The hook points a plugin can wrap around a call, and the ordering rules between stacked middleware.
* [Observer hooks](observer-hooks.md) - The observation points the agent emits and how a plugin subscribes without touching the loop.
* [Chronos managed-cron wire contract](chronos-cron-contract.md) - The agent-to-NAS contract for managed cron: message shapes, schedule ownership, failure modes.
* [Relay-to-connector contract](relay-connector-contract.md) - The relay/connector wire contract. Machine-enforced by a conformance test that reads this page.
