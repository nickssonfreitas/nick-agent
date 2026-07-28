# Surfaces

The five ways a human or a platform talks to the core. They are independent, not
layered: each one drives the same `AIAgent` directly rather than wrapping another
surface.

* [CLI](cli.md) - The interactive REPL, the hermes subcommand tree, the slash-command registry and skins.
* [Gateway](gateway.md) - The messaging runtime and every platform adapter.
* [TUI](tui.md) - The Ink terminal UI and its Python JSON-RPC backend.
* [Desktop](desktop.md) - The Electron chat app and the transport it shares with the other surfaces.
* [Dashboard and web](dashboard-web.md) - The browser dashboard and the PTY bridge that embeds the real TUI inside it.
* [Adding a messaging platform](adding-a-platform.md) - Both routes end to end: the plugin path that touches no core code, and the eleven core files the built-in path still has to change.
* [Billing lifecycle in the client](billing-lifecycle.md) - Every billing state shape mapped to what the TUI renders, plus each typed refusal code and its recovery action.
