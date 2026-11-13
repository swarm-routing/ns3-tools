#!/usr/bin/env python3
# Copyright (c) 2026 Daniel Joppi (https://github.com/swarm-routing)
# SPDX-License-Identifier: MIT OR GPL-2.0-only
"""ns3-tool: one entry point over the readers (ns3-tools#39).

    ns3-tool compare  FILE [FILE ...] [options]   paired per-seed deltas and the noise verdict
    ns3-tool sweep    NS3DIR [options]            the scenario matrix and sweeps through the harness
    ns3-tool diagnose CELL [--check ...]          the drop book; scenario_check's results check
    ns3-tool scaffold NAME [--out DIR]            an arm module skeleton for routing-bench

Each subcommand takes the options of the module it runs (`ns3-tool
compare --help` is bench_parse's): compare is bench_parse, so its output is
`ns3-bench-parse`'s byte for byte; sweep is sweep.py, a port of AntHocNet's
run-scenarios.py; diagnose is diagnose.py; scaffold is scaffold.py. The
console scripts the package installed before (ns3-bench-parse,
ns3-sweep-summary, ns3-scenario-check, ...) stay, as aliases of the same
modules. Standard library only.
"""

import sys

try:  # as the ns3_tool package, or as a script beside its siblings
    from . import bench_parse, diagnose, scaffold, sweep
except ImportError:
    import bench_parse
    import diagnose
    import scaffold
    import sweep


def _compare(argv):
    """bench_parse.main reads sys.argv: run it with the subcommand's arguments."""
    saved = sys.argv
    sys.argv = ["ns3-tool compare", *argv]
    try:
        bench_parse.main()
    finally:
        sys.argv = saved
    return 0


COMMANDS = {
    "compare": (_compare, "paired per-seed deltas and the noise verdict (bench_parse)"),
    "sweep": (sweep.main, "the scenario matrix and sweeps through the harness (run-scenarios)"),
    "diagnose": (diagnose.main, "a cell's drop book; --check: scenario_check results"),
    "scaffold": (scaffold.main, "an arm module skeleton that registers with routing-bench"),
}


def usage():
    lines = ["usage: ns3-tool {" + ",".join(COMMANDS) + "} ...", "", __doc__.split("\n")[0], ""]
    lines += [f"  {name:<9} {text}" for name, (_f, text) in COMMANDS.items()]
    lines += ["", "ns3-tool <command> --help: that command's options"]
    return "\n".join(lines)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(usage())
        return 0 if argv else 2
    cmd = COMMANDS.get(argv[0])
    if cmd is None:
        print(f"ns3-tool: unknown command '{argv[0]}'\n\n{usage()}", file=sys.stderr)
        return 2
    return cmd[0](argv[1:]) or 0


if __name__ == "__main__":
    sys.exit(main())
