---
type: Subsystem
title: Agent core
description: The AIAgent class, the synchronous tool-calling loop, prompt assembly and context compression.
resource: run_agent.py
tags: [core, agent-loop, prompt-caching]
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
# Agent core

`run_agent.py` + `agent/` — the `AIAgent` class and the synchronous tool-calling
loop every surface drives.

**Map, not policy.** Rules live in [`AGENTS.md`](../../AGENTS.md).

## Scope

| Path | Owns |
|---|---|
| `run_agent.py` | The `AIAgent` class, its public methods, and thin forwarders into `agent/` |
| `agent/` | 118 modules: loop internals, prompt assembly, provider adapters, compression, memory, display, pricing |
| `batch_runner.py` | Parallel batch processing over the same agent |
| `trajectory_compressor.py` | Trajectory file compression |

Not here: tool registration and dispatch ([Tools](tools.md)), session persistence
([State and sessions](../state/sessions.md)), provider profiles
([Providers and models](../extensions/providers-and-models.md)).

## The extraction pattern (read this before hunting for a symbol)

`run_agent.py` is ~6.7k lines and used to be far larger. Most of its bulk has been
pulled into `agent/` modules that take the parent `AIAgent` as their first argument,
with a thin forwarder left behind on the class. So the symbol you `grep` for on
`run_agent` may execute somewhere else entirely.

| You look for | It really lives in | Forwarder |
|---|---|---|
| `AIAgent.__init__` body (~1.4k lines, 60+ params) | `agent/agent_init.py::init_agent` | `__init__` wraps it |
| `run_conversation` body (~3.9k lines) | `agent/conversation_loop.py` | `run_agent.py:6393` |
| Turn prologue (~470 lines of per-turn setup) | `agent/turn_context.py::TurnContext` | consumed by the loop |
| Tool-call execution, sequential and concurrent | `agent/tool_executor.py` | `_execute_tool_calls_*` |
| Non-streaming call, request kwargs, fallback activation | `agent/chat_completion_helpers.py` | thin methods |
| Trajectory conversion, argument repair, alternation repair | `agent/agent_runtime_helpers.py` | thin methods |

Two consequences that bite people:

- **Patch targets stay on `run_agent`.** The extracted modules reach back through
  the `run_agent` module (`_ra()`) for symbols that tests patch, precisely so
  existing patch targets keep working. If you move a symbol, check whether tests
  patch it on `run_agent`.
- **A stack trace pointing at `agent/conversation_loop.py` is still "the agent
  loop".** It is not a separate subsystem.

## Entrypoints

| Symbol | Location | Role |
|---|---|---|
| `AIAgent` | `run_agent.py:400` | The agent. One instance per conversation. |
| `AIAgent.__init__` | `run_agent.py:423` → `agent/agent_init.py` | 60+ parameters: credentials, routing, callbacks, session context, budget, credential pool. |
| `AIAgent.run_conversation` | `run_agent.py:6393` → `agent/conversation_loop.py` | Full interface. Returns a dict with `final_response` and `messages`. |
| `AIAgent.chat` | `run_agent.py:6450` | Simple interface. Returns the final response string. |
| `main()` | `run_agent.py:6477` | Direct script entry. |

## The loop

Entirely synchronous. The shape, stripped to its bones:

```python
while (api_call_count < self.max_iterations and self.iteration_budget.remaining > 0) \
        or self._budget_grace_call:
    if self._interrupt_requested:
        break
    response = client.chat.completions.create(model=model, messages=messages, tools=tool_schemas)
    if response.tool_calls:
        for tool_call in response.tool_calls:
            result = handle_function_call(tool_call.name, tool_call.args, task_id)
            messages.append(tool_result_message(result))
        api_call_count += 1
    else:
        return response.content
```

What the real loop adds around that: interrupt checks, an iteration budget separate
from `max_iterations` (default 90, shared with subagents), a one-turn grace call when
the budget runs out, provider retries and fallback activation, preflight and
mid-conversation compression, plugin `pre_llm_call` / `post_llm_call` hooks, and
post-turn nudges for background memory and skill review.

Messages are OpenAI-shaped (`{"role": "system|user|assistant|tool", ...}`) regardless
of the actual provider; adapters translate at the edge. Reasoning content is carried
in `assistant_msg["reasoning"]`.

### Tool execution

`agent/tool_executor.py` runs a turn's tool calls either sequentially or
concurrently. Both paths funnel into `handle_function_call()` in `model_tools.py`,
except for **agent-level tools** (todo, memory), which `run_agent.py` intercepts
*before* dispatch because they mutate agent state rather than reaching out to the
world. `tools/todo_tool.py` is the canonical example of that pattern.

## Prompt assembly, and why it is frozen

`agent/prompt_builder.py` (stateless functions) assembles identity, platform hints,
the skills index and context files. `AIAgent._build_system_prompt()` calls those and
combines the result with memory and ephemeral prompts.

This happens **once**, at the start of a conversation. After that the prefix is
byte-stable for the life of the conversation, because a long-lived conversation
reuses a cached prompt prefix on every turn and any mutation invalidates it and
multiplies the user's cost.

Concretely, do not:

