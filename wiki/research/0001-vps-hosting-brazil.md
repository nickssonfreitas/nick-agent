---
type: Research
title: Which VPS should host this fork?
description: Sizing the gateway from the compose limits and the image contents, then pricing the providers that can serve Brazil under a R$ 50/month ceiling.
resource: deploy/vps-hardened/docker-compose.yml
tags: [operations, deployment, vps, hosting, costs]
status: stable
question: What is the cheapest VPS that can run this fork's hardened deploy bundle for a single user, with a datacenter in or near Brazil?
conclusion: Hetzner CX33 (4 vCPU x86, 8 GB, 80 GB NVMe, ~R$ 41/month, Germany or Finland) is the best value; the Brazilian options that beat it on latency either cost more for less hardware (Hostinger) or trade capacity for a silent free tier (Oracle São Paulo).
date: 2026-07-28
sources:
  - id: repo
    resource: git:44d22ee39
    title: hermes-agent @ 44d22ee39 (branch dev)
    last_modified: 2026-07-28
  - id: hetzner-locations
    resource: https://docs.hetzner.com/cloud/general/locations/
    title: Hetzner Docs — Cloud locations and which server types each one offers
    last_modified: 2026-07-28
  - id: hetzner-pricing
    resource: https://www.bitdoze.com/hetzner-cloud-cost-optimized-plans/
    title: Hetzner Cloud pricing after the April 2026 increase
    last_modified: 2026-07-28
  - id: hetzner-plans
    resource: https://www.hetzner.com/cloud/cost-optimized/
    title: Hetzner Cloud cost-optimized plan specifications
    last_modified: 2026-07-28
  - id: hostinger-br
    resource: https://www.hostinger.com/br/servidor-vps
    title: Hostinger Brazil KVM VPS plans and pricing
    last_modified: 2026-07-28
  - id: oracle-cut
    resource: https://terminalbytes.com/oracle-cloud-free-tier-changes-2026/
    title: Oracle Cloud free tier halved from 4 OCPU/24 GB to 2 OCPU/12 GB
    last_modified: 2026-07-28
  - id: oracle-infoq
    resource: https://www.infoq.com/news/2026/07/oracle-cloud-free-tier-limits/
    title: InfoQ — Oracle quietly halves free tier Ampere A1 limits with no public announcement
    last_modified: 2026-07-28
  - id: magalu
    resource: https://magalu.cloud/precos/virtual-machines/
    title: Magalu Cloud virtual machine pricing
    last_modified: 2026-07-28
  - id: contabo
    resource: https://cybernews.com/best-web-hosting/contabo-review/pricing/
    title: Contabo VPS pricing 2026
    last_modified: 2026-07-28
verified:
  - { by: agent:claude-opus-5, at: 2026-07-28 }
stale_after: 2026-10-28
---
# 0001. Which VPS should host this fork?

## Why this was asked

[Hardened VPS deployment](../operations/vps-deployment.md) describes how to run this
fork on an internet-facing VPS, but never says what to buy. Its tooling assumes
Hostinger — `hostinger-api.sh` ships in the bundle and `BOOTSTRAP.md` documents the
Hostinger VPS MCP server — and `publish-image.yml` narrowed the release build to a
single architecture because of it:

> Single architecture (amd64). Hostinger's KVM VPS is x86_64, so the arm64 half of
> upstream's matrix would double the build time to produce an image nothing here
> runs.[^repo]

That is an architecture decision derived from a provider that was never itself
compared against alternatives. This page supplies the missing comparison, for a
single-user deployment with a R$ 50/month ceiling.

## Method

Sizing came from the repo, not from guesswork. Pricing came from vendor pages and
vendor docs, fetched on 2026-07-28, converted at the rates quoted that day
(EUR = R$ 5,83; USD = R$ 5,16).

Where two sources disagreed, the vendor's own documentation won. That mattered twice:
third-party articles claimed Hetzner's ARM instances were available in Ashburn, and
Hetzner's location matrix says they are not.[^hetzner-locations]

