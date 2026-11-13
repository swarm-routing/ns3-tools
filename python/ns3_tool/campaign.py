#!/usr/bin/env python3
# Copyright (c) 2026 Daniel Joppi (https://github.com/swarm-routing)
# SPDX-License-Identifier: MIT OR GPL-2.0-only
"""Multi-protocol benchmark campaigns on one campaign image (#66).

A campaign pairs several repositories' ns-3 modules in one arena: the
protocols under test, swarm-core, this repo's routing-bench and the stock or
vendored arms. Option B of swarm-routing/.github#107: the modules are built
once into a campaign image FROM a pinned ns3-dev digest, and every cell pulls
that image by digest, so all cells run the same binary and none rebuilds a
protocol.

    ns3-campaign validate PLAN                  # the plan checker (also run by every command)
    ns3-campaign plan     PLAN                  # the image and every cell's command; dispatches nothing
    ns3-campaign build    PLAN [--ref main]     # the campaign image (campaign-image.yml), waits for it
    ns3-campaign dispatch PLAN [--cap N] [--wait]   # every cell in that image by digest, throttled
    ns3-campaign status   PLAN [--brief]
    ns3-campaign collect  PLAN --out RUNS_ROOT  # runs/<date>-<name>/: cells/, plan.toml, manifest.json
    ns3-campaign verdicts PLAN --dir RUN_DIR    # each arm minus the reference, paired per seed
    ns3-campaign image-inputs PLAN | stage PLAN # used by campaign-image.yml

A change-test plan (AntHocNet's format, with a `generation` key; .yaml, .yml
or .json, or a .toml that has the key) is routed to change_plan.py instead:
plan, dispatch, status, collect and verdicts of cells x arms in a protocol
repository, each cell through its caller of swarm-routing/.github's
reusable-benchmark-cell.yml, pinned to the plan's source_sha and image (#81).

State (the image, the dispatch date, the label -> run id log, downloaded
artifacts) lives in --work, default .campaign/<name>/, so every command is
resumable: `dispatch` dispatches until the cap and exits, and is re-run until
every label is logged (as AntHocNet's tools/bench/campaign.py, whose shape this
ports: throttling, the run log, collect from artifacts).

Plan (TOML, committed under campaigns/ before the campaign runs: it is the
pre-registration, copied into the run directory):

    name = "anthocnet-aodv-smoke"         # the run directory is <date>-<name>
    base = "3.48-optimized"               # ns3-dev <version>-optimized, or latest-optimized
    program = "routing-compare"           # default; the harness program
    harness = "routing-bench"             # default; the module that holds it
    arms = ["anthocnet", "aodv"]          # --protocols, every cell, same seeds
    reference = "aodv"                    # verdicts: every other arm minus this one
    seeds = 20                            # >= 20 (org rule 6): RngRun first_seed..
    first_seed = 1                        # default 1
    cap = 12                              # default 12: cell runs queued or running at once
    modules = [                           # name=owner/repo@<40-char commit>[:subdir];
      "anthocnet=swarm-routing/AntHocNet@<commit>",          # the first names the image
      "routing-bench=swarm-routing/ns3-tools@<commit>:ns3/routing-bench",
    ]
    suites = ["routing-bench"]            # optional test.py suites; default every module
    [install]                             # a module whose repo installs it (it copies files):
    anthocnet = 'make -s install-ns3 NS3DIR="$NS3DIR" BASELINES=0'
    [cells.static-mesh]                   # the harness flags; the driver adds
    args = "--scenario=paper --nNodes=50 --time=300 --pause=300"   # --protocols, --runs, --firstRun
    blocks = 2                            # optional: split the seeds over 2 jobs

A module with an `install` command is cloned at its commit and the command
runs in that clone with NS3DIR pointing at a scratch tree; it must leave the
module at $NS3DIR/contrib/<name>. Every other module is passed to
reusable-ns3-image.yml as a dependency, which clones it at its commit.

The image is ghcr.io/swarm-routing/ns3-tools-ns3-campaign:<commit7>-<base7>-optimized:
commit7 is the plan's first module's commit (the protocol under test: list it
first), base7 the resolved ns3-dev digest's first 7 hex digits
(reusable-ns3-image.yml resolves the tag once, builds FROM that digest and
appends -optimized; skip-existing reuses an image already built). The
manifest names that pushed tag (swarm-routing/benchmarks#17 accepts the
suffix) and its digest, which is what pins it. Because a reused image may hold
other commits of the other modules, campaign-image.yml checks the image's
modules against the plan (`expect`) before any cell runs.

Every cell's ##PROV## line is written by campaign-cell.yml from the image
itself (its labels and the base's): the base tag and digest, the campaign
image and digest, and every module commit. `collect` checks each cell ran the
campaign image and the plan's commits, then writes the run directory and
manifest of swarm-routing/benchmarks#3.

Reuse (#94): every cell has a key, the sha256 of a canonical JSON of what pins
it: the campaign image digest, every module commit, the harness program, the
harness flags sorted (without the driver's), the arms, the seeds and the
marker contract version (`cell_key`; the flags' order does not change it).
`plan` prints it once the image is built; `dispatch` passes it to
campaign-cell.yml, which writes it into ##PROV## (`key=<64 hex>`, a value
benchmarks' validator reads as neither a digest nor a commit) and into the
artifact name, `campaign-cell-<key>` (a cell from before keys has neither, so
it is never reused). Before dispatching a cell, `dispatch` looks for the same
key, in order, in the local cache, in a swarm-routing/benchmarks checkout
given with `--benchmarks DIR` (a `runs/*/cells/*.txt` file whose ##PROV##
names it; the file's sha256 must equal its manifest entry) and in this
repository's unexpired workflow artifacts of a successful run. A cell found is
recorded as reused in `reused.tsv` with its source and not dispatched;
`status`, `collect` and `verdicts` read it like any other. A reused cell must
pass every check a dispatched one does, and its ##ARGS## and ##PROV## must
recompute to the key: any mismatch stops `dispatch` with an error, never a
silent re-run. `--no-reuse` dispatches every cell. Collected cells are kept in
the cache, ~/.cache/ns3-campaign/<key>/ (NS3_CAMPAIGN_CACHE overrides it;
bounded to NS3_CAMPAIGN_CACHE_MB, default 1024, least recently used out
first), so a repeated `collect` works offline. `plan` and `status` print
"N cells, M reusable, K to run" (runner-minute estimates are #95).
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import zipfile

try:  # as the ns3_tool package, or as a script beside its siblings
    from . import bench_parse, change_plan
except ImportError:
    import bench_parse
    import change_plan

REPO = os.environ.get("NS3_TOOL_REPO", "swarm-routing/ns3-tools")
GIT = os.environ.get("NS3_TOOL_GIT", "https://github.com")  # where `stage` clones from
IMAGE_WORKFLOW = "campaign-image.yml"
CELL_WORKFLOW = "campaign-cell.yml"
IMAGE = "ghcr.io/swarm-routing/ns3-tools-ns3-campaign"
MARKERS_VERSION = 1  # swarm-routing/benchmarks schemas/markers/v1.json
CELL_ARTIFACT = "campaign-cell"  # campaign-cell.yml's artifact; -<key> appended when it has one
CACHE = os.environ.get("NS3_CAMPAIGN_CACHE") or os.path.join(
    os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "ns3-campaign"
)
CACHE_BYTES = int(os.environ.get("NS3_CAMPAIGN_CACHE_MB", "1024")) * 1024 * 1024
POLL = float(os.environ.get("NS3_TOOL_POLL", "15"))  # seconds between GitHub polls

NAME = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
CELL = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
MODULE = re.compile(
    r"^([a-z0-9][a-z0-9-]*)=([A-Za-z0-9-]+/[A-Za-z0-9._-]+)@([0-9a-f]{40})(?::([A-Za-z0-9._/-]+))?$"
)
BASE = re.compile(r"^(latest|[0-9]+\.[0-9]+(?:\.[0-9]+)?)-optimized$")
ARM = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
PROGRAM = re.compile(r"^[a-z0-9][a-z0-9-]*$")
PLAN_PATH = re.compile(r"^campaigns/[A-Za-z0-9._-]+\.toml$")
RESERVED = ("--runs", "--firstRun", "--protocols")
KEYS = {
    "name", "base", "program", "harness", "arms", "reference", "seeds",
    "first_seed", "cap", "modules", "suites", "install", "cells",
}  # fmt: skip


# --- the plan -----------------------------------------------------------------


def modules(plan):
    """[{name, repo, commit, subdir}] in plan order (the build order)."""
    out = []
    for spec in plan.get("modules", []):
        m = MODULE.match(spec) if isinstance(spec, str) else None
        if m:
            out.append({"name": m[1], "repo": m[2], "commit": m[3], "subdir": m[4] or ""})
    return out


def validate(plan):
    """Every problem of a plan, as a list of strings (empty: valid)."""
    errs = [f"unknown key '{k}'" for k in sorted(set(plan) - KEYS)]
    for key in ("name", "base", "arms", "reference", "seeds", "modules", "cells"):
        if key not in plan:
            errs.append(f"no '{key}'")
    if errs:
        return errs
    if not isinstance(plan["name"], str) or not NAME.match(plan["name"]):
        errs.append(f"name '{plan['name']}' is not [a-z0-9-] (the run directory <date>-<name>)")
    if not isinstance(plan["base"], str) or not BASE.match(plan["base"]):
        errs.append(
            f"base '{plan['base']}' is not <version>-optimized or latest-optimized "
            "(benchmarks run on the optimized image, benchmarks rule 13)"
        )
    if not PROGRAM.match(str(plan.get("program", "routing-compare"))):
        errs.append(f"program '{plan['program']}' is not a program name")
    arms = plan["arms"]
    if not isinstance(arms, list) or len(arms) < 2 or len(set(arms)) != len(arms):
        errs.append("arms: at least two distinct arms (a paired comparison, org rule 6)")
    elif not all(isinstance(a, str) and ARM.match(a) for a in arms):
        errs.append(f"arms: {arms} are not arm names")
    elif plan["reference"] not in arms:
        errs.append(f"reference '{plan['reference']}' is not one of the arms")
    seeds, first = plan["seeds"], plan.get("first_seed", 1)
    if not isinstance(seeds, int) or seeds < 20:
        errs.append(f"seeds {seeds}: at least 20 (org rule 6)")
    if not isinstance(first, int) or first < 1:
        errs.append(f"first_seed {first}: an RngRun >= 1")
    cap = plan.get("cap", 12)
    if not isinstance(cap, int) or not 1 <= cap <= 20:
        errs.append(f"cap {cap}: 1..20 (the account runs 20 jobs; leave PR CI some)")
    specs = plan["modules"] if isinstance(plan["modules"], list) else []
    if not specs:
        errs.append("modules: a list of name=owner/repo@<commit>[:subdir]")
    for spec in specs:
        m = MODULE.match(spec) if isinstance(spec, str) else None
        if not m:
            errs.append(
                f"module '{spec}' is not name=owner/repo@<40-char commit>[:subdir] "
                "(a campaign pins commits, benchmarks rule 11)"
            )
        elif m[4] and ".." in m[4].split("/"):
            errs.append(f"module '{spec}': the subdir leaves the repository")
    names = [m["name"] for m in modules(plan)]
    if len(set(names)) != len(names):
        errs.append("modules: a module is named twice")
    if plan.get("harness", "routing-bench") not in names:
        errs.append(f"harness module '{plan.get('harness', 'routing-bench')}' is not in modules")
    install = plan.get("install", {})
    if not isinstance(install, dict):
        errs.append("install: a table of module = command")
        install = {}
    for name, cmd in install.items():
        mod = next((m for m in modules(plan) if m["name"] == name), None)
        if mod is None:
            errs.append(f"install: '{name}' is not a module")
        elif mod["subdir"]:
            errs.append(
                f"install: '{name}' has a subdir; an installed module lands in contrib/{name}"
            )
        if not isinstance(cmd, str) or not cmd.strip():
            errs.append(f"install: '{name}' needs a command")
    suites = plan.get("suites", names)
    if not isinstance(suites, list) or not all(isinstance(s, str) and s for s in suites):
        errs.append("suites: a list of test.py suite names")
    cells = plan["cells"]
    if not isinstance(cells, dict) or not cells:
        errs.append("cells: at least one [cells.<name>] table")
        cells = {}
    for name, cell in cells.items():
        if not CELL.match(name):
            errs.append(f"cell '{name}' is not [a-z0-9._-]")
        if not isinstance(cell, dict) or set(cell) - {"args", "blocks"}:
            errs.append(f"cell '{name}': only 'args' and 'blocks'")
            continue
        args = cell.get("args", "")
        if not isinstance(args, str):
            errs.append(f"cell '{name}': args is a string of harness flags")
            continue
        for tok in args.split():
            if tok.split("=", 1)[0] in RESERVED:
                errs.append(f"cell '{name}': {tok} is set by the driver (arms, seeds, blocks)")
        # A repeated flag: its last value wins in the harness, but the cell key
        # sorts the flags, so two orders would share a key (#94).
        flags = [tok.split("=", 1)[0] for tok in args.split()]
        for flag in sorted({f for f in flags if flags.count(f) > 1}):
            errs.append(f"cell '{name}': {flag} is given more than once")
        blocks = cell.get("blocks", 1)
        if not isinstance(blocks, int) or blocks < 1 or (isinstance(seeds, int) and seeds % blocks):
            errs.append(f"cell '{name}': blocks {blocks} must divide the {seeds} seeds")
    return errs


def load_plan(path):
    try:
        with open(path, "rb") as f:
            plan = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        sys.exit(f"{path}: {e}")
    errs = validate(plan)
    if errs:
        sys.exit(f"{path}: invalid plan:\n" + "\n".join(f"  - {e}" for e in errs))
    plan.setdefault("program", "routing-compare")
    plan.setdefault("harness", "routing-bench")
    plan.setdefault("first_seed", 1)
    plan.setdefault("cap", 12)
    plan.setdefault("install", {})
    plan.setdefault("suites", [m["name"] for m in modules(plan)])
    plan["_path"] = path
    return plan


def seeds(plan):
    return list(range(plan["first_seed"], plan["first_seed"] + plan["seeds"]))


def jobs(plan):
    """[(label, cell, harness args)] in plan order; one job per seed block."""
    out = []
    for cell, spec in plan["cells"].items():
        blocks = spec.get("blocks", 1)
        n = plan["seeds"] // blocks
        for b in range(blocks):
            first = plan["first_seed"] + b * n
            args = " ".join(
                x
                for x in (
                    spec.get("args", ""),
                    f"--protocols={','.join(plan['arms'])}",
                    f"--runs={n}",
                    f"--firstRun={first}",
                )
                if x
            )
            out.append((cell if blocks == 1 else f"{cell}-s{first}", cell, args))
    return out


def image_tag(plan):
    """<commit7>-{base7}: the first module's commit (benchmarks' campaign tag)."""
    return f"{modules(plan)[0]['commit'][:7]}-{{base7}}"


def expected_modules(plan):
    """name@commit of every module, sorted: what the image must hold."""
    return ",".join(sorted(f"{m['name']}@{m['commit']}" for m in modules(plan)))


def parameters(args):
    """`--k=v` flags as {k: v}, numbers as numbers, a bare flag as true."""
    out = {}
    for tok in shlex.split(args):
        if not tok.startswith("--"):
            continue
        key, eq, val = tok[2:].partition("=")
        if not eq:
            out[key] = True
            continue
        for conv in (int, float):
            try:
                out[key] = conv(val)
                break
            except ValueError:
                pass
        else:
            out[key] = val
    return out


# --- the cell key (#94) -------------------------------------------------------


def cell_key(image, mods, program, args):
    """sha256 of every input that pins a cell, as canonical JSON (#94).

    image: the campaign image (only its digest counts); mods: name@commit of
    every module; args: the harness flags, the driver's --protocols, --runs
    and --firstRun included. The other flags are sorted, so their order does
    not change the key; the arms keep their order (it is the output's).
    """
    arms, first, runs, rest = [], None, None, []
    for tok in shlex.split(args):
        flag, _, val = tok.partition("=")
        if flag == "--protocols":
            arms = val.split(",")
        elif flag == "--runs":
            runs = int(val)
        elif flag == "--firstRun":
            first = int(val)
        else:
            rest.append(tok)
    doc = {
        "image": image.rpartition("@")[2],
        "modules": sorted(mods),
        "program": program,
        "args": sorted(rest),
        "arms": arms,
        "seeds": list(range(first, first + runs)) if first and runs else [],
        "markers": MARKERS_VERSION,
    }
    return hashlib.sha256(
        json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def job_key(plan, state, args):
    """The key of one of the plan's jobs in the built image (None before `build`)."""
    if not state.get("image"):
        return None
    mods = [f"{m['name']}@{m['commit']}" for m in modules(plan)]
    return cell_key(state["image"], mods, plan["program"], args)


def key_of_text(text):
    """The key a cell's own ##ARGS## and ##PROV## lines recompute to, or None."""
    prov = prov_of(text)
    args = [ln for ln in text.splitlines() if ln.startswith("##ARGS## ")]
    if prov is None or not args or "@" not in prov.get("image", ""):
        return None
    program, _, flags = args[-1].removeprefix("##ARGS## ").partition(" ")
    mods = [m for m in prov.get("modules", "").split(",") if m]
    return cell_key(prov["image"], mods, program, flags)


# --- GitHub -------------------------------------------------------------------


def gh(*args, stdin=None, binary=False):
    p = subprocess.run(
        ["gh", "api", *args], input=stdin, capture_output=True, text=not binary, check=False
    )
    if p.returncode != 0:
        err = p.stderr if not binary else p.stderr.decode(errors="replace")
        raise RuntimeError(f"gh api {args[0]}: {err.strip()}")
    return p.stdout


def runs_titled(workflow, title):
    """Ids of the workflow's dispatched runs whose run-name is `title`."""
    data = json.loads(
        gh(f"repos/{REPO}/actions/workflows/{workflow}/runs?event=workflow_dispatch&per_page=100")
    )
    return {r["id"] for r in data["workflow_runs"] if r.get("display_title") == title}


def dispatch_command(workflow, ref, inputs):
    """A dispatch as the gh command line that sends the same request."""
    body = json.dumps({"ref": ref, "inputs": inputs})
    return (
        f"gh api -X POST repos/{REPO}/actions/workflows/{workflow}/dispatches "
        f"--input - <<< {shlex.quote(body)}"
    )


def cell_inputs(plan, state, label, args):
    """(run-name, inputs) of one cell's campaign-cell.yml dispatch."""
    title = f"{plan['name']}/{label}"
    return title, {
        "image": state["image"],
        "program": plan["program"],
        "args": args,
        "label": title,
        "key": job_key(plan, state, args),
    }


def cell_command(plan, state, args):
    """What campaign-cell.yml runs for a cell: the image by digest, no rebuild."""
    run = shlex.quote('./ns3 run --no-build "$PROGRAM $ARGS"')
    return (
        f"docker run --rm -e PROGRAM={shlex.quote(plan['program'])} -e ARGS={shlex.quote(args)} "
        f"{state['image']} bash -c {run}"
    )


def dispatch_run(workflow, ref, inputs, title):
    """Dispatch and return the new run's id, found by its run-name `title`."""
    before = runs_titled(workflow, title)
    body = {"ref": ref, "inputs": inputs}
    gh(
        "-X",
        "POST",
        f"repos/{REPO}/actions/workflows/{workflow}/dispatches",
        "--input",
        "-",
        stdin=json.dumps(body),
    )
    for _ in range(30):  # the dispatch API returns no run id
        new = runs_titled(workflow, title) - before
        if new:
            return max(new)
        time.sleep(POLL)
    raise RuntimeError(f"dispatched {workflow} '{title}', but no new run appeared")


def run_state(rid):
    r = json.loads(gh(f"repos/{REPO}/actions/runs/{rid}"))
    return r["conclusion"] or r["status"]


def active_runs():
    """Cell runs holding or waiting for a runner."""
    n = 0
    for status in ("queued", "in_progress"):
        out = gh(
            f"repos/{REPO}/actions/workflows/{CELL_WORKFLOW}/runs?status={status}&per_page=1",
            "--jq",
            ".total_count",
        )
        n += int(out.strip() or 0)
    return n


def fetch(rid, dest, *names):
    """Extract run `rid`'s artifact (the first of `names` it has) into dest."""
    arts = json.loads(gh(f"repos/{REPO}/actions/runs/{rid}/artifacts"))["artifacts"]
    art = next((a for n in names for a in arts if a["name"] == n), None)
    if art is None:
        raise RuntimeError(
            f"run {rid} has no artifact '{' or '.join(names)}' (expired after 7 days?)"
        )
    data = gh(f"repos/{REPO}/actions/artifacts/{art['id']}/zip", binary=True)
    with tempfile.NamedTemporaryFile(suffix=".zip") as z:
        z.write(data)
        z.flush()
        with zipfile.ZipFile(z.name) as zf:
            zf.extractall(dest)


# --- state ----------------------------------------------------------------------


def work_dir(plan, a):
    return a.work or os.path.join(".campaign", plan["name"])


def load_state(work):
    p = os.path.join(work, "state.json")
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        return json.load(f)


def save_state(work, state):
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(work, "state.json"), "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


def read_log(work):
    """label -> run id, from the TSV dispatch appends to."""
    p = os.path.join(work, "runs.tsv")
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        return dict(line.rstrip("\n").split("\t")[:2] for line in f if "\t" in line)


def read_reused(work):
    """label -> source of every cell `dispatch` reused instead of running (#94)."""
    p = os.path.join(work, "reused.tsv")
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        return dict(line.rstrip("\n").split("\t")[:2] for line in f if "\t" in line)


# --- the local cache and the reuse lookup (#94) ---------------------------------


def cache_get(key):
    """(text, source) of a cached cell, or None; a hit counts as a use (LRU)."""
    d = os.path.join(CACHE, key)
    try:
        with open(os.path.join(d, "results.txt")) as f:
            text = f.read()
        with open(os.path.join(d, "source")) as f:
            source = f.read().strip()
    except OSError:
        return None
    os.utime(d)
    return text, source


def cache_put(key, text, source):
    """Keep a verified cell under its key, then trim the cache to CACHE_BYTES."""
    d = os.path.join(CACHE, key)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "results.txt"), "w") as f:
        f.write(text)
    with open(os.path.join(d, "source"), "w") as f:
        f.write(source + "\n")
    os.utime(d)
    entries = []
    for k in os.listdir(CACHE):
        p = os.path.join(CACHE, k)
        if os.path.isdir(p):
            size = sum(os.path.getsize(os.path.join(p, n)) for n in os.listdir(p))
            entries.append((os.stat(p).st_mtime, k, size))
    total = sum(e[2] for e in entries)
    for _mtime, k, size in sorted(entries):  # oldest use first; never the one just written
        if total <= CACHE_BYTES or k == key:
            continue
        shutil.rmtree(os.path.join(CACHE, k), ignore_errors=True)
        total -= size


