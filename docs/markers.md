# The marker-line contract

`routing-compare` (and its alias `anthocnet-compare`) prints its results as
marker lines on stdout: a line that starts with `##NAME##`, then fields. The
readers in [`python/ns3_tool/`](../python/ns3_tool/) and
[swarm-routing/benchmarks](https://github.com/swarm-routing/benchmarks)'
validator consume them, so their shape is a contract (golden rule 11): fields
are only appended; removing a field or changing its meaning bumps the contract
version and updates the readers and `benchmarks`' parsers in the same change.

The machine-readable contract is `benchmarks`'
[`schemas/markers/v<N>.json`](https://github.com/swarm-routing/benchmarks/tree/main/schemas/markers)
(vendored here under `python/tests/benchmarks/`). This page is the human
description of what the harness prints and which lines are results.

## Version

Today's output is **contract v1**: there is no `##HARNESS## version=<n>` line
yet (#3), and v1 does not require one. Version 2 (#79) adds that line, makes
the harness print `##BENCH##` and `##DIAG##` itself, and records the positional
fields of `##PERF##` below.

## Stability

From ns3-tools **v1.0.0** (maintainer decision 2026-11-13, `docs/roadmap.md`),
contract v1 as this page describes it and the `ns3-tool` CLI (`compare`,
`sweep`, `diagnose`, `scaffold`, and the console scripts kept as aliases) are
**frozen**: marker fields, marker lines, commands and options are only
appended. Removing one, or changing what one means or its units, is a
breaking change: it waits for the next major release (v2.0.0, contract v2,
#79), which says what changed and how to migrate, and moves `benchmarks`'
schema in step. Until then a replacement is added beside the old field or
option, and the old one keeps working.

## Results and cost

Every line below except `##PERF##` is a **result**: for a fixed seed it is
byte-identical run to run (org rule 5), and comparisons pair it by seed (org
rule 6). `##PERF##` is **cost**: CPU and wall time differ between two runs of
the same seed by nature. So:

- the determinism gate (`.github/workflows/ns3.yml`, `determinism:`) runs with
  `--perf=0`, and `tools/checks/check-perf-markers.sh` proves that `--perf`
  adds `##PERF##` lines and changes no other byte (two same-seed runs minus
  `##PERF##` are identical, and equal to the `--perf=0` run);
- no reader compares `##PERF##` between cells: `bench_parse` passes it through
  verbatim under the compare table and `--control` reads `##RUN##` only
  (`python/tests/test_perf.py` checks every fixture cell with `##PERF##` lines
  added);
- the workflow-written lines `##ARGS##` and `##PROV##` are not harness output
  either: they describe how a cell was run, not what it measured.

## The lines, per seed and arm

For each arm (in `--protocols` order) and each seed `firstRun..firstRun+runs-1`
the harness runs one simulation and prints, in this order (some only under a
flag or a condition, absent rather than zero when they do not apply):

| Marker | Fields | When |
|--------|--------|------|
| `##DROPID##` | `<seed> <arm> key=value ...` | UDP runs |
| `##REINJ##` | `<seed> <arm> key=value ...` | arms whose measurement keeps the re-injection books |
| `##RUN##` | `<seed> <arm> pdr delay delay99 thrput nrl jitter off50 off90 nrl_bytes` | always; positional, the row pairing uses |
| `##HOLD##` | `<seed> <arm> (count mean_ms max_ms)...` | arms with pending-queue instrumentation |
| `##AIR##`, `##ENERGY##` | `<seed> <arm> ...` | the energy model on (`--energyJ` > 0) |
| `##GOODPUT##` | `<seed> <arm> goodput_kbps` | always |
| `##ROUTE##` | `<seed> <arm> ...` | UDP runs |
| `##PERF##` | `<scenario> <arm> <seed> <cpu_s> <wall_s> <events> <maxrss_kb>` | `--perf` (default on) |

After every arm and seed: `##MATCH##` and `##COMMON##` (per seed, across arms),
the summary table and `# stddev` lines. Before the runs: `##CONFIG##`
(`key=value`).

## `##PERF##`: what one run cost

```
##PERF## <scenario> <arm> <seed> <cpu_s> <wall_s> <events> <maxrss_kb>
##PERF## paper olsr 3 0.404 0.409 4302 -
```

| Field | Meaning |
|-------|---------|
| `scenario` | `--scenario` (`-` when none was given) |
| `arm`, `seed` | the run, as in its `##RUN##` row |
| `cpu_s` | user + system CPU seconds of the process during this run (`getrusage(RUSAGE_SELF)` delta), 3 decimals |
| `wall_s` | wall seconds of this run (steady clock delta), 3 decimals |
| `events` | events the ns-3 simulator executed in this run (`Simulator::GetEventCount()`, read before `Simulator::Destroy()`); events/s is `events / wall_s` |
| `maxrss_kb` | the process's peak resident set in kB (`ru_maxrss`), **only when the process runs a single arm**; `-` otherwise |

`maxrss` is a process-lifetime peak, so with two arms in one process the second
arm's number would include the first's. It is printed only with one arm in
`--protocols`; with several seeds of that one arm it is the peak up to that
seed. Campaign cells (`campaign-cell.yml`, swarm-routing/.github's
`reusable-benchmark-cell.yml`) already run one arm per cell, so a campaign
records it for every arm.

Read it through the reader, never by column position:

```bash
python3 python/ns3_tool/bench_parse.py --get row --from perf --proto olsr CELL     # scenario seed cpu_s wall_s events maxrss_kb
python3 python/ns3_tool/bench_parse.py --get maxrss_kb --from perf --proto olsr CELL
```

`bench_parse.parse_perf()` returns each line as a dict (plus `events_per_s`).
It also reads AntHocNet's older per-process form,
`##PERF## <scenario> all <wall_s> <maxrss_kb>` (swarm-routing/AntHocNet#131,
written by its `run-scenarios.py` around one process that ran every arm): the
arm is `all` and seed, CPU and events are absent. The two forms differ in field
count (7 against 4), which is how the reader tells them apart.

These numbers are descriptive and noisy: they depend on the runner's CPU and
load. Compare them only between cells run on the same kind of runner, and never
gate on them. To see *where* the time or memory goes, profile one cell
([profiling.md](profiling.md)).

### Why this is not a version bump

`routing-bench` never printed `##PERF##` before, and contract v1 lists `PERF` as
free-form, so the new line is an addition that every v1 reader and `benchmarks`'
v1 validator accept. Its positional fields enter the machine-readable contract
with v2 (#79; the matching `schemas/markers/v2.json` entry is
swarm-routing/benchmarks#33).
