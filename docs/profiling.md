# Profiling one cell

`##PERF##` ([markers.md](markers.md#perf-what-one-run-cost)) says *what* a run
cost: CPU and wall seconds, simulator events, peak RSS. This page is the recipe
for *where* the time and the memory go: `perf` for CPU, heaptrack or valgrind's
massif for the heap, on one cell inside `ghcr.io/swarm-routing/ns3-dev:latest-debug`
(ns3-tools#91).

Profile the `debug` image: it keeps symbols and frame information, so the
stacks name functions. Its asserts and `-O0`-ish code make absolute times
larger than on `optimized`, so read the *shares* (which function, which
allocation site), not the seconds. Compare costs between arms with
`##PERF##` on the `optimized` campaign image, never with a profiler.

## The tools

`ns3-dev:latest-debug` does not ship `perf`, heaptrack or valgrind today
(checked on ns-3.49: `command -v perf heaptrack valgrind` finds none). Adding
them to the debug image only is swarm-routing/ns3-containers#109. Until it
lands, install them in a throwaway container as below (Ubuntu 24.04 packages;
versions seen working: heaptrack 1.5.0, valgrind 3.22.0, `linux-tools-generic`
for `perf`). Nothing here is committed or reaches a cell.

## Build the harness and pick a cell

From a checkout of this repository, with the arm's repository checked out
beside it (`../ARA` here; drop that mount and the `ara=` argument to profile a
stock arm). Add the arm's own dependencies (swarm-core, for one) the same way,
as in its CI:

```bash
docker run --rm -it --cap-add PERFMON -v "$PWD:/src:ro" -v "$PWD/../ARA:/arm:ro" \
    ghcr.io/swarm-routing/ns3-dev:latest-debug bash
# inside the container:
ns3-build-contrib routing-bench=/src/ns3/routing-bench ara=/arm/ns3
apt-get update && apt-get install -y --no-install-recommends linux-tools-generic heaptrack valgrind
cd /opt/ns-3
cell="routing-compare --scenario=paper --nNodes=12 --time=10 --runs=1 --protocols=olsr --perf=0"
./ns3 run --no-build "${cell/--perf=0/}" | grep '^##PERF##'   # the cost the profile explains
```

Replace `olsr` in `--protocols=` with the arm you profile (`ara` above):
the command profiles only the arm it names. Profile **one arm, one seed**: with several arms in one process the profile
mixes them, and the second arm starts on the first one's heap. `--perf=0`
keeps the profiler's own overhead out of the output you compare.

`./ns3 run --command-template="TOOL %s"` puts the profiler in front of the
program itself (`%s` is the built binary with its arguments). Putting the tool
in front of `./ns3` instead profiles the Python launcher.

## CPU: `perf`

```bash
PERF=$(ls /usr/lib/linux-tools/*/perf | head -1)   # the /usr/bin/perf wrapper wants the host kernel's package
./ns3 run --no-build --command-template="$PERF record -F 999 -g -o /tmp/perf.data %s" "$cell"
$PERF report -i /tmp/perf.data --stdio --no-children --sort dso,sym | head -40
```

`--cap-add PERFMON` on `docker run` lets the container open performance
counters; without it `perf record` fails with a permission error. On a short
cell the dynamic loader (`ld-linux`: `do_lookup_x`, `_dl_fixup`) and ns-3's RNG
(`MRG32k3a::MultModM`) lead the profile; give the cell a longer `--time` before
reading anything into the protocol's share.

## Heap: heaptrack

```bash
./ns3 run --no-build --command-template="heaptrack -o /tmp/ht %s" "$cell"
heaptrack_print /tmp/ht.gz | tail -12     # peak heap, allocation calls, leaked
heaptrack_print /tmp/ht.gz | less         # the allocation sites, by cost
```

On the cell above: about 190 000 allocation calls and a 2.2 MB peak heap.
heaptrack is the faster of the two heap tools (the run takes a few times its
normal length); its "leaked" figure includes what ns-3 frees only at exit.

## Heap over time: valgrind massif

```bash
./ns3 run --no-build --command-template="valgrind --tool=massif --massif-out-file=/tmp/massif.out %s" "$cell"
ms_print /tmp/massif.out | less           # the heap graph, then the largest snapshots' trees
```

massif shows the heap over the run, so a table that keeps growing (an
unbounded structure, golden rule 7) shows as a rising graph instead of one peak
number. Its x axis is instructions executed (massif's default
`--time-unit=i`), not simulated time, and a rising graph is a hint, not proof:
run the cell again with a longer `--time` and compare the peaks. A bounded
structure plateaus; an unbounded one keeps growing with simulated time. It is
slow (tens of times the normal run): keep the cell small.

## What to do with a finding

A hot function or a growing table is a finding: open an issue in the
protocol's repository with the cell's command, the `##PERF##` line and the
profile excerpt (AntHocNet ADR-0013). A fix that should make the protocol
cheaper is shown by `##PERF##` lines on the `optimized` image before and after,
not by a profile: CPU and wall times are noisy, so run each side several times
on the same seeds and the same kind of runner, and compare the spread, not one
pair. A deterministic count (`events`) moves only when the simulation itself
does.
