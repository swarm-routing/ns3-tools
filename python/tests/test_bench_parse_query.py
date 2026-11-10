#!/usr/bin/env python3
# SPDX-License-Identifier: MIT OR GPL-2.0-only
# Copyright (C) 2026 Daniel Henrique Joppi
"""Self-test for bench_parse.py's --get field query (#791).

The CI gates in tools/checks/ used to read marker fields by position with awk.
They now call ``bench_parse.py --get``, so this checks two things:

1. Equivalence: on every committed cell in results/cells/, the query returns
   exactly what the awk it replaced printed (##RUN## pdr, summary-table pdr,
   '# paths' hopsMean). A gate converted to the query gives the same verdict.
2. Behaviour the awk could not offer: --csv columns are read by the header's
   name (a reordered header still yields the right column), and a missing
   protocol yields nothing with exit 0, so a gate under `set -e` still prints
   its own "no result" message.

Run: python3 test_bench_parse_query.py   (no pytest; exits non-zero on the
                                          first failure)
"""

import glob
import importlib.util
import os
import subprocess
import sys

TESTS = os.path.dirname(os.path.abspath(__file__))
HERE = os.path.join(os.path.dirname(TESTS), "ns3_tool")
# NS3_TOOL_CELLS: a full corpus (CI points it at AntHocNet's results/cells,
# 435 cells); otherwise the 13 fixture cells, one per scenario family.
CELLS = os.environ.get("NS3_TOOL_CELLS") or os.path.join(TESTS, "fixtures", "cells")
MIN_CELLS = 100 if os.environ.get("NS3_TOOL_CELLS") else 13
# Comparisons that must be non-empty (run, table, paths): AntHocNet's floors on
# the full corpus; on the fixtures, a floor below what they hold (40/28/28).
MIN_CMP = (100, 100, 10) if os.environ.get("NS3_TOOL_CELLS") else (20, 20, 10)


def _load(name):
    """Load a sibling module from source (same pattern as test_stats.py)."""
    path = os.path.join(HERE, name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, HERE)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


bp = _load("bench_parse")


def awk(prog, text, **var):
    args = ["awk"]
    for k, v in var.items():
        args += ["-v", f"{k}={v}"]
    out = subprocess.run(
        args + [prog], input=text, capture_output=True, text=True, check=True
    ).stdout
    return [ln for ln in out.splitlines() if ln != ""]


def check(cond, msg):
    if not cond:
        print("FAIL:", msg)
        sys.exit(1)


# check-stock-arms.sh before ns3-tools#6, verbatim: the arm's rows, then
# "delivers" if any has $4 (pdr) > 0.
AWK_STOCK_ROW = '$1 == "##RUN##" && $3 == a'
AWK_STOCK_DELIVERS = "$4 > 0 {f = 1} END {exit !f}"
DELIVERS = os.path.join(
    os.path.dirname(os.path.dirname(TESTS)), "tools", "checks", "arm-delivers.sh"
)


def old_stock_verdict(text, arm):
    row = "\n".join(awk(AWK_STOCK_ROW, text, a=arm)) + "\n"
    return subprocess.run(["awk", AWK_STOCK_DELIVERS], input=row, text=True).returncode == 0


def delivers(text, arm):
    """(verdict, printed rows) of tools/checks/arm-delivers.sh, as the gate calls it."""
    proc = subprocess.run(["bash", DELIVERS, arm], input=text, capture_output=True, text=True)
    check(proc.returncode in (0, 1), f"arm-delivers.sh crashed: {proc}")
    return proc.returncode == 0, proc.stdout


def one_run(text, proto):
    """The cell cut to one run, as the gate runs it (--runs=1): the proto's first seed."""
    seeds = [m.group(1) for m in map(bp.RUN.search, text.splitlines()) if m and m.group(2) == proto]
    keep = [ln for ln in text.splitlines() if not bp.RUN.search(ln)]
    keep += [ln for ln in text.splitlines() if (m := bp.RUN.search(ln)) and m.group(1) == seeds[0]]
    return "\n".join(keep) + "\n"


