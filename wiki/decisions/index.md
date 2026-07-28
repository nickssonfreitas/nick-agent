# Decisions

Decisions that shaped this project and are not recoverable from the code: hosting and
infrastructure choices, vendor selections, protocol and format commitments, things
deliberately not built. The code shows *what* was decided; these pages hold *why*, and
what the alternatives were.

A decision belongs here when someone six months from now would otherwise ask "why is
it like this?" and find no answer in git. Routine implementation choices do not.

Files are named `NNNN-slug.md`, numbered in the order they were taken and never
renumbered. Copy [`_template.md`](_template.md) to start one.

Decisions carry two independent states, because they are different questions:

* `status` - the OKF document lifecycle: `draft`, `stable`, `deprecated`.
* `decision_status` - what happened to the decision itself: `proposed`, `accepted`, `rejected`, `superseded`.

A superseded decision is never deleted. It stays, with `superseded_by` pointing at
its replacement, because the rejected path is half the value of the record.

# The log

* [0001. Build profile creation in the dashboard, not the CLI](0001-profile-builder-in-dashboard.md) - Why profile creation belongs to the dashboard and the prompt_toolkit wizard was turned down.
* [0002. Keep Telegram streamed replies going past the first overflow chunk](0002-telegram-overflow-continuations.md) - The fix for streamed Telegram replies dying after the first overflow chunk.
* [0003. Expose an OpenAI-compatible API server](0003-openai-compatible-api-server.md) - Serving the agent behind an OpenAI-compatible HTTP surface, and what that commits us to.
* [0004. Support streaming LLM responses](0004-streaming-response-support.md) - Streaming model output through the loop to each surface, including the cost to the prompt-cache invariant.
