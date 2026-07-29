# Working on the repo

How the suite runs, where a test belongs, and how Hermes gets packaged and shipped.

* [Testing and CI](testing-and-ci.md) - How the suite runs, where a test belongs, and what the change classifier does.
* [Packaging and release](packaging-and-release.md) - How Hermes is packaged and shipped: wheels, Docker, Nix, the install script.
* [Hardened VPS deployment](vps-deployment.md) - The linear procedure for putting this fork on an internet-facing VPS: disk encryption, digest pinning, dashboard auth, reverse proxy and verification.
* [VPS bootstrap and what stays manual](vps-bootstrap.md) - How the deploy procedure splits once it is automated: secrets stay manual, digest moves go to CI.
* [Network egress isolation](network-egress-isolation.md) - Constraining what the agent container can reach, and why the default bridge is not enough.
* [Running more than one gateway](multi-gateway-deployment.md) - What has to be true to run several gateway instances against one Hermes home.
