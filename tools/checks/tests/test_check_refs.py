#!/usr/bin/env python3
# Copyright (c) 2026 Daniel Joppi (https://github.com/swarm-routing)
# SPDX-License-Identifier: MIT OR GPL-2.0-only
"""check-refs.py's self-test as a test script, so the coverage run that
SonarQube Cloud imports (ns3.yml, python-quality) measures tools/checks/.
Exits non-zero on any failure, like python/tests/."""

import contextlib
import importlib.util
import io
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check-refs.py"
spec = importlib.util.spec_from_file_location("check_refs", SCRIPT)
check_refs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_refs)

with contextlib.redirect_stderr(io.StringIO()):
    usage_exit = check_refs.main([])
problems = [
    name
    for name, ok in (
        # The CLI without arguments prints its usage and exits 2.
        ("no arguments should exit 2", usage_exit == 2),
        # A ``` line inside a ~~~ fence does not close it: the #1 stays skipped.
        (
            "a different fence marker closed the block",
            not check_refs.check_text("~~~\n```\n#1\n~~~\nok\n"),
        ),
    )
    if not ok
]
# Then the self-test itself, through the CLI as CI runs it.
sys.exit("FAIL: " + "; ".join(problems) if problems else check_refs.main(["--self-test"]))
