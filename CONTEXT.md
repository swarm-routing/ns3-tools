# CONTEXT.md — orientation and history

Background an agent or a new contributor needs that does not belong in
`AGENTS.md`: domain notes, current state, glossary, open questions. Read the
section a task needs, not the whole file.

## Current state

**Being populated (Phase 4); state on 2026-11-13.** On `main`: the
`routing-bench` comparison harness (`ns3/routing-bench/`, moved from AntHocNet)
with the stock AODV, OLSR, DSDV and HWMP arms and the `##PERF##` cost line
(#91, part); the `ns3_tool` readers and statistics (`python/`), strict by
default (#58), behind the `ns3-tool` CLI (#39); the plausibility checker with
anchors from a file (#28); the campaign driver `ns3-campaign` (#66) with its
result cache (#94) and the change-test plans of any protocol repository
(`change_plan.py`, #81, #89, #152). v0.1.0 is released (#26), and AntHocNet
consumes the harness and the readers from here (#29).

Next: what is left of v0.2.0 (#148: `collect` checks each cell's `##PROV##
modules=`), then the ladder in `docs/roadmap.md` (v0.3.0: the marker contract
v2, #79, and the rest of #91; v0.4.0: the family catalogue #55, the probes as
their own module #40, the satellite harnesses #5, the user guide #4). #155
(finishing #94 and #89 across repositories) is open and not on the ladder.
The comparator arms (epic #15) come after 1.0.

## Glossary

## Open questions
