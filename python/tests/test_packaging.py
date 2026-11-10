#!/usr/bin/env python3
# Copyright (c) 2026 Daniel Joppi (https://github.com/swarm-routing)
# SPDX-License-Identifier: MIT OR GPL-2.0-only
"""Self-test: the distribution ships both licence texts (#14).

pyproject.toml declares MIT OR GPL-2.0-only, so the sdist and the wheel must
carry LICENSE (GPL-2.0) and LICENSE-MIT. The texts live at the repository
root, outside the python/ build context; python/LICENSE and
python/LICENSE-MIT are symlinks to them (one copy in git), which setuptools
copies into the sdist and the wheel's .dist-info/licenses/ as plain files.
This checks the links resolve to the root texts and pyproject lists both.

Run: python3 test_packaging.py   (no pytest; exits non-zero on the first
                                  failure)
"""

import os
import re
import sys

PY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(PY)
TEXTS = ["LICENSE", "LICENSE-MIT"]


def main():
    for name in TEXTS:
        link = os.path.join(PY, name)
        assert os.path.islink(link), f"python/{name} should be a symlink to ../{name}"
        assert os.readlink(link) == f"../{name}", os.readlink(link)
        with open(link, "rb") as a, open(os.path.join(ROOT, name), "rb") as b:
            assert a.read() == b.read(), name
    with open(os.path.join(PY, "pyproject.toml")) as f:
        m = re.search(r"^license-files = \[(.*)\]$", f.read(), re.M)
    assert m, "pyproject.toml has no license-files"
    assert re.findall(r'"([^"]+)"', m.group(1)) == TEXTS, m.group(0)
    print("packaging self-test: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
