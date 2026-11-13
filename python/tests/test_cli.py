#!/usr/bin/env python3
# Copyright (c) 2026 Daniel Joppi (https://github.com/swarm-routing)
# SPDX-License-Identifier: MIT OR GPL-2.0-only
"""Self-test for the `ns3-tool` CLI (#39): compare, sweep, diagnose, scaffold.

1. `ns3-tool --help` lists the four subcommands.
2. compare: byte-identical to `ns3-bench-parse` (bench_parse.py) on every
   fixture cell, alone and as A/B pairs.
3. sweep --dry-run: the command lines AntHocNet's run-scenarios.py prints for
   the invocation its benchmarks.yml commits (`--only discrete --runs 10
   --time 120`), recorded below; with NS3_TOOL_ANTHOCNET=<checkout>, also
   against that script itself on several option sets.
4. diagnose: the drop book of a fixture cell, a malformed field refused, and
   `--check` running scenario_check's results.
5. scaffold: the skeleton's files, the registration function, refusals. (CI
   builds the skeleton beside routing-bench: ns3.yml, job `scaffold`.)

Run: python3 test_cli.py   (no pytest; exits non-zero on the first failure)
"""

import contextlib
import glob
import io
import os
import subprocess
import sys
import tempfile

TESTS = os.path.dirname(os.path.abspath(__file__))
HERE = os.path.join(os.path.dirname(TESTS), "ns3_tool")
CELLS = sorted(glob.glob(os.path.join(TESTS, "fixtures", "cells", "*.txt")))
CLI = os.path.join(HERE, "cli.py")
BENCH_PARSE = os.path.join(HERE, "bench_parse.py")
sys.path.insert(0, HERE)
import diagnose  # noqa: E402 (after the sys.path insert)
import scaffold  # noqa: E402


def check(cond, msg):
    if not cond:
        print("FAIL:", msg)
        sys.exit(1)


def run(*args, **kw):
    return subprocess.run([sys.executable, *args], capture_output=True, text=True, **kw)


def test_help():
    p = run(CLI, "--help")
    check(p.returncode == 0, f"--help exits 0: {p}")
    for cmd in ("compare", "sweep", "diagnose", "scaffold"):
        check(f"  {cmd} " in p.stdout, f"--help lists {cmd}")
        q = run(CLI, cmd, "--help")
        check(q.returncode == 0 and "usage:" in q.stdout, f"{cmd} --help: {q.stderr}")
    p = run(CLI, "nonsense")
    check(p.returncode == 2 and "unknown command" in p.stderr, "an unknown command exits 2")
    print("ok   --help lists compare, sweep, diagnose, scaffold; each has its own --help")


def test_compare():
    n = 0
    for path in CELLS:
        a, b = run(BENCH_PARSE, "--all", path), run(CLI, "compare", "--all", path)
        check(
            (a.stdout, a.returncode) == (b.stdout, b.returncode), f"compare != bench_parse: {path}"
        )
        n += 1
    for extra in (["--ab", "--all"], ["--control"], ["--all"]):
        pair = CELLS[:2] if "--control" in extra else CELLS[:4]
        a, b = run(BENCH_PARSE, *extra, *pair), run(CLI, "compare", *extra, *pair)
        check((a.stdout, a.returncode) == (b.stdout, b.returncode), f"compare {extra} differs")
    print(
        f"ok   compare is ns3-bench-parse byte for byte on {n} fixture cells and 3 multi-cell forms"
    )