def benchmarks_cell(root, key):
    """(source, text) of the cell whose ##PROV## names `key` in a benchmarks
    checkout's runs/, or None. The file must match its manifest's sha256."""
    runs = os.path.join(root, "runs")
    for run in sorted(os.listdir(runs)) if os.path.isdir(runs) else []:
        mpath = os.path.join(runs, run, "manifest.json")
        if not os.path.isfile(mpath):
            continue
        with open(mpath) as f:
            files = {x["path"]: x.get("sha256") for x in json.load(f).get("files", [])}
        for rel, sha in files.items():
            path = os.path.join(runs, run, rel)
            if not rel.startswith("cells/") or not os.path.isfile(path):
                continue
            with open(path, "rb") as f:
                if f"key={key}".encode() not in f.read():
                    continue
            return f"benchmarks:{os.path.abspath(path)}", benchmarks_block(path, key, sha)
    return None


def benchmarks_block(path, key, sha=None):
    """The block of a benchmarks cell file whose ##PROV## names `key`.

    The file must match its manifest's sha256 (`sha`, else read from the
    run's manifest.json, two directories up). collect wrote it: a
    '# <name> <cell>: ...' header, then each job's results.txt, each ending
    with its ##PROV## line. Raises RuntimeError on any mismatch."""
    try:
        with open(path, "rb") as f:
            data = f.read()
        if sha is None:
            run = os.path.dirname(os.path.dirname(path))
            with open(os.path.join(run, "manifest.json")) as f:
                rel = os.path.relpath(path, run).replace(os.sep, "/")
                sha = {x["path"]: x.get("sha256") for x in json.load(f).get("files", [])}.get(rel)
    except (OSError, ValueError) as e:
        raise RuntimeError(f"{path}: {e}") from None
    if hashlib.sha256(data).hexdigest() != sha:
        raise RuntimeError(f"{path}: its sha256 is not its manifest entry's")
    block = []
    for line in data.decode().splitlines()[1:]:
        block.append(line)
        if line.startswith("##PROV##"):
            if f"key={key}" in line.split():
                return "\n".join(block) + "\n"
            block = []
    raise RuntimeError(f"{path}: no ##PROV## line names key {key}")


