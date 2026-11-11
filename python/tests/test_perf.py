#!/usr/bin/env python3
# Copyright (c) 2026 Daniel Joppi (https://github.com/swarm-routing)
# SPDX-License-Identifier: MIT OR GPL-2.0-only
"""Self-test for the ##PERF## line (ns3-tools#91) in bench_parse.py.

1. The reader: routing-bench's per-arm, per-seed form
   ``##PERF## <scenario> <arm> <seed> <cpu_s> <wall_s> <events> <maxrss_kb>``
   (maxrss "-" when arms share the process) and AntHocNet's per-process form
   ``##PERF## <scenario> all <wall_s> <maxrss_kb>`` (AntHocNet#131), through
   parse_perf and ``--get FIELD --from perf``.
2. ##PERF## is cost, never a result (org rule 5): on every fixture cell, a
   ##PERF## line after each ##RUN## row changes no parsed result, no --control
   verdict, no scenario_check report, and the compare table only by the
   ##PERF## lines it passes through.

Run: python3 test_perf.py   (no pytest; exits non-zero on the first failure)
"""

import contextlib
import glob
import io
import os
import subprocess
import sys

TESTS = os.path.dirname(os.path.abspath(__file__))
HERE = os.path.join(os.path.dirname(TESTS), "ns3_tool")
CELLS = sorted(glob.glob(os.path.join(TESTS, "fixtures", "cells", "*.txt")))
sys.path.insert(0, HERE)
import bench_parse as bp  # noqa: E402 (after the sys.path insert)

SHARED = (
    "##RUN## 3 olsr 80.0 10.0 20.0 30.0 1.00 2.0 inf inf 0.1\n"
    "##PERF## paper olsr 3 1.250 1.302 184233 -\n"
    "##RUN## 3 dsdv 70.0 10.0 20.0 30.0 1.00 2.0 inf inf 0.1\n"
    "##PERF## paper dsdv 3 0.910 0.950 120001 -\n"
    "##RUN## 4 olsr 81.0 10.0 20.0 30.0 1.00 2.0 inf inf 0.1\n"
    "##PERF## paper olsr 4 1.300 1.350 190000 -\n"
)
ALONE = "##PERF## - aodv 1 2.000 2.500 500000 81234\n"
PROCESS = "##PERF## paper-mobile all 512.3 734212\n"


def check(cond, msg):
    if not cond:
        print("FAIL:", msg)
        sys.exit(1)


def test_parse_forms():
    rows = bp.parse_perf(SHARED + ALONE + PROCESS + "##PERF## too few\n")
    check(len(rows) == 5, f"5 rows (the 2-field line skipped), got {len(rows)}")
    r = rows[0]
    check(
        (r["scenario"], r["arm"], r["seed"], r["cpu_s"], r["wall_s"], r["events"])
        == ("paper", "olsr", 3, 1.25, 1.302, 184233),
        f"per-arm row {r}",
    )
    check(r["maxrss_kb"] is None, "maxrss '-' (arms share the process) reads None")
    check(abs(r["events_per_s"] - 184233 / 1.302) < 1e-6, "events_per_s = events / wall_s")
    a = rows[3]
    check(a["scenario"] == "-" and a["maxrss_kb"] == 81234, f"one arm alone {a}")
    p = rows[4]
    check(
        (p["arm"], p["wall_s"], p["maxrss_kb"], p["seed"], p["cpu_s"], p["events"])
        == ("all", 512.3, 734212, None, None, None),
        f"AntHocNet's per-process form {p}",
    )
    check(p["events_per_s"] is None, "no events, no rate")
    print("ok: parse_perf reads the per-arm form, '-' and AntHocNet's 'all' form")


