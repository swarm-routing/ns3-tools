# Upstream ns-3 bugs the org found

The index of defects in **upstream ns-3** (the simulator and its stock modules,
not our code) that swarm-routing found while benchmarking, running CI or
testing the `ns3-dev` images. It lives here because every comparison runs
through this repository's harness (`routing-bench`), where the stock arms
(AODV, DSDV, OLSR, HWMP) meet these defects (maintainer decision 2026-11-16,
[#233](https://github.com/swarm-routing/ns3-tools/issues/233)).

This page is the index only. Each bug has an **RCAPA** (root cause analysis
with corrective and preventive actions) in [`ns3-rcapa/`](ns3-rcapa/README.md)
and an **RCAPA ticket**, an issue titled `[rcapa] NS3-NNN …` in this
repository. The detail (reproduction, five whys, diagrams, source excerpts,
actions, the upstream text, the timeline) is in the RCAPA, not here.

Upstream reports are filed on the [nsnam GitLab](https://gitlab.com/nsnam/ns-3-dev/-/issues)
by the maintainer, under their own account: a report speaks for the project.
A session drafts the text and records the link once it is filed; it never
posts upstream.

## Index

Status: **open upstream** (no fix upstream), **draft ready** (a report is
written, not filed), **reported** (an upstream issue exists), **worked around**
(our repositories avoid it), **fixed in ns-3.X** (the first release with the
fix). A bug usually has several.

| ID | Title | ns-3 versions | Modules | Status | Workaround in our repositories | Upstream | Investigation and fixes | RCAPA |
|----|-------|---------------|---------|--------|--------------------------------|----------|-------------------------|-------|
| NS3-001 | AODV livelocks in a zero-time ICMP "net unreachable" storm | 3.49 and ns-3-dev `master` `8ec9b4a1` (run); not 3.48 | internet (`Ipv4L3Protocol::SendIcmpNoRoute`, `Icmpv4L4Protocol`), aodv | open upstream; draft ready; worked around | AODV-family arms on `ns3-dev:3.48` (ns3-tools `ns3.yml`; AntHocNet CI); strict XFAIL `3.49 sparse-aodv` in swarm-routing/ns3-containers `test/known-failures.txt` | not yet reported (draft: [swarm-routing/ns3-containers#55](https://github.com/swarm-routing/ns3-containers/issues/55)) | [#238](https://github.com/swarm-routing/ns3-tools/issues/238), [swarm-routing/ns3-containers#35](https://github.com/swarm-routing/ns3-containers/issues/35), [swarm-routing/ns3-containers#42](https://github.com/swarm-routing/ns3-containers/issues/42), [swarm-routing/ns3-containers#48](https://github.com/swarm-routing/ns3-containers/pull/48), [swarm-routing/AntHocNet#843](https://github.com/swarm-routing/AntHocNet/issues/843), [swarm-routing/AntHocNet#842](https://github.com/swarm-routing/AntHocNet/pull/842), [swarm-routing/AntHocNet#656](https://github.com/swarm-routing/AntHocNet/issues/656) | [aodv-icmp-livelock](ns3-rcapa/ns3-rcapa-aodv-icmp-livelock.md) |
| NS3-002 | AODV and DSDV order per-interface sockets by heap address | 3.42, 3.48, 3.49 and ns-3-dev `master` `8ec9b4a1` (run); 3.42, 3.48, 3.49 (read) | aodv, dsdv (`std::map` keyed by `Ptr<Socket>`) | open upstream; draft ready; worked around | `--isolateArms` (default since v1.1.0); AntHocNet: its own maps ordered, stock `aodv` in its own process, the order gate on `anthocnet` rows only | not yet reported (text: [#206](https://github.com/swarm-routing/ns3-tools/issues/206)) | [#239](https://github.com/swarm-routing/ns3-tools/issues/239), [#198](https://github.com/swarm-routing/ns3-tools/issues/198), [#202](https://github.com/swarm-routing/ns3-tools/pull/202), [#212](https://github.com/swarm-routing/ns3-tools/pull/212), [swarm-routing/AntHocNet#362](https://github.com/swarm-routing/AntHocNet/issues/362), [swarm-routing/AntHocNet#471](https://github.com/swarm-routing/AntHocNet/pull/471), [swarm-routing/AntHocNet#675](https://github.com/swarm-routing/AntHocNet/issues/675), [swarm-routing/AntHocNet#1039](https://github.com/swarm-routing/AntHocNet/issues/1039), [swarm-routing/AntHocNet#1059](https://github.com/swarm-routing/AntHocNet/pull/1059) | [socket-map-heap-order](ns3-rcapa/ns3-rcapa-socket-map-heap-order.md) |
| NS3-003 | DSDV sends on a route with a null output device (assert, or a silent SIGSEGV when optimized) | 3.36–3.48 and ns-3-dev 2026-08 (run); 3.49 (read) | dsdv (fault seen in internet's `Ipv4L3Protocol::SendRealOut`) | open upstream; reported, fix proposed; worked around | AntHocNet's `isl-grid` refuses `dsdv` on multi-interface topologies, checked by its satellite arm smoke | [nsnam/ns-3-dev#1364](https://gitlab.com/nsnam/ns-3-dev/-/issues/1364), MR [!2985](https://gitlab.com/nsnam/ns-3-dev/-/merge_requests/2985) (open; stalled since 2026-09-16: `need_rebase`, no reviewer; the alternative !3011 defers to it; see PROC-001) | [#240](https://github.com/swarm-routing/ns3-tools/issues/240), [swarm-routing/AntHocNet#281](https://github.com/swarm-routing/AntHocNet/issues/281), [swarm-routing/AntHocNet#420](https://github.com/swarm-routing/AntHocNet/issues/420), [swarm-routing/AntHocNet#422](https://github.com/swarm-routing/AntHocNet/pull/422), [swarm-routing/AntHocNet#434](https://github.com/swarm-routing/AntHocNet/issues/434), [swarm-routing/AntHocNet#445](https://github.com/swarm-routing/AntHocNet/pull/445), [#247](https://github.com/swarm-routing/ns3-tools/issues/247) | [dsdv-null-route](ns3-rcapa/ns3-rcapa-dsdv-null-route.md) |

## Candidates: not yet shown to be ns-3's

A finding whose cause is not yet known stays here, in its own issue, until it
is classified. If it turns out to be ns-3's, it gets the next `NS3-NNN`, an
RCAPA ticket and an RCAPA.

| Finding | ns-3 | Where | Open question |
|---------|------|-------|---------------|
| Four wifi unit suites (`wifi-txop`, `wifi-mlo`, `wifi-gcr`, `wifi-emlsr-basic-exchanges`) fail with the images' 16-module set; pass with all modules, and on 3.49 | 3.48 | [swarm-routing/ns3-containers#49](https://github.com/swarm-routing/ns3-containers/issues/49) | an undeclared module dependency (ns-3's) or the image's module list; the test also reads past its frame list (an `EXPECT` where an `ASSERT` is needed) |
| 3.42 → 3.48 moves AntHocNet's delay tail and overhead (same build recipe, different release) | 3.42 → 3.48 | [swarm-routing/AntHocNet#923](https://github.com/swarm-routing/AntHocNet/issues/923) | an intended upstream change or a regression; bisect pending |

## Process RCAPAs (ours)

Defects in how we carry ns-3 reports, not in ns-3. Same format, IDs
`PROC-NNN`, RCAPA ticket titled `[rcapa] PROC-NNN …`.

| ID | Title | Status | RCAPA ticket | RCAPA |
|----|-------|--------|--------------|-------|
| PROC-001 | Upstream reports have no owner after filing: NS3-003's fix (!2985) stalled two months unnoticed | open; corrective action with the maintainer (swarm-routing/.github#519), tracker planned ([#246](https://github.com/swarm-routing/ns3-tools/issues/246)) | [#247](https://github.com/swarm-routing/ns3-tools/issues/247) | [upstream-report-tracking](ns3-rcapa/ns3-rcapa-upstream-report-tracking.md) |

## Not ns-3 bugs (ours)

Listed for traceability, because they were found next to the bugs above:

- [#211](https://github.com/swarm-routing/ns3-tools/issues/211) (fixed by
  [#212](https://github.com/swarm-routing/ns3-tools/pull/212)): our isolation
  test forked its runs from different heaps, so NS3-002 showed through it.
- [#218](https://github.com/swarm-routing/ns3-tools/issues/218) (fixed by
  [#219](https://github.com/swarm-routing/ns3-tools/pull/219)): our arm workers
  shipped `ns3::Address` bytes between processes; ns-3 assigns address type
  numbers per process by design (`Address::Register`,
  `src/network/model/address.cc:130–139`, ns-3.49), so the keys did not match.

## Important links

- RCAPA guide and template: [`ns3-rcapa/README.md`](ns3-rcapa/README.md),
  [`ns3-rcapa/_template.md`](ns3-rcapa/_template.md)
- RCAPA tickets: [issues titled `[rcapa]`](https://github.com/swarm-routing/ns3-tools/issues?q=is%3Aissue+%5Brcapa%5D+in%3Atitle)
- The images and their expected failures: swarm-routing/ns3-containers
  `test/known-failures.txt` and README "Known issues and findings"
- The nightly build against ns-3-dev that catches upstream changes early:
  swarm-routing/AntHocNet `.github/workflows/ns3-dev.yml`
- ns-3's issue tracker: https://gitlab.com/nsnam/ns-3-dev/-/issues

## Adding a bug

Anyone who finds or suspects a defect in upstream ns-3 (a session or a
contributor):

1. **Open the RCAPA ticket first**: an issue in this repository titled
   `[rcapa] NS3-NNN <short description>`, with the next free number (numbers are
   never reused), labels `verification`, `ns3`, `benchmark`, a priority and a
   model suggestion (and `rcapa` once that label reaches this repository).
   Body: Context, Scope, Acceptance criteria (the RCAPA document written, the
   five whys cited, the reproducer verified on the latest image and on
   ns-3-dev `master`, corrective and preventive actions linked, the upstream
   report filed by the maintainer or the decision recorded), links to the
   investigation issues. Every investigation of an ns-3 bug runs under its
   ticket (maintainer rule 2026-11-16).
2. Write the RCAPA from [`ns3-rcapa/_template.md`](ns3-rcapa/_template.md) as
   `ns3-rcapa/ns3-rcapa-<short-description>.md`; its header links the ticket.
3. Add its row to the index above (and to the table in
   [`ns3-rcapa/README.md`](ns3-rcapa/README.md)); the row links the ticket and
   the RCAPA.
4. Every workaround points back at its row: a `test/known-failures.txt` entry in
   swarm-routing/ns3-containers, a pinned image in a workflow, a refused arm, a
   gate that skips an arm, a harness option that exists because of it. Write
   the ID next to the existing issue reference (a comment line such as
   `# NS3-001: …` above a `known-failures.txt` entry, a workflow comment, a
   README sentence).
5. Upstream: draft the report in the ticket (or link the issue that holds it);
   the maintainer files it under their own account, then the link goes into the
   ticket, the RCAPA and this index. Filing is a step, not the end: the RCAPA
   ticket stays open until the fix is in an ns-3 release we run, and the
   upstream thread (new notes, rebase requests, review, competing fixes) is
   followed on it (PROC-001; the tracker, #246, will watch every link here).
6. When a fix reaches a release: set the status to **fixed in ns-3.X**, re-run
   the reproducer on that release, and remove the workarounds in the same
   round. A row and its RCAPA are never deleted.

Only what a source supports goes in: a claim without a link to its evidence
stays in its issue until it has one.