def artifact_runs(key):
    """(live, expired): run ids of this repository's campaign-cell-<key> artifacts."""
    data = json.loads(gh(f"repos/{REPO}/actions/artifacts?name={CELL_ARTIFACT}-{key}&per_page=100"))
    live, expired = [], []
    for art in data.get("artifacts", []):
        rid = (art.get("workflow_run") or {}).get("id")
        if rid:
            (expired if art.get("expired") else live).append(rid)
    return sorted(live, reverse=True), expired


def source_text(work, key, source):
    """A reused cell's text from its source (the cache first)."""
    hit = cache_get(key)
    if hit:
        return hit[0]
    kind, _, ref = source.partition(":")
    if kind == "run":
        dest = os.path.join(work, "artifacts", ref)
        path = os.path.join(dest, "results.txt")
        if not os.path.exists(path):
            fetch(ref, dest, f"{CELL_ARTIFACT}-{key}")
        with open(path) as f:
            return f.read()
    if kind == "benchmarks":  # the recorded file, never another run with the key
        return benchmarks_block(ref, key)
    raise RuntimeError(f"reused cell {key[:12]}: its source {source} is gone, and not cached")


def find_reuse(plan, state, work, label, args, key, benchmarks=None, verify=True):
    """(source, text) of an earlier run of exactly this cell, or None (#94).

    Looks in the local cache, then a benchmarks checkout, then this
    repository's workflow artifacts. With verify, the text passes every check
    a collected cell does and recomputes to `key`; a mismatch raises
    RuntimeError (never a silent re-run). Without verify (plan's count), an
    artifact is only listed, not downloaded, and text is None.
    """
    hit = cache_get(key)
    if hit is None and benchmarks:
        found = benchmarks_cell(benchmarks, key)
        hit = (found[1], found[0]) if found else None
    if hit is None:
        live, expired = artifact_runs(key)
        for rid in live:
            if run_state(rid) != "success":
                continue
            if not verify:
                return f"run:{rid}", None
            text = source_text(work, key, f"run:{rid}")
            hit = (text, f"run:{rid}")
            break
        else:
            if expired:
                print(f"{label}: its earlier run's artifact expired; running it", file=sys.stderr)
            return None
    text, source = hit
    if verify:
        check_cell(plan, state, f"{label} (reused from {source})", source, args, text, key)
        cache_put(key, text, source)
    return source, text


