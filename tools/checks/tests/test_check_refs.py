#!/usr/bin/env python3
# Copyright (c) 2026 Daniel Joppi (https://github.com/swarm-routing)
# SPDX-License-Identifier: MIT OR GPL-2.0-only
"""check-refs.py's self-test as a test script, so the coverage run that
SonarQube Cloud imports (ns3.yml, python-quality) measures tools/checks/.
Exits non-zero on the first failure, like python/tests/."""

import contextlib
import importlib.util
import io
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check-refs.py"
spec = importlib.util.spec_from_file_location("check_refs", SCRIPT)
check_refs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_refs)

fails = 0
# The CLI without arguments prints its usage and exits 2.
with contextlib.redirect_stderr(io.StringIO()):
    if check_refs.main([]) != 2:
        print("FAIL: no arguments should exit 2")
        fails += 1
# A ``` line inside a ~~~ fence does not close it: the #1 stays skipped.
if check_refs.check_text("~~~\n```\n#1\n~~~\nok\n"):
    print("FAIL: a different fence marker closed the block")
    fails += 1
# The self-test itself, through the CLI as CI runs it.
fails += check_refs.main(["--self-test"])
sys.exit(1 if fails else 0)
