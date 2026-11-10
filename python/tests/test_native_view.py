#!/usr/bin/env python3
# Copyright (c) 2026 Daniel Joppi (https://github.com/swarm-routing)
# SPDX-License-Identifier: MIT OR GPL-2.0-only
"""Self-test for native_view.py: AntHocNet minus the oracle and minus the
best native baseline, paired by seed; and a cell whose protocols share fewer
than 2 seeds (a truncated or timed-out run, #14) reads "—" and
"n/a (<2 common seeds)" instead of raising.

Run: python3 test_native_view.py   (no pytest; exits non-zero on the first
                                    failure)
"""

import csv
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(os.path.dirname(HERE), "ns3_tool", "native_view.py")
sys.path.insert(0, os.path.dirname(TOOL))
import native_view as nv  # noqa: E402 (after the sys.path insert)


def rows(proto, pdrs, first=1):
    return "".join(
        f"##RUN## {s} {proto} {p} 10.0 100.0 13.0 4.0\n" for s, p in enumerate(pdrs, first)
    )


def main():
    with tempfile.TemporaryDirectory() as d:
        for _, cells, natives in nv.FAMILIES:
            for cell in cells:
                if cell.startswith(("leo-", "sagin-")):
                    continue  # one file per protocol; absent files are skipped
                if cell == "static-mesh":
                    # AntHocNet on seeds 1-3, the oracle and olsr on 7-9.
                    text = rows("anthocnet", [90, 91, 92]) + rows("oracle", [99, 99, 99], 7)
                    text += rows("olsr", [80, 80, 80], 7)
                else:
                    text = rows("anthocnet", [90, 91, 92]) + rows("oracle", [99, 100, 101])
                    text += "".join(rows(p, [80, 81, 82]) for p in natives)
                with open(os.path.join(d, f"{cell}-t.txt"), "w") as f:
                    f.write(text)
        out_csv = os.path.join(d, "out.csv")
        run = subprocess.run(
            [sys.executable, TOOL, "--gen", "t", "--dir", d, "--csv", out_csv],
            capture_output=True,
            text=True,
        )
        assert run.returncode == 0, run.stderr
        with open(out_csv) as f:
            by_cell = {r["cell"]: r for r in csv.DictReader(f)}
    lines = {ln.split("|")[2].strip(): ln for ln in run.stdout.splitlines()[2:]}
    full = lines["fanet-main"]
    assert "| -9.00 [-9.00, -9.00] | aodv | 81.00 | +10.00 [+10.00, +10.00]" in full, full
    mesh = lines["static-mesh"]
    assert mesh == (
        "| static mesh | static-mesh | 91.00 | 99.00 | — | olsr | 80.00 "
        "| — | — | — | n/a (<2 common seeds) |"
    ), mesh
    assert by_cell["static-mesh"]["gap"] == "", by_cell["static-mesh"]
    assert by_cell["fanet-main"]["gap"] == "-9.0000", by_cell["fanet-main"]
    print("native_view self-test: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