def test_query():
    q = bp.query
    check(
        q(SHARED, "olsr", "row", "perf")
        == ["paper 3 1.250 1.302 184233 -", "paper 4 1.300 1.350 190000 -"],
        "rows",
    )
    check(q(SHARED, "olsr", "events", "perf") == ["190000"], "last row wins")
    check(q(SHARED, "dsdv", "maxrss_kb", "perf") == ["-"], "'-' prints as written")
    check(q(ALONE, "aodv", "maxrss_kb", "perf") == ["81234"], "one arm alone")
    check(q(PROCESS, "all", "wall_s", "perf") == ["512.3"], "the 'all' form by name")
    check(q(PROCESS, "all", "events", "perf") == [], "a field the form lacks is empty")
    check(q(SHARED, "nosuch", "cpu_s", "perf") == [], "missing arm is empty")
    out = subprocess.run(
        [
            sys.executable,
            os.path.join(HERE, "bench_parse.py"),
            "--get",
            "cpu_s",
            "--from",
            "perf",
            "--proto",
            "dsdv",
            "-",
        ],
        input=SHARED,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    check(out == "0.910\n", f"CLI --from perf: {out!r}")
    print("ok: --get FIELD --from perf reads ##PERF## fields by name")


def with_perf(text):
    """The cell with a ##PERF## line after each ##RUN## row, as routing-bench prints it."""
    out = []
    for line in text.splitlines(keepends=True):
        out.append(line)
        m = bp.RUN.search(line)
        if m:
            out.append(f"##PERF## paper {m.group(2)} {m.group(1)} 1.234 1.300 123456 -\n")
    return "".join(out)


def strip_perf(text):
    return "".join(ln for ln in text.splitlines(keepends=True) if not ln.startswith("##PERF##"))


def run_main(argv):
    old, sys.argv = sys.argv, ["bench_parse.py"] + argv
    out, code = io.StringIO(), 0
    try:
        with contextlib.redirect_stdout(out):
            bp.main()
    except SystemExit as e:
        code = e.code or 0
    finally:
        sys.argv = old
    return out.getvalue(), code


def test_results_unchanged(tmp):
    check(len(CELLS) >= 13, f"fixture cells: {len(CELLS)}")
    n = 0
    for path in CELLS:
        with open(path) as f:
            text = f.read()
        perf = with_perf(text)
        check(strip_perf(perf) == text, f"{path}: the fixture already carries ##PERF##")
        for fn in (bp.parse_cell, bp.parse_runs, bp.parse_stddev):
            check(fn(perf) == fn(text), f"{os.path.basename(path)}: {fn.__name__} moved")
        for p in bp.parse_runs(text):
            check(bp.control(text, perf, p)[1] is False, f"{path}: --control fails on ##PERF##")
            check(bp.query(perf, p, "row", "run") == bp.query(text, p, "row", "run"), "run rows")
        n += text.count("##RUN##")
        a, b = os.path.join(tmp, "a.txt"), os.path.join(tmp, "b.txt")
        with open(a, "w") as f:
            f.write(text)
        with open(b, "w") as f:
            f.write(perf)
        sc = os.path.join(HERE, "scenario_check.py")
        ra = subprocess.run(
            [sys.executable, "-I", sc, "results", a], capture_output=True, text=True
        )
        rb = subprocess.run(
            [sys.executable, "-I", sc, "results", b], capture_output=True, text=True
        )
        check(
            (ra.returncode, ra.stdout.replace("a.txt", "X"))
            == (rb.returncode, rb.stdout.replace("b.txt", "X")),
            f"{os.path.basename(path)}: scenario_check reports differ with ##PERF##",
        )
        oa, ca = run_main(["--all", a, a])
        ob, cb = run_main(["--all", b, b])
        check(ca == cb, f"{path}: compare exit moved")
        check(
            strip_perf(ob.replace("b.txt", "a.txt")) == oa,
            f"{path}: compare output moved beyond ##PERF##",
        )
    check(n >= 40, f"##RUN## rows covered: {n}")
    print(f"ok: ##PERF## changes no result on {len(CELLS)} fixture cells ({n} ##RUN## rows)")


if __name__ == "__main__":
    import tempfile

    test_parse_forms()
    test_query()
    with tempfile.TemporaryDirectory() as d:
        test_results_unchanged(d)
