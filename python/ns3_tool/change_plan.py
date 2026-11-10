#!/usr/bin/env python3
# Copyright (c) 2026 Daniel Joppi (https://github.com/swarm-routing)
# SPDX-License-Identifier: MIT OR GPL-2.0-only
"""Change-test campaigns in a protocol repository (#81): AntHocNet's plans.

Ported from AntHocNet's tools/bench/campaign.py (AntHocNet#662), with the
repository, the caller workflow and the plan path as parameters, so every
protocol repository runs its own change tests through
swarm-routing/.github's reusable-benchmark-cell.yml (swarm-routing/.github#157,
epic #156). `ns3-campaign` routes a plan here when it has a `generation` key
(a .yaml/.yml plan always does); the multi-protocol release plans
(campaigns/*.toml) stay in campaign.py.

    ns3-campaign plan     PLAN                      # every job; dispatches nothing
    ns3-campaign dispatch PLAN [--arm A] [--cap N] [--wait]
    ns3-campaign status   PLAN [--arm A] [--brief]
    ns3-campaign collect  PLAN --out DIR [--from DIR] [--arm A] [--redispatch]
    ns3-campaign verdicts PLAN --dir DIR [--arm A]

Every GitHub command takes `--repo owner/name` (default: the plan's `repo`)
and `--workflow FILE` (default: the plan's `workflow`, else
benchmark-cell.yml): the repository's thin caller of the reusable cell. State
(pins, dispatch date, the label -> run id log `runs.tsv`, downloaded
artifacts) lives in `--work`, default .campaign/<generation>/, so `dispatch`
is resumable: it dispatches until the cap and exits, and is re-run until every
label is logged.

A campaign is cells x arms (AntHocNet's format, unchanged):

    generation: v230              # file suffix
    ref: main                     # the ref the caller workflow is dispatched at
    repo: swarm-routing/AntHocNet # the repository whose caller runs the cells
    workflow: benchmark-cell.yml  # that caller (default benchmark-cell.yml)
    source_sha: <40 hex>          # REQUIRED to dispatch: the commit every cell runs
    image: ghcr.io/...@sha256:... # REQUIRED to dispatch: the image every cell runs
    pins:                         # optional, per arm (a PR arm against a base arm):
      base: {source_sha: <40 hex>, image: ghcr.io/...@sha256:...}
    program: routing-compare      # the MANET program (default anthocnet-compare)
    cap: 12
    control: control              # the arm whose files carry no arm suffix
    arms:
      control: ""
      quiet: "--ns3::anthocnet::RoutingProtocol::QuietMode=true"
    cells:
      scale-500:
        workflow: benchmark-cell.yml
        inputs: {harness: anthocnet-compare, args: "--scenario=paper --runs=2 ..."}
        blocks: [1, 5, 9]          # one job per firstRun, one file
        armArgs: {quiet: "--..."}  # this cell only
    jobs:
      - {arms: [control, quiet], cells: "*", inputs: {protocols: "anthocnet,aodv"}}

`source_sha` and `image` are what every cell is pinned to: `dispatch` passes
them to the reusable cell, which checks out `source_sha` whatever the dispatch
ref and fails unless HEAD is that commit (AntHocNet#867), and runs `image` by
digest. An arm's `pins` entry overrides either for that arm. `collect` reads
each cell's ##PROV## line and rejects a cell whose commit= or image digest is
not its arm's pin; it lists the rejected labels for re-dispatch, and
`--redispatch` drops them from runs.tsv so the next `dispatch` sends them
again. `plan` and `verdicts` also read the committed historical plans, which
predate the pins.

A cell naming the retired paper-benchmark.yml or satellite-benchmark.yml is
translated into the command line those workflows built, flag for flag
(legacy_command). A plan's `legacy_images` (AntHocNet#867's in-job builds in a
frozen image) has no counterpart in the reusable cell: such a plan is refused
by `dispatch` and stays on AntHocNet's own legacy path.

`collect` writes one file per (cell, arm), `<cell>-<generation>.txt` for the
control arm and `<cell>-<generation>-<arm>.txt` for the others; `verdicts`
runs bench_parse.py --ab --proto P on every arm's file against the control
file of the same cell, for every protocol P the cell's command line names, and
prints one line per (arm, cell, protocol): byte for byte AntHocNet's output on
the same files.

Plans are YAML (PyYAML, imported only for a .yaml/.yml plan), TOML or JSON.
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import tomllib
import zipfile

CELL_KIND = "benchmark-cell.yml"  # a cell giving harness/args itself
WORKFLOW = "benchmark-cell.yml"  # the default caller in the protocol repository
ARTIFACT = "benchmark-cell"  # what reusable-benchmark-cell.yml uploads
RESULTS = ("results.txt", "paper-results.txt", "satellite-results.txt")
IMAGE_RE = re.compile(r"ghcr\.io/[a-z0-9._-]+/[a-z0-9._/-]+@sha256:[0-9a-f]{64}")
SHA_RE = re.compile(r"[0-9a-f]{40}")
REPO_RE = re.compile(r"[A-Za-z0-9-]+/[A-Za-z0-9._-]+")
CELL_RE = re.compile(r"[a-z0-9][a-z0-9._-]*")
POLL = float(os.environ.get("NS3_TOOL_POLL", "15"))  # seconds between GitHub polls

# The retired workflows' input defaults, for inputs a plan leaves out.
PAPER_DEFAULTS = {
    "scenario": "paper", "version": "3.42", "nNodes": "50", "time": "300", "runs": "3",
    "areaX": "1500", "areaY": "", "pause": "30", "speed": "20",
    "protocols": "anthocnet,aodv,olsr,dsdv", "propagation": "range", "mobility": "rwp",
    "transport": "udp", "range": "300", "harness": "compare", "extraArgs": "",
}  # fmt: skip
SAT_DEFAULTS = {
    "harness": "isl-grid", "version": "3.42", "rows": "4", "cols": "4", "torus": "true",
    "islDelayMs": "5", "islRate": "10Mbps", "time": "300", "runs": "3", "flows": "8",
    "cbrBps": "4096", "protocols": "anthocnet,aodv,olsr,oracle", "extraArgs": "",
}  # fmt: skip

# The MANET-family program. AntHocNet's ADR-0027 kept anthocnet-compare as an
# alias of routing-compare, so committed plans keep their name; a plan sets
# `program: routing-compare` to run the new one.
PROGRAM = "anthocnet-compare"


# --- the plan -----------------------------------------------------------------


def legacy_command(wf, inp, program=PROGRAM):
    """(harness, args, version) for one job, as the cell's inputs.

    paper-benchmark.yml and satellite-benchmark.yml built the harness command
    line in shell from their inputs; this is that expansion, flag for flag and
    in the same order. A benchmark-cell.yml cell gives harness/args/version
    itself, and its extraArgs (seed block, arm) are appended to args.
    """
    if wf == CELL_KIND:
        args = " ".join(x for x in (inp.get("args", ""), inp.get("extraArgs", "")) if x)
        return inp.get("harness", program), args, inp.get("version", "3.42-opt")
    if wf == "paper-benchmark.yml":
        i = dict(PAPER_DEFAULTS, **inp)
        if i["harness"] != "compare":
            sys.exit(
                f"paper-benchmark harness '{i['harness']}' has no benchmark-cell "
                "equivalent (manet-baselines is not a benchmark-cell harness)"
            )
        args = [
            f"--scenario={i['scenario'] or 'paper'}", f"--runs={i['runs']}",
            f"--nNodes={i['nNodes']}", f"--time={i['time']}", f"--areaX={i['areaX']}",
            f"--pause={i['pause']}", f"--speed={i['speed']}",
            f"--propagation={i['propagation']}", f"--range={i['range']}",
        ]  # fmt: skip
        if i["areaY"]:
            args.append(f"--areaY={i['areaY']}")
        if i["extraArgs"]:
            args.append(i["extraArgs"])
        args += [
            "--diag", f"--mobility={i['mobility']}", f"--transport={i['transport']}",
            f"--protocols={i['protocols']}",
        ]  # fmt: skip
        return program, " ".join(args), i["version"]
    if wf == "satellite-benchmark.yml":
        i = dict(SAT_DEFAULTS, **inp)
        common = [
            f"--time={i['time']}", f"--runs={i['runs']}", f"--flows={i['flows']}",
            f"--cbrBps={i['cbrBps']}", f"--islRate={i['islRate']}",
        ]  # fmt: skip
        if i["harness"] == "leo-walker":
            args = common + [f"--protocols={i['protocols']}"]
        elif i["harness"] == "isl-grid":
            args = [
                f"--rows={i['rows']}", f"--cols={i['cols']}", f"--torus={i['torus']}",
                f"--islDelayMs={i['islDelayMs']}", f"--islRate={i['islRate']}",
                f"--time={i['time']}", f"--runs={i['runs']}", f"--flows={i['flows']}",
                f"--cbrBps={i['cbrBps']}", "--diag", f"--protocols={i['protocols']}",
            ]  # fmt: skip
        else:
            sys.exit(f"unknown satellite harness '{i['harness']}'")
        if i["extraArgs"]:
            args.append(i["extraArgs"])
        return i["harness"], " ".join(args), i["version"]
    sys.exit(
        f"workflow '{wf}': a cell names benchmark-cell.yml, or a retired workflow this "
        "translates (paper-benchmark.yml, satellite-benchmark.yml)"
    )


def read_plan_file(path):
    """The plan as a dict: YAML (PyYAML), TOML or JSON, by extension."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in (".yaml", ".yml"):
            try:
                import yaml
            except ImportError:
                sys.exit(
                    f"{path}: a YAML plan needs PyYAML (pip install pyyaml); "
                    "or write the plan as TOML or JSON"
                )
            with open(path) as f:
                return yaml.safe_load(f)
        if ext == ".toml":
            with open(path, "rb") as f:
                return tomllib.load(f)
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        sys.exit(f"{path}: {e}")


