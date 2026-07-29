# Research

Investigations: a question that needed evidence before anyone could decide, the
evidence gathered, and what it concluded. Benchmarks, spike results, vendor and
protocol comparisons, "is this even possible" spikes.

Research and decisions are different artefacts and are kept apart on purpose. A
research page answers *what is true*; a [decision](../decisions/index.md) records
*what we chose*. Research often feeds a decision, and when it does, the decision links
back to it. Research that concluded nothing still belongs here, because the next
person deserves to know the road was already walked.

Files are named `NNNN-slug.md`. Copy [`_template.md`](_template.md) to start one.

A research page is finished when its `conclusion` frontmatter field is filled in.
Until then it carries `status: draft` and is understood to be in progress.

# Investigations

* [Which VPS should host this fork?](0001-vps-hosting-brazil.md) - Sizing the gateway from the compose limits and the image contents, then pricing the providers that can serve Brazil under a R$ 50/month ceiling.
* [0002. Why did the SSL CA bundle break after `hermes update`?](0002-ssl-cacert-corruption-after-update.md) - Root-cause analysis of the CA bundle corrupted by an update.
* [0003. What does a full security audit find in this fork?](0003-security-audit-2026-07-23.md) - The 2026-07-23 audit: findings, severities and the exposure each implies.
* [0004. Which audit findings were actually fixed?](0004-security-remediation-2026-07-23.md) - Fixes shipped, risks accepted, and the reasoning behind each disposition.
* [0005. Which Semgrep findings are real?](0005-semgrep-triage-2026-07-24.md) - Which rules fire truthfully here, which are noise, and why each suppression is defensible.
* [0006. What happened to the audit's open items?](0006-open-items-resolution-2026-07-24.md) - How the items left open by the audit were closed out.
* [0007. Which Bandit findings are real?](0007-bandit-triage-2026-07-25.md) - Including BND-001, the finding that moved defusedxml into core dependencies.
