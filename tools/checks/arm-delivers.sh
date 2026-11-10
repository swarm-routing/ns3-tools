#!/usr/bin/env bash
# Copyright (c) 2026 Daniel Joppi (https://github.com/swarm-routing)
# SPDX-License-Identifier: MIT OR GPL-2.0-only
#
# arm-delivers.sh ARM < OUTPUT
#
# The delivery verdict of the gates (ns3-tools#6): exit 0 if ARM's ##RUN##
# row in a harness OUTPUT (stdin) has pdr > 0, else 1. The row is read by
# name through the readers (bench_parse --get, the code python/tests cover),
# never by awk column position, so a marker change the reader absorbs cannot
# make the gate read the wrong number. Prints ARM's ##RUN## row(s) (seed and
# fields, as bench_parse --get row gives them) for the log, or nothing.
# With several runs the last row decides, as bench_parse --get does; the
# gates run --runs=1.
set -euo pipefail
arm=$1
bp="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../python/ns3_tool" && pwd)/bench_parse.py"
out=$(cat)
python3 "$bp" --get row --proto "$arm" - <<<"$out"
pdr=$(python3 "$bp" --get pdr --proto "$arm" - <<<"$out")
[ -n "$pdr" ] && awk -v v="$pdr" 'BEGIN { exit !(v > 0) }'