def is_change_plan(path):
    """A YAML/JSON plan, or a TOML one with a `generation` key."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".yaml", ".yml", ".json"):
        return True
    if ext == ".toml" and os.path.isfile(path):
        try:
            with open(path, "rb") as f:
                return "generation" in tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError):
            return False
    return False


def load_plan(path):
    plan = read_plan_file(path)
    if not isinstance(plan, dict):
        sys.exit(f"{path}: a plan is a mapping")
    for key in ("generation", "arms", "cells", "jobs"):
        if key not in plan:
            sys.exit(f"{path}: plan has no '{key}'")
    plan.setdefault("ref", "main")
    plan.setdefault("program", PROGRAM)
    plan.setdefault("cap", 12)
    plan.setdefault("control", "control")
    plan.setdefault("workflow", WORKFLOW)
    plan["legacy_images"] = {str(k): v for k, v in (plan.get("legacy_images") or {}).items()}
    for version, ref in plan["legacy_images"].items():
        if not IMAGE_RE.fullmatch(str(ref)):
            sys.exit(
                f"{path}: legacy_images {version}: {ref!r} is not "
                "ghcr.io/<owner>/<name>@sha256:<64 hex>"
            )
    pins = plan.get("pins") or {}
    if not isinstance(pins, dict):
        sys.exit(f"{path}: pins is a mapping arm -> {{source_sha, image}}")
    for where, d in [("plan", plan)] + [(f"pins {a}", p) for a, p in pins.items()]:
        if where != "plan" and (not isinstance(d, dict) or set(d) - {"source_sha", "image"}):
            sys.exit(f"{path}: {where}: only source_sha and image")
        if "source_sha" in d and not SHA_RE.fullmatch(str(d["source_sha"])):
            sys.exit(f"{path}: {where}: source_sha {d['source_sha']!r} is not a 40-char commit")
        if "image" in d and not IMAGE_RE.fullmatch(str(d["image"])):
            sys.exit(
                f"{path}: {where}: image {d['image']!r} is not ghcr.io/<owner>/<name>@sha256:<64 hex>"
            )
    for arm in pins:
        if arm not in plan["arms"]:
            sys.exit(f"{path}: pins names arm '{arm}', which the plan does not define")
    plan["pins"] = pins
    plan["_path"] = path
    return plan


def pin(plan, arm):
    """(source_sha, image) an arm's cells run; exits if either is missing."""
    p = plan["pins"].get(arm, {})
    sha, image = p.get("source_sha", plan.get("source_sha")), p.get("image", plan.get("image"))
    if not sha or not image:
        sys.exit(
            f"arm {arm}: no {'source_sha' if not sha else 'image'}: every cell is pinned to a "
            "commit and an image by digest (plan keys source_sha and image, or pins.<arm>)"
        )
    return sha, image


