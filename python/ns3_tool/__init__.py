# Copyright (c) 2026 Daniel Joppi (https://github.com/swarm-routing)
# SPDX-License-Identifier: MIT OR GPL-2.0-only
"""ns3-tool: readers and statistics for the harness's marker lines.

Ported from danieljoppi/AntHocNet tools/bench/ (swarm-routing/ns3-tools#3):
bench_parse (paired per-seed stats, noise verdict), stats_util, native_view,
comparator_view, pool_runs, sweep_summary; behind one CLI, `ns3-tool`
(cli: compare, sweep, diagnose, scaffold; #39). Standard library only. Each module
also runs as a script, as it did in AntHocNet.
"""
