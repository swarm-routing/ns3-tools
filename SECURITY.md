# Security Policy

`swarm-routing/ns3-tools` is research code for network simulation. This document records its
security posture honestly; it promises nothing the code does not do. The
org-wide policy is [swarm-routing/.github SECURITY.md](https://github.com/swarm-routing/.github/blob/main/SECURITY.md).

## Trust boundary

<!-- Which input is untrusted, the only code that parses it, and what that
code guarantees. A protocol: its wire-format decoder (org invariant 3). A
tool: the parsers of files it reads. If nothing parses untrusted input, say
so in one line. -->
The `ns3-tool` readers of `##CONFIG##`/`##RUN##`/`##BENCH##`/`##DIAG##`
lines and campaign CSVs (`python/ns3_tool/`). Their input is log output that
may come from someone else's run (a pull request's artifact, a downloaded
dataset). They never execute what they read (no `eval`, no pickle, no
shell; stdlib only). Malformed lines are rejected: a `##RUN##`, `##BENCH##`
or `# stddev` field, or a required campaign-CSV value, that is present but
not a number fails the read, naming the file, the line and the field (#58),
so malformed input cannot change a verdict by quietly dropping a seed.
`--lenient` (`bench_parse`, `sweep_summary`) skips such fields instead, for
exploring partial logs, and reports how many it dropped; no verdict path
uses it by default.

## Known, by-design limitations (out of scope)

- The probes, arenas and the `routing-bench` harness are simulation code: a
  probe trusts the simulator it is attached to.
- A comparator arm here is a model of a published protocol for ns-3, not an
  implementation for real networks.

<!-- org-sync:begin reporting -->
## Reporting a vulnerability

Report privately through GitHub's private vulnerability reporting: the
repository's **Security** tab, then **Report a vulnerability**. If that is
not available, open an issue that only asks for a private contact, and put
no vulnerability details in it. This is a research project maintained on a
best-effort basis: expect an acknowledgement within a few days, not an SLA.
<!-- org-sync:end reporting -->

## Verifying what it ships

Releases from v0.1.0 on (#26) are built, signed and published by the org's
release workflow (`swarm-routing/.github` `reusable-release.yml`, called by
`.github/workflows/release.yml`). Each carries `routing-bench-X.Y.Z.tar.gz`
(the harness module, for ns-3's `contrib/`), the `ns3-tool` wheel and sdist,
`SHA256SUMS`, a CycloneDX SBOM (`ns3-tools-X.Y.Z.cdx.json`) and signed SLSA
build provenance for every asset (`ns3-tools-X.Y.Z.intoto.jsonl`, and the
Sigstore bundle `ns3-tools-X.Y.Z.sigstore.json` for offline checks). The
workflow runs these checks itself before it publishes, and builds the
tarball in a clean `ns3-dev` tree and runs `test.py -s routing-bench` on it
first, so a release that fails either never goes out. With the
[GitHub CLI](https://cli.github.com/):

```bash
sha256sum -c SHA256SUMS --ignore-missing
gh attestation verify routing-bench-X.Y.Z.tar.gz --repo swarm-routing/ns3-tools \
  --signer-workflow swarm-routing/.github/.github/workflows/reusable-release.yml
gh attestation verify routing-bench-X.Y.Z.tar.gz --repo swarm-routing/ns3-tools \
  --predicate-type https://cyclonedx.org/bom \
  --signer-workflow swarm-routing/.github/.github/workflows/reusable-release.yml
# offline: fetch the trusted root while online (gh attestation trusted-root > trusted_root.jsonl)
gh attestation verify routing-bench-X.Y.Z.tar.gz --repo swarm-routing/ns3-tools \
  --bundle ns3-tools-X.Y.Z.sigstore.json --custom-trusted-root trusted_root.jsonl \
  --signer-workflow swarm-routing/.github/.github/workflows/reusable-release.yml
```

The same commands verify the wheel and the sdist. The signer is the org's
reusable workflow, not this repository, so every command names it with
`--signer-workflow` (without it `gh` expects a workflow of `--repo`); this
repository is in the certificate's workflow-repository extension. Each release also publishes the image
`ghcr.io/swarm-routing/ns3-tools-ns3:vX.Y.Z-optimized` and `-debug`, the
harness built on a pinned `ns3-dev` digest, signed with cosign (keyless):
see `swarm-routing/.github` `reusable-ns3-image.yml` for its verify commands.
