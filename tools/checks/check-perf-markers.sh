#!/usr/bin/env bash
# Copyright (c) 2026 Daniel Joppi (https://github.com/swarm-routing)
# SPDX-License-Identifier: MIT OR GPL-2.0-only
#
# check-perf-markers.sh NS3DIR "ARM ARM"
#
# routing-compare's ##PERF## line (ns3-tools#91), on a built ns-3 tree:
#   - one ##PERF## line per arm and seed, read through the reader
#     (bench_parse --from perf), with maxrss '-' when two arms share the
#     process and a number when one arm runs alone;
#   - ##PERF## changes no other byte: two same-seed runs minus their ##PERF##
#     lines are byte-identical, and equal to the same run with --perf=0
#     (org rule 5: wall-clock and CPU time never reach ##RUN##/##BENCH##/##DIAG##).
set -euo pipefail
ns3=$1
read -r -a arms <<<"$2"
[ "${#arms[@]}" -ge 2 ] || { echo "FAIL: name at least two arms" >&2; exit 1; }
bp="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../python/ns3_tool" && pwd)/bench_parse.py"
tmp=$(mktemp -d)
cd "$ns3"

list=$(IFS=,; echo "${arms[*]}")
args="--scenario=paper --nNodes=12 --time=10 --runs=2 --firstRun=3 --protocols=$list"
run() { ./ns3 run --no-build "routing-compare $args $1" > "$2" 2>> "$tmp/stderr.log"; }
run "" "$tmp/a.txt"
run "" "$tmp/b.txt"
run "--perf=0" "$tmp/off.txt"
fails=0
fail() { echo "FAIL: $*" >&2; fails=$((fails + 1)); }

grep -v '^##PERF##' "$tmp/a.txt" > "$tmp/a.strip" || true
grep -v '^##PERF##' "$tmp/b.txt" > "$tmp/b.strip" || true
grep -q '^##RUN##' "$tmp/a.strip" || fail "no ##RUN## rows"
cmp -s "$tmp/a.strip" "$tmp/b.strip" \
    || { fail "two same-seed runs differ outside ##PERF##"; diff "$tmp/a.strip" "$tmp/b.strip" | head -20 >&2 || true; }
cmp -s "$tmp/a.strip" "$tmp/off.txt" \
    || { fail "--perf=0 output is not the --perf output minus its ##PERF## lines"; diff "$tmp/a.strip" "$tmp/off.txt" | head -20 >&2 || true; }
if grep -q '^##PERF##' "$tmp/off.txt"; then fail "--perf=0 still prints ##PERF##"; fi
n=$(grep -c '^##PERF##' "$tmp/a.txt" || true)
[ "$n" -eq $((2 * ${#arms[@]})) ] || fail "$n ##PERF## lines, expected $((2 * ${#arms[@]})) (arms x seeds)"

for arm in "${arms[@]}"; do
    rows=$(python3 "$bp" --get row --from perf --proto "$arm" "$tmp/a.txt")
    # row: scenario seed cpu_s wall_s events maxrss_kb
    seeds=$(awk '{print $2}' <<<"$rows" | tr '\n' ' ')
    [ "$seeds" = "3 4 " ] || fail "$arm: ##PERF## seeds '$seeds', expected '3 4 '"
    awk '$1 != "paper" || $5 <= 0 || $6 != "-" {bad = 1} END {exit bad}' <<<"$rows" \
        || fail "$arm: bad ##PERF## rows (scenario paper, events > 0, maxrss '-'): $rows"
    echo "ok    $arm, shared process: $(tr '\n' ';' <<<"$rows")"
done

arm=${arms[0]}
./ns3 run --no-build "routing-compare --scenario=paper --nNodes=12 --time=10 --runs=1 --protocols=$arm" \
    > "$tmp/one.txt" 2>> "$tmp/stderr.log"
rss=$(python3 "$bp" --get maxrss_kb --from perf --proto "$arm" "$tmp/one.txt")
if ! [[ "$rss" =~ ^[1-9][0-9]*$ ]]; then fail "$arm alone: maxrss_kb '$rss', expected a number of kB"; fi
echo "ok    $arm alone: maxrss_kb=$rss"

[ "$fails" -eq 0 ] || { tail -20 "$tmp/stderr.log" >&2; echo "check-perf-markers: $fails failure(s)" >&2; exit 1; }
echo "check-perf-markers: OK (${arms[*]})"