# The awk expressions the gates used before #791, verbatim.
AWK_RUN_PDR = '$1 == "##RUN##" && $3 == p { v = $4 } END { print v }'
AWK_TABLE_PDR = "$1 == p && $2 ~ /^[0-9.]+$/ { v = $2 } END { print v }"
AWK_PATHS_HOPS = (
    '$1 == "#" && $2 == "paths" && $3 == p { for (i = 4; i <= NF; i++) '
    'if ($i ~ /^hopsMean=/) { sub(/^hopsMean=/, "", $i); v = $i } } '
    "END { print v }"
)


def test_equivalence_on_committed_cells():
    cells = sorted(glob.glob(os.path.join(CELLS, "*.txt")))
    check(len(cells) >= MIN_CELLS, f"expected >= {MIN_CELLS} cells in {CELLS}, found {len(cells)}")
    n_run = n_table = n_paths = 0
    for path in cells:
        with open(path) as fh:
            text = fh.read()
        protos = set(bp.parse_runs(text)) | set(bp.parse_cell(text))
        for p in sorted(protos):
            want = awk(AWK_RUN_PDR, text, p=p)
            got = bp.query(text, p, "pdr", "run")
            check(got == want, f"{path} {p} ##RUN## pdr: query {got} != awk {want}")
            n_run += bool(want)
            want = awk(AWK_TABLE_PDR, text, p=p)
            got = bp.query(text, p, "pdr", "table")
            check(got == want, f"{path} {p} table pdr: query {got} != awk {want}")
            n_table += bool(want)
            want = awk(AWK_PATHS_HOPS, text, p=p)
            got = bp.query(text, p, "hopsMean", "paths")
            check(got == want, f"{path} {p} paths hopsMean: query {got} != awk {want}")
            n_paths += bool(want)
    # The comparison must have compared something in each source.
    check(
        n_run > MIN_CMP[0] and n_table > MIN_CMP[1] and n_paths > MIN_CMP[2],
        f"too few non-empty comparisons: run={n_run} table={n_table} paths={n_paths}",
    )
    print(
        f"ok: query == awk on {len(cells)} committed cells "
        f"({n_run} run, {n_table} table, {n_paths} paths values)"
    )


def test_stock_arms_verdict():
    """check-stock-arms.sh's delivery verdict, before (awk) and after (the reader).

    On every cell cut to one run: the reader's pdr > 0 is the old verdict for
    every protocol; on the 13 fixtures the gate's own helper is run as well.
    Then broken inputs, which must still fail, and a changed row the old awk
    would misread."""
    cells = sorted(glob.glob(os.path.join(CELLS, "*.txt")))
    fixtures = os.path.join(TESTS, "fixtures", "cells")
    n = n_pass = n_script = 0
    for path in cells:
        with open(path) as fh:
            text = fh.read()
        for p in sorted(bp.parse_runs(text)):
            one = one_run(text, p)
            want = old_stock_verdict(one, p)
            pdr = bp.query(one, p, "pdr", "run")
            got = bool(pdr) and float(pdr[0]) > 0
            check(got == want, f"{path} {p}: reader verdict {got} != awk verdict {want}")
            if path.startswith(fixtures):
                got, rows = delivers(one, p)
                check(got == want, f"{path} {p}: arm-delivers.sh {got} != awk {want}")
                check(rows == "\n".join(bp.query(one, p, "row", "run")) + "\n", rows)
                n_script += 1
            n += 1
            n_pass += want
    check(n > MIN_CMP[0] and n_pass > 0, f"verdicts: {n_pass}/{n} deliver")
    if not os.environ.get("NS3_TOOL_CELLS"):
        check(n_script == n, "every fixture verdict ran through the script")
    # Broken inputs still fail: no delivery, no row, only another arm's row.
    run = "##RUN## 1 olsr {} 10.0 20.0 30.0 1.00\n"
    noise = "  [diag olsr seed=1] flow 10.1.0.1:49153 -> 10.1.0.2:9 tx=10 rx=0\n"
    for text, verdict in (
        (run.format("98.5"), True),
        (noise + run.format("0.0"), False),
        (noise, False),
        (run.format("98.5").replace("olsr", "dsdv"), False),
    ):
        got, _ = delivers(text, "olsr")
        check(got == verdict == old_stock_verdict(text, "olsr"), f"{text!r}: {got}")
    # A prefix the reader absorbs (a job log's timestamp) moves the awk's
    # $1/$3/$4; the reader still finds the row and its pdr.
    moved = "2026-11-10T03:00:00.0Z ##RUN## 1 olsr 98.5 10.0 20.0 30.0 1.00\n"
    check(delivers(moved, "olsr")[0], "reader reads a timestamped ##RUN## row")
    check(
        not old_stock_verdict(moved, "olsr"),
        "the positional read misses it (else this proves nothing)",
    )
    print(
        f"ok: stock-arms verdict, reader == awk on {n} one-run cells ({n_pass} deliver, {n_script} by the script)"
    )


