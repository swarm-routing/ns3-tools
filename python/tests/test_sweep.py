#!/usr/bin/env python3
# Copyright (c) 2026 Daniel Joppi (https://github.com/swarm-routing)
# SPDX-License-Identifier: MIT OR GPL-2.0-only
"""Self-test for sweep.run_compare (#192): the harness runs without a shell.

A fake `ns3` in a directory whose name holds a space and a quote records its
argv and prints the harness CSV. Arguments carrying $(...), backticks and $VAR
must reach it verbatim, as the one string `./ns3 run` word-splits itself, and
must execute nothing.

Run: python3 test_sweep.py   (no pytest; exits non-zero on the first failure)
"""

import contextlib
import io
import json
import os
import stat
import sys
import tempfile

TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(TESTS), "ns3_tool"))
import sweep  # noqa: E402 (after the sys.path insert)

FAKE_NS3 = """#!/usr/bin/env python3
import json, os, sys
with open("argv.json", "w") as f:
    json.dump({"argv": sys.argv[1:], "cwd": os.getcwd()}, f)
print("protocol,pdr")
print("aodv,0.9")
print("##RUN## 1 aodv 0.9")
"""


def check(cond, msg):
    if not cond:
        print("FAIL:", msg)
        sys.exit(1)


def test_no_shell():
    with tempfile.TemporaryDirectory() as tmp:
        ns3dir = os.path.join(tmp, 'ns 3 "tree"')
        os.mkdir(ns3dir)
        fake = os.path.join(ns3dir, "ns3")
        with open(fake, "w") as f:
            f.write(FAKE_NS3)
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IXUSR)
        pwned = [os.path.join(tmp, n) for n in ("a", "b", "c")]
        arg_str = (
            f"--csv --x=$(touch {pwned[0]}) --y=`touch {pwned[1]}` --z=$HOME; touch {pwned[2]}"
        )
        with contextlib.redirect_stderr(io.StringIO()):
            rows, run_rows = sweep.run_compare(ns3dir, arg_str, False, label="t", protocols="aodv")
        check(not any(os.path.exists(p) for p in pwned), "an argument was executed by a shell")
        with open(os.path.join(ns3dir, "argv.json")) as f:
            got = json.load(f)
        check(got["argv"] == ["run", f"{sweep.PROGRAM} {arg_str}"], f"argv: {got['argv']}")
        check(os.path.realpath(got["cwd"]) == os.path.realpath(ns3dir), f"cwd: {got['cwd']}")
        check(rows == [{"protocol": "aodv", "pdr": "0.9"}], f"rows: {rows}")
        check(run_rows and run_rows[0]["protocol"] == "aodv", f"run rows: {run_rows}")
    print("ok   run_compare: no shell; $(...), backticks, $VAR and ; reach ./ns3 run verbatim")


def test_dry_run_text():
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        sweep.run_compare("/opt/ns-3", "--csv --runs=1", True)
    want = f'[dry-run] cd "/opt/ns-3" && ./ns3 run "{sweep.PROGRAM} --csv --runs=1"\n'
    check(err.getvalue() == want, f"dry-run text: {err.getvalue()!r}")
    print("ok   run_compare --dry-run: the run-scenarios.py text, unchanged")


if __name__ == "__main__":
    test_no_shell()
    test_dry_run_text()