# --- commands -------------------------------------------------------------------


def cmd_validate(plan, _a):
    print(f"{plan['_path']}: valid, {len(jobs(plan))} job(s), image tag {image_tag(plan)}")


def cmd_plan(plan, a):
    state = load_state(work_dir(plan, a))
    image = state.get("image") or f"{IMAGE}:{image_tag(plan)}-optimized (not built)"
    print(f"image\t{image}\tFROM ghcr.io/swarm-routing/ns3-dev:{plan['base']}")
    for m in modules(plan):
        how = "install" if m["name"] in plan["install"] else "dependency"
        sub = f":{m['subdir']}" if m["subdir"] else ""
        print(f"module\t{m['name']}\t{m['repo']}@{m['commit']}{sub}\t{how}")
    reusable = 0
    for label, _cell, args in jobs(plan):
        key = job_key(plan, state, args)
        if (
            key
            and a.reuse
            and find_reuse(
                plan, state, work_dir(plan, a), label, args, key, a.benchmarks, verify=False
            )
        ):
            reusable += 1
        print(f"{plan['name']}/{label}\t{key or '(key after build)'}\t{plan['program']} {args}")
    n = len(jobs(plan))
    print(
        f"{n} job(s) x {len(plan['arms'])} arms, {plan['seeds']} seeds, cap {plan['cap']}",
        file=sys.stderr,
    )
    if state.get("image"):
        print(f"{n} cells, {reusable} reusable, {n - reusable} to run", file=sys.stderr)


