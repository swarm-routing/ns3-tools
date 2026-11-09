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
dataset): they reject malformed lines and never execute what they read (no
`eval`, no pickle, no shell), stdlib only.

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

Nothing is released yet, so there is nothing to verify.