def selected(plan, a):
    """jobs(plan), limited to --arm when it is given."""
    arm = getattr(a, "arm", None)
    if arm and arm not in plan["arms"]:
        sys.exit(f"--arm {arm}: the plan has no such arm")
    return [j for j in jobs(plan) if not arm or j[2] == arm]


def jobs(plan):
    """[(label, cell, arm, workflow, inputs)] in plan order, labels unique."""
    out, seen = [], set()
    for entry in plan["jobs"]:
        names = entry.get("cells", "*")
        names = list(plan["cells"]) if names == "*" else names
        for arm in entry["arms"]:
            if arm not in plan["arms"]:
                sys.exit(f"job names arm '{arm}', which the plan does not define")
            for cell in names:
                if cell not in plan["cells"]:
                    sys.exit(f"job names cell '{cell}', which the plan does not define")
                spec = plan["cells"][cell]
                inp = dict(spec.get("inputs", {}), **entry.get("inputs", {}))
                extra = " ".join(
                    x
                    for x in (
                        str(inp.get("extraArgs", "")),
                        plan["arms"][arm] or "",
                        spec.get("armArgs", {}).get(arm, ""),
                    )
                    if x
                )
                for first in spec.get("blocks", [None]):
                    args = f"--firstRun={first} {extra}".strip() if first else extra
                    label = f"{arm}/{cell}" + (f"-s{first}" if first else "")
                    if label in seen:
                        sys.exit(f"label '{label}' appears twice in the plan")
                    seen.add(label)
                    job_inp = {k: str(v) for k, v in inp.items()}
                    if args:
                        job_inp["extraArgs"] = args
                    else:
                        job_inp.pop("extraArgs", None)
                    out.append((label, cell, arm, spec["workflow"], job_inp))
    return out