## Findings

### What the workload actually needs

Three facts in the repo fix the sizing, and none of them require a benchmark.

The hardened compose file declares its own ceiling. The gateway is capped at
`cpus: "2.0", memory: 2g` and the dashboard at `cpus: "1.0", memory: 1g`, plus a Caddy
container.[^repo] Declared worst case is therefore ~3 vCPU and ~3 GB before the host OS
takes its share.

The image is not slim. It builds on `debian:13.4`, copies in Node 22, installs the
composite `[all]` extra, and runs `npx playwright install --with-deps chromium
--only-shell`.[^repo] A Chromium tab is 300–500 MB resident under load, and it is
already there whether or not it gets used.

Disk grows through the venv and cache trees, not the database. A working `~/.hermes` on
the author's machine is 3.5 GB, of which `state.db` is 3.5 MB; the bulk is 1.9 GB of
`hermes-agent` and 1.5 GB of `venvs`.[^repo] A 40 GB disk holding this image plus that
tree is tight; 80 GB is not.

Two optional workloads dominate the peak. Playwright is one. The other is the `[voice]`
extra, which pulls `faster-whisper` and transcribes on the CPU — a `small` model wants
roughly 2 GB and saturates a core while it runs.[^repo] With both enabled the realistic
peak is 4–5 GB, and **CPU stops being incidental**: transcription is compute-bound and
CTranslate2 leans on AVX2 on x86, which ARM cores do not have.

Everything else in the agent is I/O-bound, waiting on a model API. Inference is remote
in every provider path (`openai`, `anthropic`, `mistralai`, `bedrock`, `vertex`), so no
GPU enters the picture.[^repo]

**Target: 4 vCPU, 8 GB RAM, ≥ 80 GB NVMe.** 4 GB only works if local Whisper is dropped
for an STT API.

### The market at that size

Prices are monthly, converted on 2026-07-28. Hetzner figures include the €0.50 IPv4
surcharge, which is not optional for a box that must terminate TLS on a public
name.[^hetzner-pricing]

| Provider / plan | vCPU | Arch | RAM | Disk | Traffic | Price | ≈ BRL | Location | RTT from BR |
|---|---|---|---|---|---|---|---|---|---|
| **Hetzner CX33** | 4 shared | x86 | 8 GB | 80 GB | 20 TB | €6,99 | **R$ 41** | DE / FI | ~200 ms |
| Hetzner CAX21 | 4 shared | ARM | 8 GB | 80 GB | 20 TB | €8,49 | R$ 49 | DE / FI | ~200 ms |
| Hetzner CX23 | 2 shared | x86 | 4 GB | 40 GB | 20 TB | €4,49 | R$ 26 | DE / FI | ~200 ms |
| Hetzner CAX11 | 2 shared | ARM | 4 GB | 40 GB | 20 TB | €4,99 | R$ 29 | DE / FI | ~200 ms |
| Hetzner CPX32 | 4 shared | x86 | 8 GB | 160 GB | 20 TB | €13,99 | R$ 82 | US-East | ~120 ms |
| **Hostinger KVM 2** | 2 | x86 | 8 GB | 100 GB | 8 TB | R$ 42,99 promo | **R$ 43 → 78** | BR-SP † | ~10 ms |
| Hostinger KVM 4 | 4 | x86 | 16 GB | 200 GB | 16 TB | R$ 59,99 promo | R$ 60 → 150 | BR-SP † | ~10 ms |
| **Oracle Always Free** | 2 OCPU | ARM | 12 GB | 200 GB | 10 TB | — | **R$ 0** | BR-SP | ~10 ms |
| Magalu Cloud BV4-8-40 | 4 | x86 | 8 GB | 40 GB | — | R$ 149,99 | R$ 150 | BR | ~10 ms |
| Contabo Cloud VPS 10 | 4 | x86 | 8 GB | — | 32 TB | €4,50–6,99 | R$ 26–41 | DE / US | ~200 ms |

