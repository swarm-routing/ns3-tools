#!/usr/bin/env python3
# Copyright (c) 2026 Daniel Joppi (https://github.com/swarm-routing)
# SPDX-License-Identifier: MIT OR GPL-2.0-only
"""Every issue, PR and MR reference in a markdown file is fully qualified.

The ns-3 bug register and its RCAPAs feed upstream reports: on GitLab a bare
`#N` is an ns-3 issue and `!N` an ns-3 merge request, and on GitHub a bare
`#N` is an item of the repository the text is in. So a reference is written
(maintainer rule 2026-11-16, ns3-tools#247):

- an org item as `swarm-routing/<repo>#N` (this repository's own included),
  bare or as a link's text;
- an ns-3 item as a link whose text is `nsnam/ns-3-dev#N` (issue) or
  `nsnam/ns-3-dev!N` (merge request) and whose target is the full
  gitlab.com URL (`/-/issues/N` or `/-/work_items/N`; `/-/merge_requests/N`).

Fenced code blocks (mermaid included) are skipped; inline code is checked,
since it is read. Link targets and URLs are not references. A link whose text
is a qualified reference must point at that same item when its target is a
GitHub or GitLab item URL.

    check-refs.py FILE_OR_DIR...   # a directory: its *.md files
    check-refs.py --self-test

Prints file:line:col and a hint per finding; exits 1 if there is any.
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
import tempfile
from pathlib import Path

ORG = "swarm-routing"
NS3 = "nsnam/ns-3-dev"

FENCE = re.compile(r"^\s{0,3}(```|~~~)")
# [text](target): text without nested brackets, target up to the closing paren.
LINK = re.compile(r"\[([^\[\]]*)\]\(([^()\s]*(?:\([^()\s]*\)[^()\s]*)*)(?:\s+\"[^\"]*\")?\)")
URL = re.compile(r"<?https?://[^\s<>)\]]+>?")
# Anything that looks like a reference: optional owner/repo or repo, then # or !, then digits.
REF = re.compile(r"(?<![\w&#!.-])((?:[\w.-]+/)?[\w.-]+)?([#!])(\d+)\b")
GH_URL = re.compile(
    r"^https://github\.com/([\w.-]+/[\w.-]+)/(?:issues|pull|discussions)/(\d+)(?:[#?].*)?$"
)
GL_URL = re.compile(
    r"^https://gitlab\.com/nsnam/ns-3-dev/-/(issues|work_items|merge_requests)/(\d+)(?:[#?/].*)?$"
)
QUALIFIED_TEXT = re.compile(r"^(swarm-routing/[\w.-]+#\d+|nsnam/ns-3-dev[#!]\d+)$")


def blank(s: str, a: int, b: int) -> str:
    return s[:a] + " " * (b - a) + s[b:]


def check_line(line: str) -> list[tuple[int, str]]:
    """Findings (column, message) for one line outside code fences."""
    out: list[tuple[int, str]] = []
    scan = line
    ns3_ok: set[int] = set()  # columns of nsnam/ns-3-dev refs that are proper links
    for m in LINK.finditer(line):
        text, target = m.group(1).strip().strip("`"), m.group(2)
        # the target is never a reference
        scan = blank(scan, m.start(2), m.end(2))
        if not QUALIFIED_TEXT.match(text):
            continue
        sep = "!" if "!" in text else "#"
        repo, num = text.split(sep)
        gh, gl = GH_URL.match(target), GL_URL.match(target)
        if repo == NS3:
            want = "merge_requests" if sep == "!" else ("issues", "work_items")
            if (
                not gl
                or gl.group(2) != num
                or (gl.group(1) not in want if sep == "#" else gl.group(1) != want)
            ):
                out.append(
                    (
                        m.start() + 1,
                        f"link text {text} must point at https://gitlab.com/{NS3}/-/{'merge_requests' if sep == '!' else 'issues'}/{num}",
                    )
                )
            ns3_ok.add(m.start(1) + m.group(1).index(text))
        elif gh and (gh.group(1).lower() != repo.lower() or gh.group(2) != num):
            out.append((m.start() + 1, f"link text {text} points at {gh.group(1)}#{gh.group(2)}"))
    for m in URL.finditer(scan):
        scan = blank(scan, m.start(), m.end())
    for m in REF.finditer(scan):
        prefix, sep, num = m.group(1) or "", m.group(2), m.group(3)
        col = m.start() + 1
        whole = m.group(0)
        if prefix == NS3:
            if m.start() not in ns3_ok:
                kind = "merge_requests" if sep == "!" else "issues"
                out.append(
                    (
                        col,
                        f"{whole}: an ns-3 reference is a link, [{whole}](https://gitlab.com/{NS3}/-/{kind}/{num})",
                    )
                )
            continue
        if sep == "#" and prefix.startswith(f"{ORG}/") and prefix.count("/") == 1:
            continue
        if not prefix:
            hint = (
                f"bare {whole}: write swarm-routing/<repo>{whole}, or [{NS3}{whole}](https://gitlab.com/{NS3}/-/"
                f"{'merge_requests' if sep == '!' else 'issues'}/{num}) for ns-3"
            )
        elif sep == "!":
            hint = f"{whole}: only ns-3 merge requests use '!', as [{NS3}!{num}](https://gitlab.com/{NS3}/-/merge_requests/{num})"
        elif prefix in ("ns-3-dev", "nsnam", "ns-3", "ns3"):
            hint = f"{whole}: write [{NS3}#{num}](https://gitlab.com/{NS3}/-/issues/{num})"
        elif "/" in prefix:
            hint = f"{whole}: only {ORG}/<repo>#N and {NS3}#N|!N are allowed"
        else:
            hint = f"{whole}: write {ORG}/{prefix}#{num}"
        out.append((col, hint))
    return out


def check_text(text: str) -> list[tuple[int, int, str]]:
    found: list[tuple[int, int, str]] = []
    fence: str | None = None
    for n, line in enumerate(text.splitlines(), 1):
        f = FENCE.match(line)
        if f:
            if fence is None:
                fence = f.group(1)
                continue
            if f.group(1) == fence:
                fence = None
                continue
        if fence is not None:
            continue
        # an HTML comment line (the template's notes) is read too: no skip
        found.extend((n, c, msg) for c, msg in check_line(line))
    return found


def files_of(args: list[str]) -> list[Path]:
    out: list[Path] = []
    for a in args:
        p = Path(a)
        out.extend(sorted(p.glob("*.md")) if p.is_dir() else [p])
    return out


def main(argv: list[str]) -> int:
    if argv == ["--self-test"]:
        return self_test()
    if not argv:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    bad = 0
    files = files_of(argv)
    for path in files:
        for n, c, msg in check_text(path.read_text(encoding="utf-8")):
            print(f"{path}:{n}:{c}: {msg}")
            bad += 1
    print(f"check-refs: {len(files)} file(s), {bad} unqualified reference(s)", file=sys.stderr)
    return 1 if bad else 0


GOOD = """\
See swarm-routing/AntHocNet#843 and [swarm-routing/ns3-tools#240](https://github.com/swarm-routing/ns3-tools/issues/240).
Upstream: [nsnam/ns-3-dev#1364](https://gitlab.com/nsnam/ns-3-dev/-/issues/1364),
[nsnam/ns-3-dev#1290](https://gitlab.com/nsnam/ns-3-dev/-/work_items/1290) and
[nsnam/ns-3-dev!2985](https://gitlab.com/nsnam/ns-3-dev/-/merge_requests/2985).
A link with an anchor [the register](../ns3-bugs.md#adding-a-bug), a URL
https://example.org/page#123, an entity &#123; and an image ![x](y.png).
`swarm-routing/ns3-containers#35` in inline code; ##RUN## markers; a heading below.
## 1. Heading
```mermaid
A->>B: see #843 and !12
```
"""

BAD = {
    "bare #843 here": "bare #843",
    "MR !2985 merged": "bare !2985",
    "in AntHocNet#434": "write swarm-routing/AntHocNet#434",
    "`ns3-containers#35` in code": "write swarm-routing/ns3-containers#35",
    "[#240](https://github.com/swarm-routing/ns3-tools/issues/240)": "bare #240",
    "nsnam/ns-3-dev#1364 bare": "is a link",
    "ns-3-dev#503": "write [nsnam/ns-3-dev#503]",
    "[nsnam/ns-3-dev!3011](https://gitlab.com/nsnam/ns-3-dev/-/issues/3011)": "must point at",
    "[swarm-routing/ns3-tools#2](https://github.com/swarm-routing/ns3-tools/issues/3)": "points at",
    "other/repo#4": "only swarm-routing",
    "deep/path/repo#6": "only swarm-routing",
    "swarm-routing/ns3-tools!5": "only ns-3 merge requests",
}


def self_test() -> int:
    fails = 0
    got = check_text(GOOD)
    if got:
        print(f"FAIL: clean fixture flagged: {got}")
        fails += 1
    for text, want in BAD.items():
        got = check_text(text + "\n")
        if len(got) != 1 or want not in got[0][2]:
            print(f"FAIL: {text!r}: want one finding containing {want!r}, got {got}")
            fails += 1
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "a.md").write_text(GOOD, encoding="utf-8")
        (Path(d) / "b.md").write_text("line\nsee #7\n", encoding="utf-8")
        (Path(d) / "c.txt").write_text("#9\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            codes = (main([str(Path(d) / "a.md")]), main([d]))
        if codes != (0, 1):
            print("FAIL: exit codes on files and a directory")
            fails += 1
        if len(check_text("swarm-routing/ns3-tools#211/#212\n")) != 1:
            print("FAIL: a ref after a slash")
            fails += 1
        if [(n, c) for n, c, _ in check_text((Path(d) / "b.md").read_text())] != [(2, 5)]:
            print("FAIL: line:col of a finding")
            fails += 1
    print("check-refs self-test:", "FAIL" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
