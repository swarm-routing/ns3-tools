# RCAPAs: one root-cause record per upstream ns-3 bug

An **RCAPA** (root cause analysis with corrective and preventive actions) is
the full record of one defect in upstream ns-3 that the org found. The
register, [`../ns3-bugs.md`](../ns3-bugs.md), is the index: one row per bug,
with its status and a link to its RCAPA here. This directory holds the detail
(maintainer decision 2026-11-16, [#233](https://github.com/swarm-routing/ns3-tools/issues/233)).

| RCAPA | Register row | RCAPA ticket |
|-------|--------------|--------------|
| [`ns3-rcapa-aodv-icmp-livelock.md`](ns3-rcapa-aodv-icmp-livelock.md) | NS3-001: ns-3.49 AODV livelocks in a zero-time ICMP "net unreachable" storm | [#238](https://github.com/swarm-routing/ns3-tools/issues/238) |
| [`ns3-rcapa-socket-map-heap-order.md`](ns3-rcapa-socket-map-heap-order.md) | NS3-002: AODV and DSDV order per-interface sockets by heap address | [#239](https://github.com/swarm-routing/ns3-tools/issues/239) |
| [`ns3-rcapa-dsdv-null-route.md`](ns3-rcapa-dsdv-null-route.md) | NS3-003: DSDV sends on a route with a null output device | [#240](https://github.com/swarm-routing/ns3-tools/issues/240) |

## What an RCAPA answers

1. **What happened, and where did we see it?** The symptom, the run or job
   that showed it, the issue that recorded it.
2. **How do I see it myself?** Exact commands, the image by tag and digest,
   seeds, expected against actual, and the minimal program linked at a commit.
3. **Why did it happen?** Five whys. Each answer cites its evidence: an ns-3
   source line at a named release (`src/…/file.cc:NNN`, ns-3.49), or our own
   evidence (a run, a comment, a measured count). An answer without a source
   is a hypothesis and says so.
4. **What does it look like?** Mermaid diagrams: the failing call chain as a
   sequence diagram, the ns-3 types involved as a class diagram, and, where it
   helps, the infrastructure (images, CI legs, harness processes).
5. **What did we do about it?** Corrective actions (the workarounds in our
   repositories, each with its PR) and preventive actions (tests, expected
   failures, canaries, checks that catch a recurrence). An action not done yet
   is an open issue, linked.
6. **What goes upstream?** The ready-to-file report and its status. The
   maintainer files upstream reports on the [nsnam GitLab](https://gitlab.com/nsnam/ns-3-dev/-/issues)
   under their own account; a session drafts and records, never posts.
7. **When?** A dated timeline and every link.

## Adding a bug: the ticket first

Every ns-3 bug, and every investigation of one, has its own **RCAPA ticket**
(maintainer rule 2026-11-16). The full procedure is in the register's
["Adding a bug"](../ns3-bugs.md#adding-a-bug); in short:

1. **Open the RCAPA ticket first**: an issue in swarm-routing/ns3-tools titled
   `[rcapa] NS3-NNN <short description>` (labels `verification`, `ns3`,
   `benchmark`, a priority, a model suggestion, and `rcapa` once it reaches
   this repository). Its acceptance criteria: this document written, the five
   whys cited, the reproducer verified on the latest image and on ns-3-dev
   `master`, corrective and preventive actions linked, the upstream report
   filed by the maintainer or the decision recorded.
2. Copy [`_template.md`](_template.md) to `ns3-rcapa-<short-description>.md`
   (lowercase, hyphens: what breaks, not where it was found). Its summary links
   the ticket.
3. Add its row to the register and to the table above, in the same pull
   request.

## Writing one

- Quote ns-3 source briefly, with file, line and release, from the release's
  tag (for example `https://gitlab.com/nsnam/ns-3-dev/-/raw/ns-3.49/src/aodv/model/aodv-rqueue.cc`).
  ns-3 is GPL-2.0, these docs are GPL-2.0-only.
- Say which versions were **run** and which were only **read**.
- Mermaid: no HTML entities (`&lt;`, `&gt;`) inside a block, since GitHub then
  renders nothing; write template arguments as words ("map of Ptr Socket").
- An RCAPA is never deleted. When ns-3 ships the fix, update its status, the
  re-run on the fixed release, and the removal of each workaround.