† Sources disagree on whether São Paulo is currently a VPS-eligible region at
Hostinger; one 2026 review states it was removed and is now cloud/shared-hosting
only.[^hostinger-br] Confirm in the order flow before paying.

Four findings in that table are worth stating outright, because three of them invert
the assumption they replace.

**ARM is no longer the cheap option at Hetzner.** After the April 2026 price increase,
CAX21 costs €7.99 against CX33's €6.49 for identical vCPU, RAM and disk.[^hetzner-pricing]
The x86 box is cheaper *and* keeps AVX2 for Whisper *and* needs no CI change. The case
for restoring the arm64 leg in `publish-image.yml` therefore rests entirely on Oracle's
free tier, not on paid ARM being better value.

**Hetzner's cheap plans are Europe-only.** Ashburn, Hillsboro and Singapore carry only
`CPX` and `CCX`; both `CAX` (ARM) and `CX` (Intel/AMD shared) exist solely in
Falkenstein, Nuremberg and Helsinki.[^hetzner-locations] Buying Hetzner closer to Brazil
means CPX32 at €13.49, which breaks the budget. Cheap Hetzner means ~200 ms.

**Oracle halved the free tier on 15 June 2026**, from 4 OCPU / 24 GB to 2 OCPU /
12 GB, with no blog post, no customer notification and no grandfathering — users found
out when instances stopped.[^oracle-cut] InfoQ confirmed the change went out
undocumented.[^oracle-infoq] Block storage stayed at 200 GB. 12 GB free in São Paulo
still beats every paid option on hardware per real, but the governance record is the
point: the terms moved silently once and can move again.

**The Brazilian premium is real and large.** Magalu Cloud charges R$ 149,99 for the same
4 vCPU / 8 GB that Hetzner sells for R$ 41 — 3.6× — and gives 40 GB of disk instead of
80.[^magalu] Hostinger's R$ 42,99 is a promotional rate requiring a 24–48 month prepay
and renewing at R$ 77,99,[^hostinger-br] and it buys 2 vCPU where Hetzner gives 4 for
less money. Against a CPU-bound Whisper workload, that halved core count is the
binding constraint, not the RAM.

### What the deploy bundle demands beyond CPU and RAM

`DEPLOY.md` step 1 requires an encrypted volume, because conversations are stored as
plaintext SQLite and a provider snapshot captures them in the clear.[^repo] This is a
genuine differentiator and not a checkbox:

- **Oracle** encrypts block and boot volumes at rest by default — the requirement is met
  with no work.
- **Hetzner** offers no encrypted block storage; LUKS has to be set up by hand on the
  volume backing Docker.
- **Hostinger** — unverified; assume manual LUKS until confirmed in hPanel.

The bundle also needs ports 80/443 reachable and a domain with an A/AAAA record for
Caddy to issue TLS.[^repo] Every provider above satisfies that; Oracle needs its
security list opened explicitly, which is a common first-deploy trap.

## Conclusion

**Hetzner CX33 is the best value at this sizing**: R$ 41/month for 4 shared x86 vCPU,
8 GB, 80 GB NVMe and 20 TB of traffic, cancellable monthly, with AVX2 intact for
Whisper and no change to `publish-image.yml`. What it costs is Brazilian residency and
~200 ms of SSH and dashboard latency — which does not touch agent response time, since
the dominant wait is the model API call to the United States either way.

**If Brazilian residency is required**, the honest ranking is Oracle Always Free in
`br-saopaulo-1` (R$ 0, 12 GB, encrypted volumes by default, but ARM — so it depends on
restoring the arm64 matrix leg — plus capacity scarcity and a demonstrated willingness
to change the terms silently), then Hostinger KVM 2 (R$ 42,99 locked behind a 24–48
month prepay, renewing at R$ 77,99, and only 2 vCPU against a CPU-bound transcription
load).