def out_name(plan, cell, arm):
    suffix = "" if arm == plan["control"] else f"-{arm}"
    return f"{cell}-{plan['generation']}{suffix}.txt"


def cell_protocols(plan, cell, arm):
    """The protocols an arm's jobs of a cell run, in command-line order."""
    out = []
    for _l, c, job_arm, wf, inp in jobs(plan):
        if (c, job_arm) != (cell, arm):
            continue
        args = legacy_command(wf, inp, plan["program"])[1]
        named = re.findall(r"--protocols=(\S+)", args)
        for p in named[-1].split(",") if named else ["anthocnet"]:
            if p not in out:
                out.append(p)
    return out


# --- GitHub -------------------------------------------------------------------


def gh(*args, stdin=None, binary=False):
    p = subprocess.run(
        ["gh", "api", *args], input=stdin, capture_output=True, text=not binary, check=False
    )
    if p.returncode != 0:
        err = p.stderr if not binary else p.stderr.decode(errors="replace")
        raise RuntimeError(f"gh api {args[0]}: {err.strip()}")
    return p.stdout


def target(plan, a):
    """(repo, workflow): --repo/--workflow, else the plan's."""
    repo = getattr(a, "repo", None) or plan.get("repo")
    if not repo or not REPO_RE.fullmatch(repo):
        sys.exit(
            f"the repository whose caller runs the cells: --repo owner/name or plan key repo (got {repo!r})"
        )
    return repo, getattr(a, "workflow", None) or plan["workflow"]


def active_runs(repo, wf):
    """Cell runs holding or waiting for a runner."""
    n = 0
    for status in ("queued", "in_progress"):
        out = gh(
            f"repos/{repo}/actions/workflows/{wf}/runs?status={status}&per_page=1",
            "--jq",
            ".total_count",
        )
        n += int(out.strip() or 0)
    return n


def runs_titled(repo, wf, title):
    """Ids of the workflow's dispatched runs whose run-name is `title`."""
    data = json.loads(
        gh(f"repos/{repo}/actions/workflows/{wf}/runs?event=workflow_dispatch&per_page=100")
    )
    return {r["id"] for r in data["workflow_runs"] if r.get("display_title") == title}