def cmd_image_inputs(plan, _a):
    """reusable-ns3-image.yml's inputs, in $GITHUB_OUTPUT form."""
    path = plan["_path"]
    if not PLAN_PATH.match(path):
        sys.exit(f"{path}: the image workflow reads a plan committed as campaigns/<name>.toml")
    deps = [
        f"{m['name']}={m['repo']}@{m['commit']}" + (f":{m['subdir']}" if m["subdir"] else "")
        for m in modules(plan)
        if m["name"] not in plan["install"]
    ]
    own = [f"{n}=.stage/ns3/contrib/{n}" for n in plan["install"]]
    print("dependencies<<EOF_DEPS\n" + "\n".join(deps) + "\nEOF_DEPS")
    print(f"modules={' '.join(own)}")
    print(f"stage={'python3 python/ns3_tool/campaign.py stage ' + path if own else ''}")
    print(f"ns3-version={plan['base'].removesuffix('-optimized')}")
    print(f"tag={image_tag(plan)}")
    print(f"test-suites={' '.join(plan['suites'])}")
    print(f"expect={expected_modules(plan)}")


def cmd_stage(plan, a):
    """Clone each `install` module at its commit and run its install into .stage/ns3."""
    root = os.path.abspath(a.root)
    ns3dir = os.path.join(root, "ns3")
    os.makedirs(os.path.join(ns3dir, "contrib"), exist_ok=True)
    open(os.path.join(ns3dir, "CMakeLists.txt"), "a").close()
    for m in modules(plan):
        cmd = plan["install"].get(m["name"])
        if cmd is None:
            continue
        src = os.path.join(root, "src", m["name"])
        shutil.rmtree(src, ignore_errors=True)
        git = ["git", "-C", src]
        subprocess.run(["git", "init", "--quiet", src], check=True)
        subprocess.run(
            [*git, "fetch", "--quiet", "--depth", "1", f"{GIT}/{m['repo']}", m["commit"]],
            check=True,
        )
        subprocess.run([*git, "checkout", "--quiet", "FETCH_HEAD"], check=True)
        env = dict(os.environ, NS3DIR=ns3dir)
        subprocess.run(["bash", "-euo", "pipefail", "-c", cmd], cwd=src, env=env, check=True)
        dest = os.path.join(ns3dir, "contrib", m["name"])
        if not os.path.isfile(os.path.join(dest, "CMakeLists.txt")):
            sys.exit(f"install of {m['name']} left no {dest}/CMakeLists.txt")
        # Read back by campaign-cell.yml from /src/<name>/ in the image: the
        # commit of a module the image's dependencies label does not list.
        with open(os.path.join(dest, ".swarm-module"), "w") as f:
            f.write(f"{m['name']}={m['repo']}@{m['commit']}\n")
        print(f"staged {m['name']} = {m['repo']}@{m['commit']} -> {dest}")


