---
type: Decision
title: Build the release image for arm64 as well as amd64
description: "Restoring the arm64 matrix leg in publish-image.yml, because the amd64-only choice was derived from a provider that had never been compared."
resource: .github/workflows/publish-image.yml
tags: [decisions, ci, docker, deployment, arm64]
status: stable
decision_status: proposed
deciders: [human:nickssonfreitas]
date: 2026-07-28
sources:
  - id: repo
    resource: git:8cc913c4e
    title: hermes-agent @ 8cc913c4e (branch dev)
    last_modified: 2026-07-28
verified:
  - { by: agent:claude-opus-5, at: 2026-07-28 }
stale_after: 2026-10-28
---
# 0005. Build the release image for arm64 as well as amd64

## Context

`publish-image.yml` built one architecture, and said why:

> Single architecture (amd64). Hostinger's KVM VPS is x86_64, so the arm64 half
> of upstream's matrix would double the build time to produce an image nothing
> here runs.

That reasoning is sound *given* Hostinger. What it hides is that Hostinger was
never compared against anything — the deploy bundle simply shipped with
`hostinger-api.sh` in it, and the architecture followed the provider.

[Which VPS should host this fork?](../research/0001-vps-hosting-brazil.md) ran the
comparison the rationale assumed. Two findings bear on this decision, and they
point in opposite directions:

- The cheapest hardware that fits this deploy at all, and the only free option
  physically in Brazil, is **Ampere**: Oracle's `br-saopaulo-1` free tier. An
  amd64-only release pipeline makes that provider unreachable, so the CI
  constraint was silently deciding the hosting question.
- **Paid ARM is no longer the cheap option.** After Hetzner's April 2026 price
  increase, CAX21 (ARM) costs €7.99 against CX33 (x86) at €6.49 for identical
  vCPU, RAM and disk. On Hetzner, x86 is cheaper *and* keeps AVX2 for local
  Whisper transcription.

So arm64 is not a general improvement. It buys exactly one thing: the option of
deploying to an Ampere box.

## Options considered

**Stay amd64-only.** Zero CI cost, and correct for every provider actually on
the table except Oracle. Rejected because it leaves an infrastructure decision
encoded in a build file, where nobody looking for it would find it.

**Build arm64 under QEMU on the amd64 runner.** One runner, no matrix. Rejected:
the docker integration suite runs against the image *before* it is pushed, and
emulated execution is both slow enough to blow the timeout and a different thing
from what would ship.

**Native matrix leg, push by digest, merge manifests.** Chosen. The shape already
exists and already works in `docker.yml`, so this is restoration rather than new
engineering.

## Decision

Restore the two-architecture matrix in `publish-image.yml`, copying `docker.yml`'s
push-by-digest and manifest-merge shape. Each leg builds on a native runner
(`ubuntu-latest`, `ubuntu-24.04-arm`), runs the docker suite against its own
image, and pushes by digest with no tag. A `publish` job stitches the digests
into one tagged manifest list.

The workflow's `digest` output becomes the **manifest list** digest rather than a
per-architecture one. This matters downstream: `deploy/vps-hardened` pins a
digest, and pinning a per-arch digest would defeat the point of a manifest list.

## Consequences

Release builds now cost two runners instead of one. That is the price of the
Oracle option, and it is only worth paying if that option gets taken — see
**Open** below.

`ubuntu-24.04-arm` is a GitHub-hosted arm64 runner. It is free for public
repositories; on a private repo it bills at the standard rate.

## Open

**This has not run yet.** The arm64 leg is unproven in two specific places, both
inside the image build rather than the workflow:

- `uv sync --locked` must resolve the lockfile on `linux/aarch64`. Any transitive
  with no aarch64 wheel falls back to a source build.
- The Dockerfile runs `npx playwright install --with-deps chromium --only-shell`.
  Playwright ships arm64 Linux builds, but this combination has never been
  exercised here.

Neither can be settled by reading. The way to settle them is
`gh workflow run publish-image.yml --ref <branch>` and reading the arm64 leg.

Kept `proposed` rather than `accepted` for that reason, and because the decision
it serves — which VPS this actually deploys to — is still open.