def dispatch_run(repo, wf, ref, inputs, title):
    """Dispatch and return the new run's id, found by its run-name `title`."""
    before = runs_titled(repo, wf, title)
    gh(
        "-X", "POST", f"repos/{repo}/actions/workflows/{wf}/dispatches", "--input", "-",
        stdin=json.dumps({"ref": ref, "inputs": inputs}),
    )  # fmt: skip
    for _ in range(30):  # the dispatch API returns no run id
        new = runs_titled(repo, wf, title) - before
        if new:
            return max(new)
        time.sleep(POLL)
    raise RuntimeError(f"dispatched {wf} '{title}', but no new run appeared")


def run_state(repo, rid):
    r = json.loads(gh(f"repos/{repo}/actions/runs/{rid}"))
    return r["conclusion"] or r["status"]


def fetch(repo, rid, dest):
    """Extract run `rid`'s benchmark-cell artifact into dest."""
    arts = json.loads(gh(f"repos/{repo}/actions/runs/{rid}/artifacts"))["artifacts"]
    art = next((x for x in arts if x["name"] == ARTIFACT), None)
    if art is None:
        raise RuntimeError(f"run {rid} has no artifact '{ARTIFACT}' (expired after 7 days?)")
    data = gh(f"repos/{repo}/actions/artifacts/{art['id']}/zip", binary=True)
    with tempfile.NamedTemporaryFile(suffix=".zip") as z:
        z.write(data)
        z.flush()
        with zipfile.ZipFile(z.name) as zf:
            zf.extractall(dest)


# --- state --------------------------------------------------------------------


def work_dir(plan, a):
    return getattr(a, "work", None) or os.path.join(".campaign", str(plan["generation"]))


def load_state(work):
    p = os.path.join(work, "state.json")
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        return json.load(f)


def save_state(work, state):
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(work, "state.json"), "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")


def read_log(work):
    """label -> run id, from the TSV dispatch appends to."""
    p = os.path.join(work, "runs.tsv")
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        return dict(line.rstrip("\n").split("\t")[:2] for line in f if "\t" in line)


def write_log(work, log):
    with open(os.path.join(work, "runs.tsv"), "w") as f:
        f.writelines(f"{label}\t{rid}\n" for label, rid in log.items())


# --- commands -----------------------------------------------------------------


def cmd_plan(plan, _a):
    for label, _c, arm, wf, inp in jobs(plan):
        harness, args, _version = legacy_command(wf, inp, plan["program"])
        p = plan["pins"].get(arm, {})
        image = p.get("image", plan.get("image")) or "(no image pinned)"
        print(f"{label}\t{image}\t{harness} {args}")
    shas = sorted(
        {
            plan["pins"].get(a, {}).get("source_sha", plan.get("source_sha")) or "-"
            for a in plan["arms"]
        }
    )
    print(
        f"{len(jobs(plan))} job(s), cap {plan['cap']}, ref {plan['ref']}, "
        f"source_sha {', '.join(shas)}",
        file=sys.stderr,
    )


def cmd_dispatch(plan, a):
    repo, wf = target(plan, a)
    if plan["legacy_images"]:
        sys.exit(
            "legacy_images: the reusable cell runs one image by digest and has no "
            "in-job legacy build; dispatch this plan with the repository's own legacy path"
        )
    mine = selected(plan, a)
    pins = {arm: pin(plan, arm) for arm in sorted({j[2] for j in mine})}
    work = work_dir(plan, a)
    state = load_state(work)
    logged = state.get("pins", {})
    for arm, (sha, image) in pins.items():
        if arm in logged and logged[arm] != [sha, image]:
            sys.exit(
                f"{work}/state.json pins arm {arm} to {logged[arm][0]} on {logged[arm][1]}; "
                f"refusing {sha} on {image} (one campaign, one commit and image per arm)"
            )
        logged[arm] = [sha, image]
    state.update(pins=logged, repo=repo, workflow=wf)
    state.setdefault("date", datetime.datetime.now(datetime.timezone.utc).date().isoformat())
    save_state(work, state)
    cap = a.cap or plan["cap"]
    done = read_log(work)
    for label, cell, arm, jwf, inp in mine:
        if label in done:
            continue
        if not CELL_RE.fullmatch(cell):
            sys.exit(f"cell '{cell}' is not [a-z0-9][a-z0-9._-]*")
        harness, args, _version = legacy_command(jwf, inp, plan["program"])
        while active_runs(repo, wf) >= cap:
            if not a.wait:
                print(
                    f"cap {cap} reached; {len(read_log(work))} of {len(mine)} "
                    "dispatched, run again later",
                    file=sys.stderr,
                )
                return
            time.sleep(POLL * 8)
        sha, image = pins[arm]
        title = f"{plan['generation']}/{label}"
        inputs = {"image": image, "source_sha": sha, "harness": harness, "args": args,
                  "cell": cell, "label": title}  # fmt: skip
        rid = dispatch_run(repo, wf, plan["ref"], inputs, title)
        with open(os.path.join(work, "runs.tsv"), "a") as f:
            f.write(f"{label}\t{rid}\n")
        print(f"{label}\t{rid}")
    print(f"all {len(mine)} job(s) dispatched", file=sys.stderr)