- alter past context mid-conversation,
- change the toolset mid-conversation,
- reload memory or rebuild the system prompt mid-conversation,
- inject a synthetic user message mid-loop, or emit two same-role messages in a row.

The **only** sanctioned exception is context compression. Slash commands that mutate
system-prompt state (skills, tools, memory) must be cache-aware: they default to
deferred invalidation, taking effect next session, with an opt-in `--now` flag.
`/skills install --now` is the canonical pattern.

Skill slash commands are injected as a **user message**, not into the system prompt,
for exactly this reason (`agent/skill_commands.py`, shared by `cli.py` and
`gateway/run.py`).

Full statement: [`AGENTS.md` § Prompt Caching Must Not Break](../../AGENTS.md#prompt-caching-must-not-break).

## Context compression

`agent/context_compressor.py` (~4.6k lines) is the one path allowed to rewrite
history. It summarizes middle turns with a cheap auxiliary model while protecting
head and tail, keeps a structured summary template with resolved/pending question
tracking, and updates the summary iteratively across repeated compactions so
information survives several passes.

It is pluggable: `agent/context_engine.py` defines the ABC, selection is
`context.engine` in `config.yaml`, the built-in `"compressor"` is the default, and
exactly one engine is active. Third-party engines drop into
`plugins/context_engine/<name>/`.

Token accounting and context-length lookups live in `agent/model_metadata.py` (pure
utilities, no `AIAgent` dependency), used both by the compressor and by preflight
checks in the loop.

## Provider adapters

Everything internal speaks OpenAI-shaped messages. Provider-specific translation is
isolated at the edge, one adapter per API shape:

| Adapter | API |
|---|---|
| `agent/anthropic_adapter.py` | Anthropic Messages API. Handles API keys (`x-api-key`), OAuth setup tokens and Claude Code credentials (Bearer). |
| `agent/codex_responses_adapter.py` + `agent/codex_runtime.py` | Codex Responses API. |
| `agent/bedrock_adapter.py` | AWS Bedrock. |
| default | OpenAI-compatible chat completions. |

`api_mode` on `AIAgent.__init__` selects the path. Provider profiles, base URLs and
credential resolution are [Providers and models](../extensions/providers-and-models.md).

`agent/credential_pool.py` gives same-provider failover across multiple credentials;
`agent/error_classifier.py` decides what is retryable, what is fatal, and what should
trip a breaker.

## Auxiliary model work

`agent/auxiliary_client.py` (~8k lines) runs every side-LLM task that is not the main
conversation: curator reviews, vision, embeddings, title generation, session search.
Each task can pin its own provider, model, base URL, max tokens and reasoning effort
under `auxiliary:` in `config.yaml`; resolution order is in `_resolve_auto`.

When a side task starts using the user's expensive main model unexpectedly, that
function is where to look.

## Delegation

`tools/delegate_tool.py` spawns a subagent with an isolated context and terminal
session. Default is synchronous: the parent waits for the child's summary. With
`background=true` the parent gets a delegation id immediately and the result
re-enters the conversation later through the async-delegation completion queue.

- **Single:** `goal` (+ optional `context`, `toolsets`).
- **Batch:** `tasks: [...]`, each its own concurrent subagent, capped by
  `delegation.max_concurrent_children` (default 3).
- **Roles:** `leaf` (default) cannot delegate, clarify, use memory, send messages or
  schedule cron, but keeps `execute_code`. `orchestrator` keeps `delegate_task`,
  gated by `delegation.orchestrator_enabled` and bounded by
  `delegation.max_spawn_depth` (default 2).

Durability rule: a background delegation is detached from the turn but still
**process-local**. Work that must survive a restart belongs in `cronjob` or
`terminal(background=True, notify_on_complete=True)`.

## Pitfalls

- **`_last_resolved_tool_names` is a process-global** in `model_tools.py`.
  `_run_single_child()` in `delegate_tool.py` saves and restores it around subagent
  execution, so it can be transiently stale during a child run.
- **The `__init__` signature is not the contract.** ~60 parameters, most optional,
  many mutually constraining. Read `agent/agent_init.py` before adding one.
- **Alternation repair exists for a reason.** `repair_message_sequence` in
  `agent/agent_runtime_helpers.py` enforces the invariants; if you find yourself
  wanting to append a second user message, you are about to break a rule the repair
  function will fight you over.
- **Cron sessions pass `skip_memory=True`** by default and carry a 3-minute hard
  interrupt. An agent behaving differently under cron is usually this, not a bug.

## Where to touch for…

| Task | Start at |
|---|---|
| Change loop control flow, retries, budgets | `agent/conversation_loop.py` |
| Change per-turn setup | `agent/turn_context.py` |
| Change what the system prompt contains | `agent/prompt_builder.py` |
| Change compression behavior | `agent/context_compressor.py`, or write a context engine |
| Add a provider API shape | a new adapter in `agent/`, plus `api_mode` wiring |
| Change side-LLM routing | `agent/auxiliary_client.py::_resolve_auto` |
| Change subagent behavior | `tools/delegate_tool.py` |

## Related

[Architecture](../concepts/architecture.md) · [Tools](tools.md) · [Memory and context](../state/memory-and-context.md) · [Providers and models](../extensions/providers-and-models.md) · [Index](../index.md)
