# State and configuration

Where durable state lives, and the three loaders that decide what a running agent
believes about its configuration.

* [State and sessions](sessions.md) - The SQLite session store, FTS5 search and retention.
* [Memory and context](memory-and-context.md) - Memory providers, context files and what happens at the token limit.
* [Config and profiles](config-and-profiles.md) - The three config loaders and the profile isolation that makes paths profile-aware.
* [Session lifecycle](session-lifecycle.md) - How the gateway derives a session key, isolates users, resets and recovers. The routing half of sessions.
* [Profile-based routing](profile-routing.md) - How an inbound message picks the profile it runs under.
