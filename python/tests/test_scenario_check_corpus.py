#!/usr/bin/env python3
# Copyright (c) 2026 Daniel Joppi (https://github.com/swarm-routing)
# SPDX-License-Identifier: MIT OR GPL-2.0-only
"""The port's verdicts against AntHocNet's scenario_check.py (ns3-tools#28).

`results` on every cell must print what AntHocNet's script prints, line for
line, once the lines of the rules that stayed in AntHocNet are taken out of
its output (the ##REINJ## books, #386/#402, and the opspf rule, #707) and its
verdict line is recounted without them. The port runs with AntHocNet's arm
roles (ROLES), which its script hard-coded.

- Offline (CI's self-test step): the 13 fixture cells against
  fixtures/scenario_check/<cell>.out, AntHocNet's output recorded once and
  filtered the same way.
- With NS3_TOOL_CELLS=<AntHocNet>/results/cells and
  NS3_TOOL_SCENARIO_CHECK=<AntHocNet>/tools/bench/scenario_check.py (CI's
  corpus job): every cell of the corpus, against the script itself.
  `--record` rewrites the fixture outputs from NS3_TOOL_SCENARIO_CHECK.

Run: python3 test_scenario_check_corpus.py   (exits non-zero on the first
                                              difference)
"""

import glob
import os
import re
import subprocess
import sys

TESTS = os.path.dirname(os.path.abspath(__file__))
MODULE = os.path.join(os.path.dirname(TESTS), "ns3_tool", "scenario_check.py")
FIXTURE_CELLS = os.path.join(TESTS, "fixtures", "cells")
EXPECTED = os.path.join(TESTS, "fixtures", "scenario_check")
ROLES = ["--single-path", "aodv,olsr,dsdv", "--multipath", "anthocnet", "--oracle", "oracle"] + [
    x for a in ("ara", "termite", "qrouting", "beeadhoc") for x in ("--hold-ceiling", f"{a}=35000")
]
# The report lines of the rules that stayed in AntHocNet.
ANTHOCNET_ONLY = re.compile(r"#386|#402|\(#707\)")


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout + r.stderr, r.returncode


def filtered(text):
    """AntHocNet's output without its own rules' lines, verdict recounted."""
    lines = [ln for ln in text.splitlines() if not (ln.startswith(("FAIL: ", "WARN: ")) and ANTHOCNET_ONLY.search(ln))]
    fails = sum(ln.startswith("FAIL: ") for ln in lines)
    warns = sum(ln.startswith("WARN: ") for ln in lines)
    word = "FAIL" if fails else "WARN" if warns else "OK"
    lines = [f"verdict: {word} ({fails} fail, {warns} warn)" if ln.startswith("verdict: ") else ln for ln in lines]
    return "\n".join(lines) + "\n", 1 if fails else 0


def ours(cell):
    out, code = run([sys.executable, "-I", MODULE, "results", cell, *ROLES])
    return out, code


def fail(name, want, got):
    import difflib

    diff = "".join(difflib.unified_diff(want.splitlines(True), got.splitlines(True), "AntHocNet", "ns3-tools"))
    print(f"FAIL {name}: the port's output differs\n{diff}")
    sys.exit(1)


def main():
    reference = os.environ.get("NS3_TOOL_SCENARIO_CHECK")
    corpus = os.environ.get("NS3_TOOL_CELLS")
    if "--record" in sys.argv:
        if not reference:
            sys.exit("--record needs NS3_TOOL_SCENARIO_CHECK")
        os.makedirs(EXPECTED, exist_ok=True)
        for cell in sorted(glob.glob(os.path.join(FIXTURE_CELLS, "*"))):
            text, code = filtered(run([sys.executable, "-I", reference, "results", cell])[0])
            with open(os.path.join(EXPECTED, os.path.basename(cell) + ".out"), "w") as fh:
                fh.write(f"# exit {code}\n" + text)
        print(f"recorded {EXPECTED}")
        return
    n = 0
    for cell in sorted(glob.glob(os.path.join(FIXTURE_CELLS, "*"))):
        with open(os.path.join(EXPECTED, os.path.basename(cell) + ".out")) as fh:
            head, want = fh.read().split("\n", 1)
        got, code = ours(cell)
        if got != want or f"# exit {code}" != head:
            fail(os.path.basename(cell), head + "\n" + want, f"# exit {code}\n" + got)
        n += 1
    print(f"ok   {n} fixture cells match AntHocNet's recorded output")
    if not (corpus and reference):
        print("skip the full corpus (set NS3_TOOL_CELLS and NS3_TOOL_SCENARIO_CHECK)")
        return
    cells = sorted(p for p in glob.glob(os.path.join(corpus, "*")) if os.path.isfile(p))
    if len(cells) < 100:
        sys.exit(f"FAIL: only {len(cells)} cells under {corpus}")
    for cell in cells:
        want, want_code = filtered(run([sys.executable, "-I", reference, "results", cell])[0])
        got, code = ours(cell)
        if got != want or code != want_code:
            fail(os.path.basename(cell), f"# exit {want_code}\n" + want, f"# exit {code}\n" + got)
    print(f"ok   {len(cells)} corpus cells match AntHocNet's scenario_check.py")


if __name__ == "__main__":
    main()
