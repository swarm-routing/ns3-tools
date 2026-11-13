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
shell; stdlib only). Malformed lines are rejected by default (#58): the
read fails, naming the file, the line and the field, when a `##RUN##`,
`##BENCH##` or `# stddev` field is present but not a number, when a line
starts `##RUN##` but is not `<run> <arm> <fields>` (a row cut by interleaved
output), or when a required campaign-CSV value is unreadable or missing.
What strict reading does not cover: a seed that is simply absent from a cell
(the paired statistics use the seeds both cells have, and say how many), a
trailing `##RUN##` field an older harness does not print, and
`bench_parse --get`, which prints a field's token as written for the CI
gates and judges nothing. `--lenient` (`bench_parse`, `sweep_summary`) is
for exploring partial logs and reports what it dropped; it can change a
result: `bench_parse` then leaves a malformed field out of that seed (a row
whose PDR is malformed is dropped), and `sweep_summary` keeps a row and
substitutes its caller's default (0 for most metrics) for an unreadable
aggregate value. No verdict path uses it unless asked.

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
repository is in the certificate's workflow-repository extension. Each release from v0.2.0 on also publishes the image
`ghcr.io/swarm-routing/ns3-tools:vX.Y.Z-optimized` and `-debug`, and moves the
rolling `latest-optimized`, `latest-debug` and `latest` to them (v0.1.0:
`ghcr.io/swarm-routing/ns3-tools-ns3`, frozen), the
harness built on a pinned `ns3-dev` digest, signed with cosign (keyless):
see `swarm-routing/.github` `reusable-ns3-image.yml` for its verify commands.