def test_run_rows():
    text = (
        "##RUN## 1 oracle 99.0 10.0 20.0 30.0 1.00 2.0 inf inf 0.1\n"
        "##RUN## 1 opspf 99.0 10.0 20.0 30.0 1.00 2.0 inf inf 0.1\n"
        "##RUN## 2 oracle 98.0 11.0 21.0 31.0 1.10 2.1 inf inf 0.2\n"
        "##RUN## 2 opspf 97.0 11.0 21.0 31.0 1.10 2.1 inf inf 0.2\n"
    )
    o = bp.query(text, "oracle", "row", "run")
    s = bp.query(text, "opspf", "row", "run")
    check(
        o
        == [
            "1 99.0 10.0 20.0 30.0 1.00 2.0 inf inf 0.1",
            "2 98.0 11.0 21.0 31.0 1.10 2.1 inf inf 0.2",
        ],
        f"oracle rows {o}",
    )
    check(o[0] == s[0] and o[1] != s[1], "row comparison must see seed 2 differ")
    check(bp.query(text, "oracle", "delay99", "run") == ["21.0"], "last row wins")
    print("ok: --get row prints each ##RUN## row without the protocol")


# Header lines as the harnesses print them (routing-bench-harness.cc, isl-grid.cc).
ROUTING_CSV = (
    "protocol,runs,nNodes,area,speed,flows,pdr_pct,delay_ms,throughput_kbps,"
    "delay99_ms,nrl,jitter_ms\n"
    "anthocnet,2,50,1500x300,20.0,10,91.3,45.2,120.4,300.1,3.21,12.0\n"
    "aodv,2,50,1500x300,20.0,10,88.0,30.1,118.0,250.0,4.10,9.5\n"
)
ISL_CSV = (
    "protocol,runs,rows,cols,nodes,links,isl_delay_ms,flows,pdr_pct,delay_ms,"
    "delay99_ms,throughput_kbps,nrl,nrl_bytes,jitter_ms\n"
    "dsdv,1,4,4,16,32,2.0,4,100.0,8.1,9.0,40.0,0.5,0.01,0.1\n"
)


def test_csv_by_name():
    check(bp.query(ROUTING_CSV, "aodv", "pdr", "csv") == ["88.0"], "routing csv pdr")
    check(
        awk('BEGIN { FS = "," } $1 == a { print $7 }', ROUTING_CSV, a="aodv") == ["88.0"],
        "fixture must match the gates' old $7",
    )
    check(bp.query(ISL_CSV, "dsdv", "pdr", "csv") == ["100.0"], "isl csv pdr")
    check(bp.query(ISL_CSV, "dsdv", "delay", "csv") == ["8.1"], "isl csv delay")
    check(bp.query(ISL_CSV, "dsdv", "pdr_pct", "csv") == ["100.0"], "raw column name")
    # A reordered header: named lookup still finds PDR; the old $7 would not.
    cols = ROUTING_CSV.splitlines()[0].split(",")
    order = [0, 6] + [i for i in range(1, len(cols)) if i != 6]
    moved = (
        "\n".join(",".join(line.split(",")[i] for i in order) for line in ROUTING_CSV.splitlines())
        + "\n"
    )
    check(bp.query(moved, "aodv", "pdr", "csv") == ["88.0"], "reordered header")
    check(
        awk('BEGIN { FS = "," } $1 == a { print $7 }', moved, a="aodv") != ["88.0"],
        "the positional read must break on the reordered header (else this proves nothing)",
    )
    print("ok: --from csv reads columns by header name, aliases included")


