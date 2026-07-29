# Core

The agent loop and the tool machinery every surface drives. Nothing here may import
anything from `surfaces/`; the dependency arrow points one way.

* [Agent core](agent-core.md) - The AIAgent class, the synchronous tool-calling loop, prompt assembly and context compression.
* [Tools](tools.md) - Tool registration, discovery, exposure through toolsets, and dispatch.
* [Terminal backends](terminal-backends.md) - The execution environments a shell tool can run in: local, Docker, SSH, Modal, Daytona, Singularity.