def cmd_status(plan, a):
    repo, _wf = target(plan, a)
    log = read_log(work_dir(plan, a))
    counts = {}
    for label, *_ in selected(plan, a):
        rid = log.get(label)
        state = run_state(repo, rid) if rid else "not-dispatched"
        counts[state] = counts.get(state, 0) + 1
        if not (a.brief and state == "success"):
            print(f"{label}\t{rid or '-'}\t{state}")
    print(", ".join(f"{v} {k}" for k, v in sorted(counts.items())), file=sys.stderr)


def results_text(directory):
    for name in RESULTS:
        p = os.path.join(directory, name)
        if os.path.exists(p):
            with open(p) as f:
                return f.read()
    return None


def prov_of(text):
    """The last ##PROV## line's key=value fields, or None."""
    lines = [ln for ln in text.splitlines() if ln.startswith("##PROV##")]
    if not lines:
        return None
    return dict(t.split("=", 1) for t in lines[-1].split()[1:] if "=" in t)


def prov_problem(prov, sha, image):
    """Why a cell's ##PROV## disagrees with its pin ("" when it agrees)."""
    if prov is None:
        return "no ##PROV## line"
    got_sha = prov.get("commit", "")
    got_digest = prov.get("image", "").rpartition("@")[2]
    want_digest = image.rpartition("@")[2]
    bad = []
    if got_sha != sha:
        bad.append(f"commit {got_sha or '-'}, not {sha}")
    if got_digest != want_digest:
        bad.append(f"image digest {got_digest or '-'}, not {want_digest}")
    return "; ".join(bad)


def cmd_collect(plan, a):
    work = work_dir(plan, a)
    log = read_log(work)
    repo = None if a.src else target(plan, a)[0]
    groups = {}
    for label, cell, arm, _wf, _inp in selected(plan, a):
        groups.setdefault((cell, arm), []).append(label)
    os.makedirs(a.out, exist_ok=True)
    written = skipped = 0
    rejected = []  # (label, run id, why)
    for (cell, arm), labels in groups.items():
        sha, image = pin(plan, arm)
        bodies = []
        for label in labels:
            rid = log.get(label)
            if a.src:
                src = os.path.join(a.src, label)
            elif rid:
                # Keyed by run id: two plans can share a label.
                src = os.path.join(work, "artifacts", str(rid))
                if results_text(src) is None:
                    try:
                        fetch(repo, rid, src)
                    except RuntimeError as e:
                        print(f"{label}: {e}", file=sys.stderr)
            else:
                src = None
            text = results_text(src) if src else None
            if not rid or text is None:
                break
            if "##RUN##" not in text and "##BENCH##" not in text:
                # A run killed at its time limit still uploads a stub with
                # only the ##PROV## line; it is a missing run, not a result.
                rejected.append((label, rid, "no result rows (a timed-out run's stub?)"))
                continue  # every label of the cell is checked
            why = prov_problem(prov_of(text), sha, image)
            if why:
                rejected.append((label, rid, why))
                continue
            bodies.append((label, rid, text))
        name = out_name(plan, cell, arm)
        if len(bodies) < len(labels):
            print(f"SKIP {name}: {len(bodies)} of {len(labels)} run(s) ready", file=sys.stderr)
            skipped += 1
            continue
        runs = " / ".join(f"{rid} ({label})" for label, rid, _ in bodies)
        with open(os.path.join(a.out, name), "w") as f:
            f.write(
                f"# {plan['generation']} campaign {name[:-4]}: {len(bodies)} run(s): {runs}; "
                f"ref {plan['ref']}; source_sha {sha}\n"
            )
            for _l, _r, text in bodies:
                f.write(text if text.endswith("\n") else text + "\n")
        written += 1
    print(f"wrote {written} file(s), skipped {skipped}", file=sys.stderr)
    if rejected:
        print("re-dispatch (each cell must run its arm's source_sha and image):", file=sys.stderr)
        for label, rid, why in rejected:
            print(f"  {label}\trun {rid}\t{why}", file=sys.stderr)
        if a.redispatch:
            write_log(work, {k: v for k, v in log.items() if k not in {r[0] for r in rejected}})
            print(
                f"dropped {len(rejected)} label(s) from {work}/runs.tsv; run dispatch again",
                file=sys.stderr,
            )
        else:
            print(
                "  (collect --redispatch drops them from runs.tsv, so dispatch sends them again)",
                file=sys.stderr,
            )
        sys.exit(1)


