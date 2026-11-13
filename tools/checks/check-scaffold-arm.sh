#!/usr/bin/env bash
# Copyright (c) 2026 Daniel Joppi (https://github.com/swarm-routing)
# SPDX-License-Identifier: MIT OR GPL-2.0-only
#
# check-scaffold-arm.sh NS3DIR ARM
#
# The arm `ns3-tool scaffold ARM` wrote, built beside routing-bench (#39):
#   - routing-compare offers it (the arm list in --PrintHelp comes from the
#     registry, so it is there only if helper/ARM-bench-arms.cc registered);
#   - it delivers (a ##RUN## row with pdr > 0, read through the readers by
#     arm-delivers.sh, as check-stock-arms.sh reads the stock arms).
set -euo pipefail
ns3=$1
arm=$2
budget=${BUDGET:-600}
delivers="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/arm-delivers.sh"
cd "$ns3"

help=$(./ns3 run --no-build "routing-compare --PrintHelp" 2>&1 || true)
list=$(printf '%s\n' "$help" | sed -n 's/.*Comma-separated list (\([^)]*\)).*/\1/p')
[[ ",$list," == *",$arm,"* ]] \
    || { echo "FAIL: routing-compare does not offer $arm (it offers: ${list:-nothing})" >&2; exit 1; }
echo "ok    routing-compare offers $arm ($list)"

out=$(timeout "$budget" ./ns3 run --no-build \
    "routing-compare --scenario=paper --nNodes=20 --time=30 --runs=1 --protocols=$arm" 2>&1) \
    || { echo "FAIL: $arm did not run" >&2; tail -20 <<<"$out" >&2; exit 1; }
row=$(bash "$delivers" "$arm" <<<"$out") \
    || { echo "FAIL: $arm delivered nothing: ${row:-no ##RUN## row}" >&2; exit 1; }
echo "ok    $arm delivers: $row"