def cmd_build(plan, a):
    work = work_dir(plan, a)
    state = load_state(work)
    if state.get("image"):
        print(f"image\t{state['image']}\t{state['tag']} (built by run {state['image_run']})")
        return
    path = plan["_path"]
    if not PLAN_PATH.match(path):
        sys.exit(
            f"{path}: build reads the plan from {REPO} at --ref: pass it as campaigns/<name>.toml"
        )
    rid = state.get("image_run")
    if not rid:
        rid = dispatch_run(IMAGE_WORKFLOW, a.ref, {"plan": path}, f"image {path}")
        state.update(image_run=rid, ref=a.ref, plan=path)
        save_state(work, state)
        print(f"dispatched {IMAGE_WORKFLOW} run {rid}", file=sys.stderr)
    while (s := run_state(rid)) in ("queued", "in_progress", "waiting", "pending", "requested"):
        time.sleep(POLL)
    if s != "success":
        sys.exit(f"image run {rid}: {s}; fix it, then delete image_run from {work}/state.json")
    dest = os.path.join(work, "artifacts", str(rid))
    fetch(rid, dest, "campaign-image")
    with open(os.path.join(dest, "campaign-image.json")) as f:
        info = json.load(f)
    if not re.match(rf"^{re.escape(IMAGE)}@sha256:[0-9a-f]{{64}}$", info.get("image", "")):
        sys.exit(f"image run {rid} reported no image by digest: {info}")
    state.update(image=info["image"], tag=info["tag"])
    save_state(work, state)
    print(f"image\t{state['image']}\t{state['tag']}")


def cmd_dispatch(plan, a):
    work = work_dir(plan, a)
    state = load_state(work)
    if not state.get("image"):
        sys.exit("no campaign image yet: run `build` first")
    state.setdefault("date", datetime.datetime.now(datetime.timezone.utc).date().isoformat())
    save_state(work, state)
    cap = a.cap or plan["cap"]
    done, reused = read_log(work), read_reused(work)
    for label, _cell, args in jobs(plan):
        if label in done or (a.reuse and label in reused):
            continue  # with --no-reuse a reused label runs, and its run then wins
        if a.reuse:
            key = job_key(plan, state, args)
            try:
                hit = find_reuse(plan, state, work, label, args, key, a.benchmarks)
            except RuntimeError as e:
                sys.exit(f"reuse: {e}\n(a mismatch is an error; --no-reuse runs every cell anew)")
            if hit:
                with open(os.path.join(work, "reused.tsv"), "a") as f:
                    f.write(f"{label}\t{hit[0]}\n")
                reused[label] = hit[0]
                print(f"{label}\treused {hit[0]}")
                continue
        while active_runs() >= cap:
            if not a.wait:
                print(
                    f"cap {cap} reached; {len(read_log(work))} of {len(jobs(plan))} "
                    "dispatched, run again later",
                    file=sys.stderr,
                )
                return
            time.sleep(POLL * 8)
        title, inputs = cell_inputs(plan, state, label, args)
        rid = dispatch_run(CELL_WORKFLOW, state.get("ref", "main"), inputs, title)
        with open(os.path.join(work, "runs.tsv"), "a") as f:
            f.write(f"{label}\t{rid}\n")
        print(f"{label}\t{rid}")
    n, r = len(jobs(plan)), len(set(reused) - set(read_log(work)))
    print(f"all {n} job(s) dispatched" + (f" ({r} reused)" if r else ""), file=sys.stderr)