# AntHocNet's run-scenarios.py, as its benchmarks.yml runs it:
#   run-scenarios.py /opt/ns-3 --out scenarios.csv --only discrete --runs 10 --time 120 --dry-run
COMMITTED = ["/opt/ns-3", "--only", "discrete", "--runs", "10", "--time", "120"]
EXPECTED = """\
[dry-run] cd "/opt/ns-3" && ./ns3 run "anthocnet-compare --csv --nNodes=16 --area=250 --speed=5 --pause=1 --flows=4 --range=0 --runs=10 --time=120 --protocols=anthocnet,aodv,olsr,dsdv"
[dry-run] cd "/opt/ns-3" && ./ns3 run "anthocnet-compare --csv --scenario=paper --runs=10 --time=120 --protocols=anthocnet,aodv,olsr,dsdv"
[dry-run] cd "/opt/ns-3" && ./ns3 run "anthocnet-compare --csv --scenario=paper --pause=900 --runs=10 --time=120 --protocols=anthocnet,aodv,olsr,dsdv"
[dry-run] cd "/opt/ns-3" && ./ns3 run "anthocnet-compare --csv --scenario=paper --pause=0 --runs=10 --time=120 --protocols=anthocnet,aodv,olsr,dsdv"
[dry-run] cd "/opt/ns-3" && ./ns3 run "anthocnet-compare --csv --nNodes=50 --areaX=1500 --areaY=300 --speed=20 --pause=30 --range=300 --flows=40 --cbrBps=4096 --runs=10 --time=120 --protocols=anthocnet,aodv,olsr,dsdv"
[dry-run] cd "/opt/ns-3" && ./ns3 run "anthocnet-compare --csv --scenario=paper --nNodes=100 --areaX=2100 --areaY=700 --runs=10 --time=120 --protocols=anthocnet,aodv,olsr,dsdv"
"""
ANTHOCNET = ["--program", "anthocnet-compare", "--protocols", "anthocnet,aodv,olsr,dsdv"]


def test_sweep():
    with tempfile.TemporaryDirectory() as d:
        p = run(CLI, "sweep", *COMMITTED, *ANTHOCNET, "--dry-run", cwd=d)
        check(p.returncode == 0 and p.stderr == EXPECTED, f"sweep --dry-run:\n{p.stderr}")
        check(os.listdir(d) == [], "sweep --dry-run writes no file")
        p = run(CLI, "sweep", "/opt/ns-3", "--only", "area", "--point", "1900", "--dry-run", cwd=d)
        check(
            p.stderr.strip()
            == '[dry-run] cd "/opt/ns-3" && ./ns3 run "routing-compare --csv --scenario=paper '
            '--areaX=1900 --runs=5 --protocols=aodv,olsr,dsdv"',
            f"sweep defaults: routing-compare and the stock arms ({p.stderr})",
        )
        # Refused before any output file is opened, so an earlier result survives.
        out = os.path.join(d, "kept.csv")
        with open(out, "w") as f:
            f.write("earlier result\n")
        for bad, needle in (
            (["--only", "aera"], "--only 'aera' is not one of"),
            (["--only", "area", "--point", "1"], "--point '1' not in sweep 'area'"),
            (["--extra-args=--protocols=olsr"], "not in --extra-args"),
        ):
            p = run(CLI, "sweep", "/opt/ns-3", "--out", out, *bad, cwd=d)
            check(p.returncode != 0 and needle in p.stderr, f"sweep {bad}: {p.stderr}")
        with open(out) as f:
            check(f.read() == "earlier result\n", "sweep: a refused selection writes nothing")
    print(
        "ok   sweep --dry-run prints AntHocNet's committed command lines; defaults name no protocol of ours"
    )
    print("ok   sweep refuses a bad --only, --point or an arm list in --extra-args before writing")
    root = os.environ.get("NS3_TOOL_ANTHOCNET")
    if not root:
        print("     (skipped the live comparison: set NS3_TOOL_ANTHOCNET=<AntHocNet checkout>)")
        return
    theirs = os.path.join(root, "ns3", "tools", "run-scenarios.py")
    sets = [
        COMMITTED,
        ["/opt/ns-3"],
        ["/opt/ns-3", "--quick"],
        ["/opt/ns-3", "--only", "area", "--point", "1900"],
        ["/opt/ns-3", "--only", "sweeps", "--run-first", "11", "--propagation", "tworay"],
        ["/x", "--only", "scale", "--extra-args=--ns3::anthocnet::RoutingProtocol::BetaData=20"],
    ]
    with tempfile.TemporaryDirectory() as d:
        for args in sets:
            a = run(theirs, *args, "--protocols", "anthocnet,aodv,olsr,dsdv", "--dry-run", cwd=d)
            b = run(CLI, "sweep", *args, *ANTHOCNET, "--dry-run", cwd=d)
            check(a.returncode == 0 and a.stderr == b.stderr, f"sweep {args} != run-scenarios.py")
    print(f"ok   NS3_TOOL_ANTHOCNET: sweep --dry-run = run-scenarios.py on {len(sets)} option sets")