**The arm64 question resolves to: only for Oracle.** Paid ARM at Hetzner now costs more
than the equivalent x86, so the CI work is worth doing only if the free tier is the
target. The shape to copy already exists and works — `docker.yml` carries the full
amd64 + arm64 matrix with push-by-digest and manifest merge; `publish-image.yml` is the
only place it was dropped.[^repo]

## What this does not cover

No benchmarks were run. Every CPU claim here is architectural (AVX2 versus NEON, shared
versus dedicated vCPU) rather than measured, and a Whisper transcription benchmark on
CAX21 against CX33 would sharpen the ARM conclusion considerably.

Backup and disaster recovery were scoped out. `hostinger-api.sh` is provider-specific,
and what replaces it on Hetzner or Oracle is an open question — as is where an off-box
backup of `~/.hermes` should live, which matters most precisely in the Oracle case
where reclamation risk is highest.

Pricing is a snapshot. Hetzner raised prices in April 2026 and Oracle cut the free tier
in June 2026; both moved within the four months before this page was written. Treat
every figure as stale after `stale_after`.

[^repo]: `deploy/vps-hardened/docker-compose.yml` lines 45–48 and 86–88 (resource
    limits); `Dockerfile` lines 102–149 (Node 22 and the Playwright Chromium install)
    and 163–186 (the `[all]` extra); `.github/workflows/publish-image.yml` lines 18–21
    (the amd64-only rationale) and `.github/workflows/docker.yml` lines 37–113 (the
    two-architecture matrix that already exists); `pyproject.toml` `[voice]` extra
    (`faster-whisper`); [Hardened VPS deployment](../operations/vps-deployment.md) steps 1 and 7. Local
    `~/.hermes` measured with `du -sh` at 3.5 GB on 2026-07-28.

[^hetzner-locations]: Hetzner's location matrix marks Cloud Shared AMPERE (CAX) and
    Cloud Shared Intel/AMD (CX) as available in Falkenstein, Nuremberg and Helsinki
    only, stating that "Ashburn, VA, Hillsboro, OR, and Singapore are currently only
    available for cloud products (Cloud Shared AMD, Cloud Dedicated AMD, and Cloud
    features)."

[^hetzner-pricing]: Post-April-2026 rates: CX23 €3.99, CX33 €6.49, CX43 €11.99;
    CAX11 €4.49, CAX21 €7.99, CAX31 €15.99; CPX32 €13.49. All shared plans carry a
    €0.50/month IPv4 surcharge and 20 TB of included traffic.

[^hostinger-br]: Hostinger Brazil lists KVM 1 (1 vCPU / 4 GB / 50 GB) at R$ 29,99,
    KVM 2 (2 vCPU / 8 GB / 100 GB) at R$ 42,99 renewing at R$ 77,99, KVM 4 (4 vCPU /
    16 GB / 200 GB) at R$ 59,99 renewing at R$ 149,99, and KVM 8 at R$ 119,99. Headline
    rates require a 24-month term. A separate 2026 review reports São Paulo was removed
    from VPS-eligible regions, which the vendor page neither confirms nor denies.

[^oracle-cut]: Always Free Ampere A1 went from 3,000 OCPU-hours + 18,000 GB-hours
    (4 OCPU / 24 GB) to 1,500 + 9,000 (2 OCPU / 12 GB), effective 15 June 2026.
    Documentation was updated without notification; no grandfathering, and instances
    over the new limit are stopped until resized. Block and boot storage stayed at
    200 GB.

[^oracle-infoq]: InfoQ reports Oracle published no blog post, sent no customer
    notification and made no announcement; users discovered the limits when instances
    were shut down.

[^magalu]: Magalu Cloud BV4-8-40 (4 vCPU / 8 GB / 40 GB NVMe) at R$ 149,99/month,
    BV4-8-100 at R$ 219,99. The Dedicated Performance line starts at R$ 563,00 for the
    same core and memory count.

[^contabo]: Contabo Cloud VPS 10 (4 vCPU / 8 GB) is quoted between €3.60 and €4.50 on
    12-month terms and around €6.99 monthly, depending on source; the spread was not
    resolved. No Brazilian location.