def cmd_status(plan, a):
    log, reused = read_log(work_dir(plan, a)), read_reused(work_dir(plan, a))
    counts = {}
    for label, *_ in jobs(plan):
        rid = log.get(label)
        if label in reused and not rid:  # a run recorded for the label wins (--no-reuse)
            rid, state = reused[label], "reused"
        else:
            state = run_state(rid) if rid else "not-dispatched"
        counts[state] = counts.get(state, 0) + 1
        if not (a.brief and state in ("success", "reused")):
            print(f"{label}\t{rid or '-'}\t{state}")
    print(", ".join(f"{v} {k}" for k, v in sorted(counts.items())), file=sys.stderr)
    n, r = len(jobs(plan)), counts.get("reused", 0)
    print(f"{n} cells, {r} reused, {n - r} run here", file=sys.stderr)


def prov_of(text):
    """The last ##PROV## line's key=value fields."""
    lines = [ln for ln in text.splitlines() if ln.startswith("##PROV##")]
    if not lines:
        return None
    return dict(t.split("=", 1) for t in lines[-1].split()[1:] if "=" in t)


def cell_text(plan, work, label, rid, args):
    """One job's results.txt: a successful run, every arm on exactly the job's
    seed block, and the campaign's ##PROV##."""
    state = load_state(work)
    if (s := run_state(rid)) != "success":
        raise RuntimeError(f"{label}: run {rid} is {s}, not success")
    src = os.path.join(work, "artifacts", str(rid))
    path = os.path.join(src, "results.txt")
    key = job_key(plan, state, args)
    if not os.path.exists(path):
        fetch(rid, src, f"{CELL_ARTIFACT}-{key}", CELL_ARTIFACT)
    with open(path) as f:
        text = f.read()
    # A keyed cell gets the key checks too; a cell from before keys is never reused (#94).
    keyed = "key" in (prov_of(text) or {})
    prov = check_cell(plan, state, label, rid, args, text, key if keyed else None)
    if keyed:
        cache_put(key, text, f"run:{rid}")
    return text, prov


def check_cell(plan, state, label, rid, args, text, key=None):
    """A cell's checks: every arm on the job's whole seed block, the campaign
    image and the plan's commits in ##PROV##, and, for a reused cell (`key`),
    the key in ##PROV## and recomputed from ##ARGS## and ##PROV##. Returns the
    ##PROV## fields; raises RuntimeError naming the label."""
    # A crashed or timed-out harness still uploads a partial results.txt with
    # ##PROV## stamped on it: every arm must carry the whole seed block.
    p = parameters(args)
    block = set(range(p["firstRun"], p["firstRun"] + p["runs"]))
    runs = bench_parse.parse_runs(text)
    for arm in plan["arms"]:
        got_seeds = set(runs.get(arm, {}))
        if got_seeds != block:
            missing = sorted(block - got_seeds)
            raise RuntimeError(
                f"{label}: run {rid} arm {arm} lacks seeds {missing} of its block "
                f"{min(block)}..{max(block)} (a failed or timed-out run)"
            )
    prov = prov_of(text)
    if prov is None:
        raise RuntimeError(f"{label}: run {rid} has no ##PROV## line")
    image = prov.get("image", "")
    if image.rsplit("@", 1)[-1] != state["image"].rsplit("@", 1)[-1]:
        raise RuntimeError(
            f"{label}: run {rid} ran {image}, not the campaign image {state['image']}"
        )
    want = {f"{m['name']}@{m['commit']}" for m in modules(plan)}
    got = set(prov.get("modules", "").split(",")) - {""}
    if got != want:
        raise RuntimeError(f"{label}: run {rid} has modules {sorted(got)}, the plan {sorted(want)}")
    if key is not None:
        if prov.get("key") != key:
            raise RuntimeError(f"{label}: ##PROV## key={prov.get('key')}, not the cell's {key}")
        if key_of_text(text) != key:
            raise RuntimeError(
                f"{label}: its ##ARGS## and ##PROV## recompute to key {key_of_text(text)}, not {key}"
            )
    return prov


