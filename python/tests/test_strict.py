#!/usr/bin/env python3
# Copyright (c) 2026 Daniel Joppi (https://github.com/swarm-routing)
# SPDX-License-Identifier: MIT OR GPL-2.0-only
"""Self-test for the strict readers (#58).

A marker line (``##RUN##``, ``##BENCH##``, ``# stddev``) or a campaign-CSV
value that is present but not a number used to be skipped, so a verdict could
silently run on fewer seeds. Now:

1. Strict is the default: each malformed case fails (exit 2) and the message
   names the file, the line and the field.
2. ``--lenient`` reproduces the old output byte for byte and reports on
   stderr how many fields and rows it dropped.
3. Every fixture cell (``fixtures/cells/``) reads the same strict and lenient,
   with nothing dropped, so the fixture corpus parses byte-identically.

Each case corrupts one value of a fixture cell (or of a small CSV written
here) into a temporary file.

Run: python3 test_strict.py   (no pytest; exits non-zero on the first failure)
"""

import glob
import os
import subprocess
import sys
import tempfile

TESTS = os.path.dirname(os.path.abspath(__file__))
HERE = os.path.join(os.path.dirname(TESTS), "ns3_tool")
CELL = os.path.join(TESTS, "fixtures", "cells", "static-mesh-cap1s.txt")
CELLS = sorted(glob.glob(os.path.join(TESTS, "fixtures", "cells", "*.txt")))
sys.path.insert(0, HERE)
import bench_parse as bp  # noqa: E402 (after the sys.path insert)

BENCH_PARSE = os.path.join(HERE, "bench_parse.py")
SWEEP = os.path.join(HERE, "sweep_summary.py")


def check(cond, msg):
    if not cond:
        print("FAIL:", msg)
        sys.exit(1)


def run(*args):
    return subprocess.run([sys.executable, *args], capture_output=True, text=True)