# manet-baselines' table prints four numbers per arm (no throughput column):
# the #829 CI failure. The single-hop anchor reads its PDR.
BASELINES_TABLE = (
    "protocol        PDR%  delay(ms)  delay99(ms)     NRL\n"
    "--------------------------------------------------------\n"
    "  [diag aodv seed=1] flow 10.1.0.1:49153 -> 10.1.0.2:9 tx=862 rx=862 lost=0\n"
    "aodv            100.0        0.8          0.5     1.14\n"
    "dsdv            100.0        0.8          0.5     1.02\n"
)


def test_four_column_table():
    for p in ("aodv", "dsdv"):
        want = awk(AWK_TABLE_PDR, BASELINES_TABLE, p=p)
        got = bp.query(BASELINES_TABLE, p, "pdr", "table")
        check(got == want == ["100.0"], f"manet-baselines {p}: query {got}, awk {want}")
    print("ok: --from table reads PDR from manet-baselines' four-number rows")


def test_last_empty_clears():
    # awk's `{ v = $n } END { print v }` takes the LAST matching line even when
    # its field is empty; a stale earlier value must not leak through.
    csv = ROUTING_CSV + "aodv,2,50,1500x300,20.0,10,,,,,,\n"
    check(bp.query(csv, "aodv", "pdr", "csv") == [], "csv: empty last row clears")
    paths = "# paths x hopsMean=2.0\n# paths x hopsMean=\n"
    check(bp.query(paths, "x", "hopsMean", "paths") == [], "paths: empty last value clears")
    check(awk(AWK_PATHS_HOPS, paths, p="x") == [], "awk agrees on paths")
    run = "##RUN## 1 x 50.0 1 2 3 4\n##RUN## 2 x 49.0\n"
    check(bp.query(run, "x", "delay99", "run") == [], "run: short last row clears")
    check(bp.query(run, "x", "pdr", "run") == ["49.0"], "run: present field still read")
    print("ok: an empty or missing field in the last matching row clears the value")


def test_missing_is_empty_exit_0():
    for src, field in (("run", "pdr"), ("table", "pdr"), ("csv", "pdr"), ("paths", "hopsMean")):
        check(bp.query(ROUTING_CSV, "nosuch", field, src) == [], f"{src} missing proto")
    proc = subprocess.run(
        [
            sys.executable,
            os.path.join(HERE, "bench_parse.py"),
            "--get",
            "pdr",
            "--from",
            "csv",
            "--proto",
            "nosuch",
            "-",
        ],
        input=ROUTING_CSV,
        capture_output=True,
        text=True,
        check=False,
    )
    check(proc.returncode == 0 and proc.stdout == "", f"CLI missing: {proc}")
    proc = subprocess.run(
        [
            sys.executable,
            os.path.join(HERE, "bench_parse.py"),
            "--get",
            "pdr",
            "--from",
            "csv",
            "--proto",
            "aodv",
            "-",
        ],
        input=ROUTING_CSV,
        capture_output=True,
        text=True,
        check=False,
    )
    check(proc.returncode == 0 and proc.stdout == "88.0\n", f"CLI value: {proc}")
    print("ok: a missing value prints nothing and exits 0; the CLI prints the raw token")


if __name__ == "__main__":
    test_equivalence_on_committed_cells()
    test_stock_arms_verdict()
    test_run_rows()
    test_csv_by_name()
    test_four_column_table()
    test_last_empty_clears()
    test_missing_is_empty_exit_0()
    print("all bench_parse --get tests passed")