def cmd_collect(plan, a):
    work = work_dir(plan, a)
    state, log = load_state(work), read_log(work)
    if not state.get("date"):
        sys.exit("nothing dispatched yet")
    by_cell, provs, errs = {}, [], []
    reused = read_reused(work)
    for label, cell, args in jobs(plan):
        rid = log.get(label)
        if label in reused and not rid:  # #94: from its source, checked as at dispatch
            rid, key = reused[label], job_key(plan, state, args)
            try:
                text = source_text(work, key, rid)
                prov = check_cell(plan, state, label, rid, args, text, key)
            except RuntimeError as e:
                errs.append(str(e))
                continue
            provs.append(prov)
            by_cell.setdefault(cell, []).append((label, rid, args, text))
            continue
        if not rid:
            errs.append(f"{label}: not dispatched")
            continue
        try:
            text, prov = cell_text(plan, work, label, rid, args)
        except RuntimeError as e:
            errs.append(str(e))
            continue
        provs.append(prov)
        by_cell.setdefault(cell, []).append((label, rid, args, text))
    bases = {p["base"] for p in provs}
    if len(bases) > 1:
        errs.append(f"cells name different bases: {sorted(bases)}")
    if errs:
        sys.exit("not collected:\n" + "\n".join(f"  - {e}" for e in errs))
    run_dir = os.path.join(a.out, f"{state['date']}-{plan['name']}")
    os.makedirs(os.path.join(run_dir, "cells"), exist_ok=True)
    shutil.copyfile(plan["_path"], os.path.join(run_dir, "plan.toml"))
    files, commands = [], []
    for cell, parts in by_cell.items():
        rel = f"cells/{cell}.txt"
        runs = " / ".join(f"{rid} ({label})" for label, rid, _a, _t in parts)
        with open(os.path.join(run_dir, rel), "w") as f:
            f.write(f"# {plan['name']} {cell}: {len(parts)} run(s): {runs}\n")
            for _l, _r, _a, text in parts:
                f.write(text if text.endswith("\n") else text + "\n")
        files.append({"path": rel, "kind": "cell", "scenario": cell})
        # In order, per job: the dispatch, the container command, the harness line.
        for label, _r, args, _t in parts:
            _title, inputs = cell_inputs(plan, state, label, args)
            commands += [
                dispatch_command(CELL_WORKFLOW, state.get("ref", "main"), inputs),
                cell_command(plan, state, args),
                f"{plan['program']} {args}",
            ]
    files.append({"path": "plan.toml", "kind": "preregistration"})
    for entry in files:
        with open(os.path.join(run_dir, entry["path"]), "rb") as f:
            entry["sha256"] = hashlib.sha256(f.read()).hexdigest()
    base_ref, _, base_digest = provs[0]["base"].rpartition("@")
    image_ref, _, image_digest = provs[0]["image"].rpartition("@")  # the pushed tag
    manifest = {
        "schema_version": 1,
        "campaign": plan["name"],
        "date": state["date"],
        "base_image": {
            "ref": base_ref,
            "digest": base_digest,
            "ns3_version": re.search(r":([0-9.]+)-", base_ref)[1] if ":" in base_ref else "",
            "profile": "optimized",
        },
        "campaign_image": {"ref": image_ref, "digest": image_digest},
        "modules": [{k: m[k] for k in ("name", "repo", "commit")} for m in modules(plan)],
        "harness": {"program": plan["program"], "module": plan["harness"]},
        "scenarios": [
            {"name": c, "arms": plan["arms"], "parameters": parameters(s.get("args", ""))}
            for c, s in plan["cells"].items()
        ],
        "preregistration": {"path": "plan.toml"},
        "seeds": seeds(plan),
        "commands": [
            dispatch_command(
                IMAGE_WORKFLOW, state.get("ref", "main"), {"plan": state.get("plan", plan["_path"])}
            ),
            *commands,
        ],
        "markers": {"schema_version": MARKERS_VERSION},
        "files": files,
    }
    with open(os.path.join(run_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    print(f"wrote {run_dir}: {len(by_cell)} cell(s), manifest.json", file=sys.stderr)


def cmd_verdicts(plan, a):
    ref = plan["reference"]
    for cell in plan["cells"]:
        path = os.path.join(a.dir, "cells", f"{cell}.txt")
        if not os.path.exists(path):
            print(f"{cell}\t-\t(missing {path})")
            continue
        with open(path) as f:
            runs = bench_parse.parse_runs(f.read())
        for arm in plan["arms"]:
            if arm == ref:
                continue
            ps = bench_parse.paired_stats(runs.get(ref, {}), runs.get(arm, {}))
            line = bench_parse.fmt_paired(ps) if ps else "(no paired ##RUN## rows)"
            print(f"{cell}\t{arm} - {ref}\t{line}")


COMMANDS = {
    "validate": cmd_validate,
    "plan": cmd_plan,
    "image-inputs": cmd_image_inputs,
    "stage": cmd_stage,
    "build": cmd_build,
    "dispatch": cmd_dispatch,
    "status": cmd_status,
    "collect": cmd_collect,
    "verdicts": cmd_verdicts,
}


def main(argv=None):
    # A change-test plan (AntHocNet's format: a `generation` key; every .yaml,
    # .yml or .json plan) runs through change_plan.py (#81).
    args = sys.argv[1:] if argv is None else argv
    plans = [x for x in args[1:] if not x.startswith("-") and os.path.isfile(x)]
    if plans and change_plan.is_change_plan(plans[0]):
        return change_plan.main(args)
    p = argparse.ArgumentParser(prog="ns3-campaign", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in COMMANDS:
        s = sub.add_parser(name)
        s.add_argument("plan")
        s.add_argument("--work", help="state directory (default .campaign/<name>)")
        if name == "stage":
            s.add_argument("--root", default=".stage")
        if name == "build":
            s.add_argument("--ref", default="main", help=f"the {REPO} ref holding the plan")
        if name in ("plan", "dispatch"):
            s.add_argument(
                "--no-reuse",
                dest="reuse",
                action="store_false",
                help="dispatch every cell, even one that already ran (#94)",
            )
            s.add_argument(
                "--benchmarks",
                metavar="DIR",
                help="a swarm-routing/benchmarks checkout to reuse released cells from",
            )
        if name == "dispatch":
            s.add_argument("--cap", type=int)
            s.add_argument(
                "--wait", action="store_true", help="sleep at the cap instead of exiting"
            )
        if name == "status":
            s.add_argument(
                "--brief", action="store_true", help="only the labels not yet successful"
            )
        if name == "collect":
            s.add_argument("--out", required=True, help="the runs/ root of a benchmarks checkout")
        if name == "verdicts":
            s.add_argument("--dir", required=True, help="the run directory collect wrote")
    a = p.parse_args(argv)
    COMMANDS[a.cmd](load_plan(a.plan), a)


if __name__ == "__main__":
    main()