def corrupt(text, marker, field_index, value):
    """``text`` with field ``field_index`` (0 = the first after the marker's
    fixed prefix) of the first ``marker`` line replaced; returns (text, lineno)."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(marker):
            toks = line.split()
            toks[field_index] = value
            lines[i] = " ".join(toks)
            return "\n".join(lines) + "\n", i + 1
    raise AssertionError(f"no {marker} line")


def write(tmp, name, text):
    path = os.path.join(tmp, name)
    with open(path, "w") as f:
        f.write(text)
    return path


def expect_strict(args, path, lineno, field):
    proc = run(*args)
    check(proc.returncode == 2, f"{args}: strict exit {proc.returncode}, want 2\n{proc.stderr}")
    want = f"{path}:{lineno}:"
    check(want in proc.stderr, f"{args}: message lacks {want!r}: {proc.stderr}")
    check(field in proc.stderr, f"{args}: message lacks the field {field!r}: {proc.stderr}")
    check(proc.stdout == "", f"{args}: strict printed a result: {proc.stdout}")


def expect_lenient(args, fields, rows):
    proc = run(*args, "--lenient")
    check(proc.returncode in (0, 1), f"{args} --lenient: exit {proc.returncode}\n{proc.stderr}")
    want = f"dropped {fields} malformed field(s), {rows} row(s)"
    check(want in proc.stderr, f"{args} --lenient: no {want!r} in {proc.stderr!r}")
    return proc.stdout


def test_cells(tmp):
    with open(CELL) as f:
        text = f.read()
    # ##RUN## <run> <proto> pdr delay ...: split index 3 is pdr, 4 delay.
    cases = [
        ("run-metric", "##RUN##", 4, "5##RSS##", "delay", 1, 0),
        ("run-pdr", "##RUN##", 3, "9x.5", "pdr", 1, 1),
        ("stddev", "# stddev", 4, "delay=1.2.3", "delay", 1, 0),
    ]
    for name, marker, idx, value, field, fields, rows in cases:
        bad, lineno = corrupt(text, marker, idx, value)
        path = write(tmp, f"{name}.txt", bad)
        expect_strict([BENCH_PARSE, "--all", path], path, lineno, field)
        out = expect_lenient([BENCH_PARSE, "--all", path], fields, rows)
        check(out, f"{name}: lenient printed nothing")
        print(
            f"ok   {name}: strict fails at {os.path.basename(path)}:{lineno} ({field}); "
            f"lenient drops {fields} field(s), {rows} row(s)"
        )
    # ##BENCH## <proto> pdr ...: a compact line appended to a cell.
    bench = text + "##BENCH## zrp 9o.1 10.0 20.0 30.0 1.00\n"
    path = write(tmp, "bench.txt", bench)
    lineno = len(bench.splitlines())
    expect_strict([BENCH_PARSE, "--all", path], path, lineno, "pdr")
    expect_lenient([BENCH_PARSE, "--all", path], 1, 1)
    print(f"ok   bench: strict fails at bench.txt:{lineno} (pdr); lenient drops 1 field, 1 row")
    # A ##RUN## row cut by interleaved output: the seed would be lost.
    lines = text.splitlines()
    i = next(n for n, ln in enumerate(lines) if ln.startswith("##RUN##"))
    lines[i] = "##RUN## 6 ao##RSS## t=660s top=ns3 pid=457 rss_kb=107788"
    path = write(tmp, "cut-row.txt", "\n".join(lines) + "\n")
    expect_strict([BENCH_PARSE, "--all", path], path, i + 1, "row")
    expect_lenient([BENCH_PARSE, "--all", path], 1, 1)
    print(f"ok   cut-row: strict fails at cut-row.txt:{i + 1}; lenient drops 1 row")
    # A '# stddev' line left with no value counts as a dropped row.
    bad, lineno = corrupt(text, "# stddev", 3, "pdr=x")
    lines = bad.splitlines()
    lines[lineno - 1] = " ".join(lines[lineno - 1].split()[:4])
    path = write(tmp, "stddev-row.txt", "\n".join(lines) + "\n")
    expect_lenient([BENCH_PARSE, "--all", path], 1, 1)
    print("ok   stddev-row: a '# stddev' line with no usable value counts as a dropped row")
    # --control reads ##RUN## rows: a malformed baseline row fails too.
    bad, lineno = corrupt(text, "##RUN##", 5, "x")
    path = write(tmp, "control.txt", bad)
    expect_strict([BENCH_PARSE, "--control", CELL, path], path, lineno, "delay99")
    print("ok   control: a malformed row in either cell fails")


def test_inf_allowed():
    text = "##RUN## 1 olsr 80.0 10.0 20.0 30.0 1.00 2.0 inf inf 0.1\n"
    runs = bp.parse_runs(text)
    check(runs["olsr"][1]["pdr"] == 80.0 and "off50" not in runs["olsr"][1], f"inf: {runs}")
    try:
        bp.parse_runs("##RUN## 1 olsr 80.0 1O.0\n", bp.Reader(where="x.txt"))
    except bp.Malformed as e:
        check("x.txt:1:" in str(e) and "delay" in str(e), f"message: {e}")
    else:
        check(False, "parse_runs accepted a malformed delay")
    print("ok   inf still parses; parse_runs raises Malformed naming file, line and field")


CSV_HEAD = "kind,group,x,protocol,runs,pdr_pct,delay_ms,delay99_ms,nrl,pdr_sd\n"
CSV_ROWS = [
    "sweep,area,500,anthocnet,20,90.0,30.0,300.0,20.0,1.0\n",
    "sweep,area,500,aodv,20,80.0,40.0,400.0,40.0,1.5\n",
    "discrete,paper-base,,anthocnet,20,95.0,30.0,300.0,20.0,1.0\n",
    "discrete,paper-base,,aodv,20,85.0,40.0,400.0,40.0,1.5\n",
]
RUNS_HEAD = "kind,group,x,protocol,run,pdr_pct,delay99_ms,nrl\n"


def test_csv(tmp):
    good = write(tmp, "good.csv", CSV_HEAD + "".join(CSV_ROWS))
    ref = run(SWEEP, good)
    check(ref.returncode == 0 and ref.stderr == "", f"good CSV: {ref}")
    check(expect_lenient_none(good) == ref.stdout, "good CSV: lenient output differs")
    # A corrupted required cell: the aodv sweep row's delay99_ms.
    rows = list(CSV_ROWS)
    rows[1] = rows[1].replace("400.0", "4oo.0")
    bad = write(tmp, "bad.csv", CSV_HEAD + "".join(rows))
    expect_strict([SWEEP, bad], bad, 3, "delay99_ms")
    out = expect_lenient([SWEEP, bad], 1, 0)
    check("area" in out, f"lenient CSV output: {out}")
    print("ok   csv: a corrupted required cell fails at bad.csv:3 (delay99_ms); lenient counts it")
    # A row that ends before a required column (csv gives None).
    rows = list(CSV_ROWS)
    rows[1] = rows[1].rsplit(",", 3)[0] + "\n"
    short = write(tmp, "short.csv", CSV_HEAD + "".join(rows))
    expect_strict([SWEEP, short], short, 3, "delay99_ms")
    print("ok   csv: a row that ends before a required column fails")
    # A sibling -runs.csv value the paired verdict reads.
    before = write(tmp, "before.csv", CSV_HEAD + "".join(CSV_ROWS))
    after = write(tmp, "after.csv", CSV_HEAD + "".join(CSV_ROWS))
    lines = [
        f"sweep,area,500,{p},{r},{80 + r},{300 + r},20.0\n"
        for p in ("anthocnet", "aodv")
        for r in range(1, 4)
    ]
    write(tmp, "before-runs.csv", RUNS_HEAD + "".join(lines))
    lines[0] = lines[0].replace(",81,", ",8l,")
    sib = write(tmp, "after-runs.csv", RUNS_HEAD + "".join(lines))
    expect_strict([SWEEP, after, "--vs", before], sib, 2, "pdr_pct")
    expect_lenient([SWEEP, after, "--vs", before], 1, 0)
    print("ok   csv: a corrupted -runs.csv value fails at after-runs.csv:2 (pdr_pct)")


def expect_lenient_none(path):
    proc = run(SWEEP, path, "--lenient")
    check(proc.returncode == 0 and proc.stderr == "", f"{path} --lenient: {proc}")
    return proc.stdout


def test_fixtures_unchanged():
    """Strict and lenient read every fixture cell alike, dropping nothing."""
    for path in CELLS:
        strict = run(BENCH_PARSE, "--all", path)
        lenient = run(BENCH_PARSE, "--all", "--lenient", path)
        check(strict.returncode == lenient.returncode, f"{path}: exit codes differ")
        check(strict.stdout == lenient.stdout, f"{path}: strict and lenient output differ")
        check(lenient.stderr == "", f"{path}: lenient dropped something: {lenient.stderr}")
    # The A/B and control paths over pairs of fixture cells.
    pair = [CELLS[0], CELLS[1]]
    for extra in (["--ab", "--all"], ["--control"]):
        a, b = run(BENCH_PARSE, *extra, *pair), run(BENCH_PARSE, *extra, "--lenient", *pair)
        check(a.stdout == b.stdout and a.returncode == b.returncode, f"{extra}: differ")
    print(f"ok   {len(CELLS)} fixture cells: strict output == lenient output, nothing dropped")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        test_cells(tmp)
        test_csv(tmp)
    test_inf_allowed()
    test_fixtures_unchanged()
    print("test_strict: all ok")


if __name__ == "__main__":
    main()
