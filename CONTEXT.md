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

v0.2.0 and v1.0.0 are released too. The ladder in `docs/roadmap.md`, as
re-scoped by the maintainer on 2026-11-13: v1.0.0 froze today's `ns3-tool` CLI
and marker contract v1 (#137); next, v1.1.0 is additive (the family catalogue #55, the probes as
their own module #40, the satellite harnesses #5, the user guide #4, the rest
of #91); v2.0.0 is the marker contract v2 (#79).
The comparator arms (epic #15) come after 1.0.

## Glossary

## Open questions