def test_diagnose():
    cell = os.path.join(TESTS, "fixtures", "cells", "static-mesh-cap1s.txt")
    p = run(CLI, "diagnose", cell)
    lines = p.stdout.splitlines()
    check(p.returncode == 0 and lines[0].split()[:3] == ["arm", "lines", "pdr"], f"diagnose: {p}")
    aodv = next(ln for ln in lines if ln.startswith("aodv "))
    check(aodv.split()[1:4] == ["1", "95.99", "2.43"], f"diagnose: aodv's drop book ({aodv})")
    check("[route: setup=- reconv=- repair=-]" in p.stdout, "diagnose: protocol causes as printed")
    p = run(CLI, "diagnose", cell, "--proto", "olsr")
    check([ln.split()[0] for ln in p.stdout.splitlines()[1:2]] == ["olsr"], "diagnose --proto")
    with open(cell) as f:
        text = f.read()
    try:
        diagnose.report(text.replace("route=2.43", "route=2.4x"), "bad.txt")
    except diagnose.Malformed as e:
        check("bad.txt:" in str(e) and "route" in str(e), f"diagnose: the message ({e})")
    else:
        check(False, "diagnose accepted a malformed drop field")
    ids = [c for c in CELLS if "##DROPID##" in open(c).read()]
    p = run(CLI, "diagnose", ids[0])
    check(p.returncode == 0 and "##DROPID## " in p.stdout, f"diagnose: ##DROPID## means ({ids[0]})")
    a = run(os.path.join(HERE, "scenario_check.py"), "results", cell)
    b = run(CLI, "diagnose", cell, "--check")
    check(
        b.returncode == a.returncode and b.stdout.endswith(a.stdout), "diagnose --check = results"
    )
    print("ok   diagnose: the drop book, ##DROPID## means, a malformed field refused, --check")


def test_scaffold():
    with tempfile.TemporaryDirectory() as d:
        with contextlib.redirect_stdout(io.StringIO()):
            code = scaffold.main(["my-arm", "--out", d, "--holder", "A. Person", "--year", "2027"])
        check(code == 0, "scaffold writes a skeleton")
        root = os.path.join(d, "my-arm")
        got = sorted(os.path.relpath(p, root) for p in glob.glob(root + "/**/*", recursive=True)
                     if os.path.isfile(p))  # fmt: skip
        check(
            got == ["CMakeLists.txt", "README.md", "helper/my-arm-bench-arms.cc",
                    "model/my-arm.cc", "model/my-arm.h"],
            f"scaffold: the files ({got})",
        )  # fmt: skip
        arm = open(os.path.join(root, "helper", "my-arm-bench-arms.cc")).read()
        check("void RegisterArms_my_arm()" in arm and 'RegisterArm("my-arm",' in arm,
              "scaffold: RegisterArms_<module> with hyphens as underscores, the arm's name")  # fmt: skip
        check(arm.startswith("// Copyright (c) 2027 A. Person\n// SPDX-License-Identifier: GPL-2.0-only"),
              "scaffold: the holder's GPL-2.0-only header")  # fmt: skip
        cmake = open(os.path.join(root, "CMakeLists.txt")).read()
        check("LIBNAME my-arm" in cmake and "helper/my-arm-bench-arms.cc" in cmake
              and "IN_LIST ns3-all-enabled-modules" in cmake,
              "scaffold: the arm file only where routing-bench is enabled")  # fmt: skip
        with contextlib.redirect_stderr(io.StringIO()) as err:
            again = scaffold.main(["my-arm", "--out", d])
            bad = [scaffold.main([n, "--out", d]) for n in ("core", "routing-bench", "My_Arm", "x")]
        check(again == 2 and "exists" in err.getvalue(), "scaffold: never overwrites")
        check(bad == [2, 2, 2, 2], "scaffold: refuses core, routing-bench and bad names")
    print("ok   scaffold: the module, its arm registration, refusals")


def main():
    test_help()
    test_compare()
    test_sweep()
    test_diagnose()
    test_scaffold()
    print("test_cli: all ok")


if __name__ == "__main__":
    main()
