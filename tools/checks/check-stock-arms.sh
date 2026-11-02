#!/usr/bin/env bash
# Copyright (c) 2026 Daniel Joppi (https://github.com/swarm-routing)
# SPDX-License-Identifier: MIT OR GPL-2.0-only
#
# check-stock-arms.sh NS3DIR "ARMS"
#
# routing-bench alone, with stock ns-3 only (AntHocNet's routing-bench-alone
# job, moved with the harness, ns3-tools#2):
#   - routing-compare offers exactly the stock arms;
#   - every arm in ARMS delivers (a ##RUN## row with pdr > 0);
#   - an arm that is not installed aborts with "unknown protocol" (#448).
# Each run has a wall-clock budget, so a hang (ns-3.49's AODV, #843 /
# ns3-containers#35) fails the check instead of running to the job timeout.
set -euo pipefail
ns3=$1
arms=$2
budget=${BUDGET:-600}
cd "$ns3"

help=$(./ns3 run "routing-compare --PrintHelp" 2>&1 || true)
printf '%s\n' "$help" | grep -q 'Comma-separated list (aodv,dsdv,hwmp,olsr)' \
    || { echo "FAIL: routing-compare alone should offer exactly the stock arms" >&2
         printf '%s\n' "$help" | grep -i protocols >&2; exit 1; }

fails=0
for arm in $arms; do
    if [ "$arm" = hwmp ]; then  # 802.11s needs its own radio settings (#708)
        args="--scenario=paper --nNodes=15 --time=20 --runs=1 --protocols=hwmp --standard=80211a --rateManager=constant6"
    else
        args="--scenario=paper --nNodes=20 --time=30 --runs=1 --protocols=$arm"
    fi
    start=$(date +%s)
    rc=0
    out=$(timeout "$budget" ./ns3 run "routing-compare $args" 2>&1) || rc=$?
    took=$(( $(date +%s) - start ))
    if [ "$rc" -eq 124 ]; then
        echo "FAIL: $arm did not finish within ${budget} s (hang)" >&2; fails=$((fails + 1)); continue
    elif [ "$rc" -ne 0 ]; then
        echo "FAIL: $arm exited $rc" >&2; tail -20 <<<"$out" >&2; fails=$((fails + 1)); continue
    fi
    row=$(awk -v a="$arm" '$1 == "##RUN##" && $3 == a' <<<"$out")
    if awk '$4 > 0 {f = 1} END {exit !f}' <<<"$row"; then
        echo "ok    $arm (${took} s): $row"
    else
        echo "FAIL: $arm delivered nothing: ${row:-no ##RUN## row}" >&2; fails=$((fails + 1))
    fi
done

if ./ns3 run "routing-compare --protocols=no-such-arm --nNodes=8 --time=5" >/tmp/u.log 2>&1; then
    echo "FAIL: an arm that is not installed must abort (#448)" >&2; fails=$((fails + 1))
elif ! grep -q "unknown protocol 'no-such-arm'" /tmp/u.log; then
    echo "FAIL: the abort does not name the unknown arm" >&2; tail -5 /tmp/u.log >&2; fails=$((fails + 1))
else
    echo "ok    an unknown arm aborts"
fi
[ "$fails" -eq 0 ] || { echo "check-stock-arms: $fails failure(s)" >&2; exit 1; }
echo "check-stock-arms: OK ($arms)"