def cmd_verdicts(plan, a):
    parser = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bench_parse.py")
    pairs = sorted(
        {
            (cell, arm)
            for _l, cell, arm, *_ in jobs(plan)
            if arm != plan["control"] and (not a.arm or arm == a.arm)
        },
        key=lambda p: (p[1], list(plan["cells"]).index(p[0])),
    )
    for cell, arm in pairs:
        base = os.path.join(a.dir, out_name(plan, cell, plan["control"]))
        test = os.path.join(a.dir, out_name(plan, cell, arm))
        if not (os.path.exists(base) and os.path.exists(test)):
            print(
                f"{arm}\t{cell}\t-\t(missing {os.path.basename(base)} or {os.path.basename(test)})"
            )
            continue
        for proto in cell_protocols(plan, cell, arm):
            out = subprocess.run(
                [sys.executable, parser, "--ab", "--proto", proto, base, test],
                capture_output=True, text=True, check=False,
            ).stdout  # fmt: skip
            line = next(
                (ln.strip() for ln in out.splitlines() if ln.strip().startswith("paired 95% CI")),
                None,
            )
            print(f"{arm}\t{cell}\t{proto}\t{line or '(no paired result; see bench_parse.py)'}")


COMMANDS = {
    "plan": cmd_plan,
    "dispatch": cmd_dispatch,
    "status": cmd_status,
    "collect": cmd_collect,
    "verdicts": cmd_verdicts,
}


def main(argv=None):
    p = argparse.ArgumentParser(prog="ns3-campaign", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in COMMANDS:
        s = sub.add_parser(name)
        s.add_argument("plan")
        s.add_argument("--arm", help="only this arm's jobs")
        if name in ("dispatch", "status", "collect"):
            s.add_argument("--work", help="state directory (default .campaign/<generation>)")
            s.add_argument(
                "--repo",
                help="owner/name whose caller workflow runs the cells (default: plan's repo)",
            )
            s.add_argument(
                "--workflow",
                help="that caller workflow (default: plan's workflow, else benchmark-cell.yml)",
            )
        if name == "status":
            s.add_argument(
                "--brief", action="store_true", help="only the labels not yet successful"
            )
        if name == "dispatch":
            s.add_argument("--cap", type=int)
            s.add_argument(
                "--wait", action="store_true", help="sleep at the cap instead of exiting"
            )
        if name == "collect":
            s.add_argument(
                "--out", required=True, help="where the <cell>-<generation>[-<arm>].txt files go"
            )
            s.add_argument(
                "--from", dest="src", help="DIR/<label>/results.txt instead of downloading"
            )
            s.add_argument(
                "--redispatch", action="store_true", help="drop rejected labels from runs.tsv"
            )
        if name == "verdicts":
            s.add_argument("--dir", required=True, help="the directory collect wrote")
    a = p.parse_args(argv)
    COMMANDS[a.cmd](load_plan(a.plan), a)


if __name__ == "__main__":
    main()
