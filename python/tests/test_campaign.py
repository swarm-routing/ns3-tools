#!/usr/bin/env python3
# Copyright (c) 2026 Daniel Joppi (https://github.com/swarm-routing)
# SPDX-License-Identifier: MIT OR GPL-2.0-only
"""Self-test for campaign.py (#66), offline.

The plan validator gets a passing plan and one failing mutation per rule.
`stage` installs a module from a local git repository. build -> dispatch ->
status -> collect -> verdicts run against a fake GitHub (campaign.gh replaced
by a stub that keeps workflow runs and artifacts in memory and writes each
cell's results the way campaign-cell.yml does), including the throttle and a
cell that ran another image. The collected run is checked by
swarm-routing/benchmarks' tools/validate_run.py: the copy vendored in
benchmarks/ (provenance in its README), or NS3_TOOL_BENCHMARKS=<checkout>.

Run: python3 test_campaign.py     (no pytest dependency; exits non-zero on the
                                   first failure)
"""

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "ns3_tool")
EXAMPLE = os.path.join(HERE, "..", "..", "campaigns", "anthocnet-aodv-smoke.toml")


def _load(name):
    path = os.path.join(PKG, name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, PKG)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


cp = _load("campaign")
cp.POLL = 0
cp.CACHE = tempfile.mkdtemp(prefix="ns3-campaign-cache-")  # never the user's ~/.cache
REAL_IMAGE_GONE = cp.image_gone
cp.image_gone = lambda image: False  # no registry here; test_image_gone covers it

BASE = "sha256:" + "b" * 64
IMAGE = cp.IMAGE + "@sha256:" + "c" * 64
OTHER = cp.IMAGE + "@sha256:" + "d" * 64
LEAD7 = "4fe1400"  # the example plan's first module (anthocnet) commit


def check(cond, what):
    if not cond:
        sys.exit(f"FAIL: {what}")
    print(f"ok   {what}")


def run(argv):
    """main(argv) -> (exit message or None, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    code = None
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            cp.main(argv)
        except SystemExit as e:
            code = str(e.code)
    return code, out.getvalue(), err.getvalue()


def example():
    with open(EXAMPLE, "rb") as f:
        return tomllib.load(f)


def test_validate():
    check(cp.validate(example()) == [], "the committed example plan is valid")

    def fails(what, needle, change):
        plan = example()
        change(plan)
        errs = cp.validate(plan)
        check(any(needle in e for e in errs), f"validator: {what} ({needle!r} in {errs})")

    sha = "a" * 40
    fails("unknown key", "unknown key 'colour'", lambda p: p.update(colour=1))
    fails("missing seeds", "no 'seeds'", lambda p: p.pop("seeds"))
    fails("19 seeds", "at least 20", lambda p: p.update(seeds=19))
    fails("debug base", "optimized", lambda p: p.update(base="3.48-debug"))
    fails("one arm", "two distinct arms", lambda p: p.update(arms=["aodv"]))
    fails("reference off the arms", "is not one of the arms", lambda p: p.update(reference="olsr"))
    fails(
        "unpinned module",
        "40-char commit",
        lambda p: p["modules"].append("swarm-core=swarm-routing/ns3-swarm-core@main"),
    )
    fails(
        "subdir escapes",
        "leaves the repository",
        lambda p: p["modules"].append(f"x=o/r@{sha}:../etc"),
    )
    fails(
        "module twice",
        "named twice",
        lambda p: p["modules"].append(f"anthocnet=o/r@{sha}"),
    )
    fails("harness module missing", "harness module", lambda p: p.update(harness="probe"))
    fails("install of a non-module", "is not a module", lambda p: p["install"].update(ghost="make"))
    fails(
        "install with a subdir",
        "has a subdir",
        lambda p: p["install"].update({"routing-bench": "make"}),
    )
    fails(
        "reserved flag",
        "set by the driver",
        lambda p: p["cells"]["grid-rwp"].update(args="--runs=5"),
    )
    fails(
        "repeated flag",
        "--nNodes is given more than once",
        lambda p: p["cells"]["grid-rwp"].update(args="--nNodes=50 --nNodes=100"),
    )
    fails("blocks not dividing", "must divide", lambda p: p["cells"]["grid-rwp"].update(blocks=3))
    fails("cap above 20", "cap 40", lambda p: p.update(cap=40))
    fails("bad cell name", "is not [a-z0-9._-]", lambda p: p["cells"].update({"Grid": {}}))

    code, _o, _e = run(["validate", EXAMPLE])
    check(code is None, "validate exits 0 on the example")
    with tempfile.TemporaryDirectory() as d:
        bad = os.path.join(d, "bad.toml")
        with open(bad, "w") as f:
            f.write('name = "x"\n')
        code, _o, _e = run(["validate", bad])
        check(code and "invalid plan" in code, "validate exits non-zero on an invalid plan")


def test_plan_and_inputs():
    code, out, _e = run(["plan", EXAMPLE, "--work", tempfile.mkdtemp()])
    check(code is None, "plan runs offline")
    labels = [
        ln.split("\t")[0] for ln in out.splitlines() if ln.startswith("anthocnet-aodv-smoke/")
    ]
    check(
        labels
        == [
            "anthocnet-aodv-smoke/grid-rwp",
            "anthocnet-aodv-smoke/static-mesh-s1",
            "anthocnet-aodv-smoke/static-mesh-s11",
        ],
        f"plan: one job per cell, one per seed block ({labels})",
    )
    check("--runs=10 --firstRun=11" in out, "plan: the second block starts at seed 11")
    check(
        f"ns3-tools-dev:{cp.modules(example())[0]['commit'][:7]}-{{base7}}-optimized" in out,
        "plan: names the image it needs",
    )
    code, out, _e = run(["image-inputs", EXAMPLE])
    check(
        code and "campaigns/<name>.toml" in code,
        "image-inputs: refuses a path not under campaigns/",
    )
    old = os.getcwd()
    os.chdir(os.path.join(HERE, "..", ".."))
    try:
        code, out, _e = run(["image-inputs", "campaigns/anthocnet-aodv-smoke.toml"])
    finally:
        os.chdir(old)
    check(code is None, "image-inputs runs")
    check(
        "dependencies<<EOF_DEPS\nrouting-bench=swarm-routing/ns3-tools@" in out
        and ":ns3/routing-bench\nEOF_DEPS" in out,
        "image-inputs: the plain module is a dependency, with its subdir",
    )
    check(
        "modules=anthocnet=.stage/ns3/contrib/anthocnet\n" in out,
        "image-inputs: the installed one is staged",
    )
    check(
        "ns3-version=3.48\n" in out and re.search(r"\ntag=[0-9a-f]{7}-\{base7\}\n", out),
        "image-inputs: version and tag",
    )
    lead = cp.modules(example())[0]
    check(
        f"\ntag={lead['commit'][:7]}-{{base7}}\n" in out and lead["name"] == "anthocnet",
        "image-inputs: the tag names the first module's commit",
    )
    check(
        re.search(r"\nexpect=anthocnet@[0-9a-f]{40},routing-bench@[0-9a-f]{40}\n", out),
        "image-inputs: the modules the image must hold",
    )
    check(
        cp.parameters("--nNodes=50 --speed=2.5 --mobility=rwp --diag")
        == {"nNodes": 50, "speed": 2.5, "mobility": "rwp", "diag": True},
        "parameters: typed flags",
    )


def test_stage():
    with tempfile.TemporaryDirectory() as d:
        repo = os.path.join(d, "git", "o", "proto")
        os.makedirs(os.path.join(repo, "mod"))
        with open(os.path.join(repo, "mod", "CMakeLists.txt"), "w") as f:
            f.write("build_lib(LIBNAME proto)\n")
        git = ["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run(["git", "init", "-q", repo], check=True)
        subprocess.run([*git, "add", "-A"], check=True)
        subprocess.run([*git, "commit", "-qm", "m"], check=True)
        sha = subprocess.run(
            [*git, "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout.strip()
        plan = example()
        plan["modules"].append(f"proto=o/proto@{sha}")
        plan["install"] = {"proto": 'cp -r mod "$NS3DIR/contrib/proto"'}
        path = os.path.join(d, "plan.toml")
        with open(path, "w") as f:
            f.write(toml(plan))
        cp.GIT = os.path.join(d, "git")
        code, out, _e = run(["stage", path, "--root", os.path.join(d, "stage")])
        check(code is None, f"stage runs ({code})")
        marker = os.path.join(d, "stage", "ns3", "contrib", "proto", ".swarm-module")
        with open(marker) as f:
            check(f.read() == f"proto=o/proto@{sha}\n", "stage: the module records its commit")
        plan["install"] = {"proto": "true"}
        with open(path, "w") as f:
            f.write(toml(plan))
        code, _o, _e = run(["stage", path, "--root", os.path.join(d, "stage2")])
        check(code and "left no" in code, "stage: an install that leaves no module fails")


def toml(plan):
    """The few shapes a plan uses, as TOML (tomllib cannot write)."""

    def val(v):
        if isinstance(v, str):
            return json.dumps(v)
        if isinstance(v, list):
            return "[" + ", ".join(val(x) for x in v) + "]"
        return str(v).lower() if isinstance(v, bool) else str(v)

    lines = [f"{k} = {val(v)}" for k, v in plan.items() if not isinstance(v, dict)]
    for k, v in plan.items():
        if isinstance(v, dict) and k != "cells":
            lines.append(f"[{k}]")
            lines += [f'"{kk}" = {val(vv)}' for kk, vv in v.items()]
    for name, cell in plan.get("cells", {}).items():
        lines.append(f'[cells."{name}"]')
        lines += [f"{kk} = {val(vv)}" for kk, vv in cell.items()]
    return "\n".join(lines) + "\n"


class FakeGitHub:
    """The endpoints campaign.py calls, over in-memory runs and artifacts."""

    def __init__(self, plan, image=IMAGE, active=0, wrong=(), failed=(), partial=()):
        self.plan, self.image, self.active, self.wrong = plan, image, active, set(wrong)
        self.failed, self.partial = tuple(failed), tuple(partial)
        self.runs, self.artifacts, self.dispatched = {}, {}, []
        self.next_id = 1000
        self.expired = set()  # run ids whose artifact has expired

    def zip_of(self, files):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for name, text in files.items():
                z.writestr(name, text)
        return buf.getvalue()

    def results(self, inputs):
        """What campaign-cell.yml uploads: the harness output, ##ARGS##, ##PROV##."""
        args = cp.parameters(inputs["args"])
        first, n = args["firstRun"], args["runs"]
        rng = random.Random(first)
        out = [f"##CONFIG## scenario=paper runs={n} firstRun={first}"]
        for arm in args["protocols"].split(","):
            # A partial run (a crash or timeout): aodv lost its last seed.
            last = first + n - (arm == "aodv" and inputs["label"].endswith(self.partial or ("\0",)))
            for s in range(first, last):
                pdr = (90.0 if arm == "anthocnet" else 80.0) + rng.uniform(-2, 2)
                d99 = 200.0 + rng.uniform(-20, 20)
                nrl = 5.0 + rng.uniform(-0.5, 0.5)
                out.append(
                    f"##RUN## {s} {arm} {pdr:.1f} 18.4 {d99:.1f} 12.51 {nrl:.2f} 27.79 2.5 120.5 7.7219"
                )
        mods = ",".join(f"{m['name']}@{m['commit']}" for m in cp.modules(self.plan))
        image = OTHER if inputs["label"].endswith(tuple(self.wrong)) else inputs["image"]
        out.append(f"##ARGS## {inputs['program']} {inputs['args']}")
        key = f" key={inputs['key']}" if inputs.get("key") else ""
        out.append(
            f"##PROV## base=ghcr.io/swarm-routing/ns3-dev:3.48-optimized-1a2b3c4@{BASE} "
            f"image={cp.IMAGE}:{LEAD7}-bbbbbbb-optimized@{image.split('@')[1]} "
            f"modules={mods} run_id=1 attempt=1{key}"
        )
        return "\n".join(out) + "\n"

    def __call__(self, *args, stdin=None, binary=False):
        path = args[2] if args[0] == "-X" else args[0]
        m = re.match(r"repos/[^/]+/[^/]+/actions/workflows/([^/]+)/dispatches$", path)
        if m:
            body = json.loads(stdin)
            wf, inputs = m[1], body["inputs"]
            rid = self.next_id = self.next_id + 1
            title = inputs["label"] if wf == cp.CELL_WORKFLOW else f"image {inputs['plan']}"
            self.runs[rid] = {
                "id": rid,
                "display_title": title,
                "wf": wf,
                "status": "completed",
                "conclusion": "failure"
                if self.failed and title.endswith(self.failed)
                else "success",
            }
            self.dispatched.append((wf, body))
            if wf == cp.IMAGE_WORKFLOW:
                files = {
                    "campaign-image.json": json.dumps(
                        {"image": self.image, "tag": f"{LEAD7}-bbbbbbb-optimized"}
                    )
                }
                name = "campaign-image"
            else:
                files = {"results.txt": self.results(inputs)}
                name = "campaign-cell" + (f"-{inputs['key']}" if inputs.get("key") else "")
            self.artifacts[rid] = (name, self.zip_of(files))
            return ""
        m = re.match(r"repos/[^/]+/[^/]+/actions/workflows/([^/]+)/runs\?(.*)$", path)
        if m:
            if "status=" in m[2]:
                return str(self.active if "queued" in m[2] else 0)
            runs = [r for r in self.runs.values() if r["wf"] == m[1]]
            return json.dumps({"workflow_runs": runs})
        m = re.match(r"repos/[^/]+/[^/]+/actions/runs/(\d+)/artifacts$", path)
        if m:
            rid = int(m[1])
            arts = [{"id": rid, "name": self.artifacts[rid][0]}] if rid in self.artifacts else []
            return json.dumps({"artifacts": arts})
        m = re.match(r"repos/[^/]+/[^/]+/actions/artifacts\?name=([^&]+)", path)
        if m:
            arts = [
                {"id": rid, "name": name, "expired": rid in self.expired,
                 "workflow_run": {"id": rid}}
                for rid, (name, _z) in self.artifacts.items()
                if name == m[1]
            ]  # fmt: skip
            return json.dumps({"total_count": len(arts), "artifacts": arts})
        m = re.match(r"repos/[^/]+/[^/]+/actions/artifacts/(\d+)/zip$", path)
        if m:
            return self.artifacts[int(m[1])][1]
        m = re.match(r"repos/[^/]+/[^/]+/actions/runs/(\d+)$", path)
        if m:
            return json.dumps(self.runs[int(m[1])])
        raise AssertionError(f"unexpected gh api {args}")


def test_campaign():
    root = os.path.join(HERE, "..", "..")
    old = os.getcwd()
    os.chdir(root)
    plan_path = "campaigns/anthocnet-aodv-smoke.toml"
    plan = cp.load_plan(plan_path)
    try:
        with tempfile.TemporaryDirectory() as d:
            work, runs_root = os.path.join(d, "work"), os.path.join(d, "runs")
            fake = FakeGitHub(plan)
            cp.gh = fake
            code, _o, _e = run(["dispatch", plan_path, "--work", work])
            check(code and "build" in code, "dispatch refuses to run before the image is built")
            code, out, _e = run(["build", plan_path, "--work", work])
            check(code is None and IMAGE in out, f"build: records the image by digest ({code})")
            check(
                fake.dispatched[0][1]["inputs"] == {"plan": plan_path}, "build: dispatches the plan"
            )
            code, out, _e = run(["build", plan_path, "--work", work])
            check(len(fake.dispatched) == 1, "build: a second call reuses the recorded image")

            fake.active = 12
            code, _o, err = run(["dispatch", plan_path, "--work", work])
            check(
                len(fake.dispatched) == 1 and "cap 12 reached" in err, "dispatch: holds at the cap"
            )
            fake.active = 0
            code, out, _e = run(["dispatch", plan_path, "--work", work])
            cells = [b for wf, b in fake.dispatched if wf == cp.CELL_WORKFLOW]
            check(code is None and len(cells) == 3, "dispatch: one run per job")
            check(
                all(b["inputs"]["image"] == IMAGE for b in cells),
                "dispatch: every cell runs the image by digest",
            )
            check(
                cells[2]["inputs"]["args"].endswith(
                    "--protocols=anthocnet,aodv --runs=10 --firstRun=11"
                ),
                "dispatch: the harness flags carry arms and the seed block",
            )
            code, out, _e = run(["dispatch", plan_path, "--work", work])
            check(len(fake.dispatched) == 4, "dispatch: resumable, nothing dispatched twice")
            code, out, err = run(["status", plan_path, "--work", work])
            check("3 success" in err, "status: every label successful")

            code, _o, err = run(["collect", plan_path, "--work", work, "--out", runs_root])
            check(code is None, f"collect runs ({code})")
            date = json.load(open(os.path.join(work, "state.json")))["date"]
            run_dir = os.path.join(runs_root, f"{date}-anthocnet-aodv-smoke")
            m = json.load(open(os.path.join(run_dir, "manifest.json")))
            check(m["seeds"] == list(range(1, 21)), "manifest: 20 seeds")
            check(
                m["campaign_image"]["digest"] == IMAGE.split("@")[1],
                "manifest: the campaign image digest",
            )
            check(
                m["campaign_image"]["ref"] == f"{cp.IMAGE}:{LEAD7}-bbbbbbb-optimized",
                "manifest: the pushed tag, -optimized included",
            )
            check(
                m["base_image"]["digest"] == BASE and m["base_image"]["ns3_version"] == "3.48",
                "manifest: the base",
            )
            check(
                [x["name"] for x in m["modules"]] == ["anthocnet", "routing-bench"],
                "manifest: every module",
            )
            cmds = m["commands"]
            check(len(cmds) == 1 + 3 * 3, "manifest: the image dispatch, then 3 commands per job")
            sent = [json.loads(shlex.split(c)[-1]) for c in cmds[0:2]]
            check(
                sent == [fake.dispatched[0][1], fake.dispatched[1][1]],
                "manifest: the dispatch commands send exactly the requests made",
            )
            other = os.path.join(d, "copy.toml")
            shutil.copyfile(plan_path, other)
            run(["collect", other, "--work", work, "--out", runs_root + "-copy"])
            m2 = json.load(
                open(os.path.join(runs_root + "-copy", os.path.basename(run_dir), "manifest.json"))
            )
            check(
                m2["commands"][0] == cmds[0],
                "manifest: the image dispatch names the plan path build sent, whatever path collect reads",
            )
            check(
                shlex.split(cmds[2])[:3] == ["docker", "run", "--rm"]
                and IMAGE in cmds[2]
                and shlex.split(cmds[2])[-1] == './ns3 run --no-build "$PROGRAM $ARGS"',
                "manifest: the container command runs the image by digest, no rebuild",
            )
            check(
                cmds[3].startswith("routing-compare ") and cmds[3].endswith("--firstRun=1"),
                "manifest: the harness line",
            )
            check(m["scenarios"][0]["parameters"]["nNodes"] == 50, "manifest: cell parameters")
            for f in m["files"]:
                with open(os.path.join(run_dir, f["path"]), "rb") as fh:
                    check(
                        hashlib.sha256(fh.read()).hexdigest() == f["sha256"],
                        f"manifest: sha256 of {f['path']}",
                    )
            with open(os.path.join(run_dir, "cells", "static-mesh.txt")) as f:
                runs = cp.bench_parse.parse_runs(f.read())
            check(
                sorted(runs["aodv"]) == list(range(1, 21)),
                "collect: the seed blocks joined in one cell",
            )
            bench = os.environ.get("NS3_TOOL_BENCHMARKS") or os.path.join(HERE, "benchmarks")
            p = subprocess.run(
                [
                    sys.executable,
                    os.path.join(bench, "tools", "validate_run.py"),
                    "check",
                    run_dir,
                ],
                capture_output=True,
                text=True,
            )
            check(p.returncode == 0, f"benchmarks validate_run.py passes:\n{p.stdout}{p.stderr}")

            code, out, _e = run(["verdicts", plan_path, "--dir", run_dir])
            lines = out.splitlines()
            check(
                len(lines) == 2
                and all("anthocnet - aodv" in ln and "PAIRED-IMPROVED" in ln for ln in lines),
                f"verdicts: paired per-seed deltas per cell ({lines})",
            )

            # A cell that ran another image is refused.
            work2 = os.path.join(d, "work2")
            cp.gh = FakeGitHub(plan, wrong=["static-mesh-s11"])
            run(["build", plan_path, "--work", work2])
            run(["dispatch", plan_path, "--work", work2, "--no-reuse"])
            code, _o, _e = run(["collect", plan_path, "--work", work2, "--out", runs_root + "2"])
            check(
                code and "not the campaign image" in code,
                "collect: refuses a cell from another image",
            )
            for kind, needle in (
                ("failed", "is failure, not success"),
                (
                    "partial",
                    "arm aodv lacks seeds [20] of its block 11..20",
                ),
            ):
                work4 = os.path.join(d, f"work-{kind}")
                label = "static-mesh-s1" if kind == "failed" else "static-mesh-s11"
                cp.gh = FakeGitHub(plan, **{kind: [label]})
                run(["build", plan_path, "--work", work4])
                run(["dispatch", plan_path, "--work", work4, "--no-reuse"])
                code, _o, _e = run(
                    ["collect", plan_path, "--work", work4, "--out", work4 + "-runs"]
                )
                check(
                    code and needle in code and f"{label}: run" in code,
                    f"collect: refuses a {kind} run, naming the cell ({code})",
                )
                check(
                    not os.path.exists(work4 + "-runs"),
                    f"collect: writes nothing after a {kind} run",
                )
            bad = copy.deepcopy(plan)
            bad["modules"][0] = bad["modules"][0][:-1] + "0"
            cp.gh = FakeGitHub(bad)
            work3 = os.path.join(d, "work3")
            run(["build", plan_path, "--work", work3])
            run(["dispatch", plan_path, "--work", work3, "--no-reuse"])
            code, _o, _e = run(["collect", plan_path, "--work", work3, "--out", runs_root + "3"])
            check(
                code and "has modules" in code, "collect: refuses a cell built from other commits"
            )
    finally:
        os.chdir(old)


def test_cell_key():
    mods = ["anthocnet@" + "1" * 40, "routing-bench@" + "2" * 40]
    args = "--scenario=paper --nNodes=50 --diag --protocols=anthocnet,aodv --runs=10 --firstRun=11"
    k = cp.cell_key(IMAGE, mods, "routing-compare", args)
    check(re.fullmatch(r"[0-9a-f]{64}", k), "cell_key: a 64-hex sha256")
    toks = args.split()
    for seed in range(5):
        random.Random(seed).shuffle(toks)
        same = cp.cell_key(IMAGE, list(reversed(mods)), "routing-compare", " ".join(toks))
        check(same == k, f"cell_key: flags and modules in any order give the same key ({seed})")
    check(
        cp.cell_key(cp.IMAGE + ":tag@sha256:" + "c" * 64, mods, "routing-compare", args) == k,
        "cell_key: only the image digest counts, not its tag",
    )
    other_mods = [mods[0], "routing-bench@" + "3" * 40]
    changed = {
        "the image digest": (OTHER, mods, "routing-compare", args),
        "a module commit": (IMAGE, other_mods, "routing-compare", args),
        "the module list": (IMAGE, mods[:1], "routing-compare", args),
        "the program": (IMAGE, mods, "isl-grid", args),
        "a flag value": (IMAGE, mods, "routing-compare", args.replace("50", "51")),
        "a flag added": (IMAGE, mods, "routing-compare", args + " --pause=0"),
        "the arms": (IMAGE, mods, "routing-compare", args.replace(",aodv", "")),
        "the arm order": (
            IMAGE,
            mods,
            "routing-compare",
            args.replace("anthocnet,aodv", "aodv,anthocnet"),
        ),
        "the seed block": (IMAGE, mods, "routing-compare", args.replace("=11", "=1")),
        "the seed count": (IMAGE, mods, "routing-compare", args.replace("--runs=10", "--runs=9")),
    }
    for what, inputs in changed.items():
        check(cp.cell_key(*inputs) != k, f"cell_key: changing {what} changes the key")
    real = cp.MARKERS_VERSION
    try:
        cp.MARKERS_VERSION = real + 1
        check(
            cp.cell_key(IMAGE, mods, "routing-compare", args) != k,
            "cell_key: the marker contract version changes the key",
        )
    finally:
        cp.MARKERS_VERSION = real


def cell_files(run_dir):
    """{cell: text without collect's '#' header line} of a collected run."""
    out = {}
    for name in sorted(os.listdir(os.path.join(run_dir, "cells"))):
        with open(os.path.join(run_dir, "cells", name)) as f:
            out[name] = f.read().split("\n", 1)[1]
    return out


def only_run(parent):
    return os.path.join(parent, os.listdir(parent)[0])


def test_reuse():
    """#94: dispatch reuses a cell found by its key; status, collect and
    verdicts read it as any other; a mismatch is an error."""
    root = os.path.join(HERE, "..", "..")
    old, cache = os.getcwd(), cp.CACHE
    os.chdir(root)
    plan_path = "campaigns/anthocnet-aodv-smoke.toml"
    plan = cp.load_plan(plan_path)

    def fresh(d, name, fake):
        """A new work directory with the image built in it."""
        cp.gh = fake
        work = os.path.join(d, name)
        run(["build", plan_path, "--work", work])
        return work

    def sent(fake):
        return [b for wf, b in fake.dispatched if wf == cp.CELL_WORKFLOW]

    def results(fake, rid):
        with zipfile.ZipFile(io.BytesIO(fake.artifacts[rid][1])) as z:
            return z.read("results.txt").decode()

    try:
        with tempfile.TemporaryDirectory() as d:
            cp.CACHE = os.path.join(d, "cache")
            fake = FakeGitHub(plan)
            work = fresh(d, "a", fake)
            code, out, err = run(["plan", plan_path, "--work", work])
            keys = [
                ln.split("\t")[1]
                for ln in out.splitlines()
                if ln.startswith("anthocnet-aodv-smoke/")
            ]
            check(
                code is None
                and all(re.fullmatch(r"[0-9a-f]{64}", k) for k in keys)
                and len(set(keys)) == 3,
                "plan: prints each cell's key once the image is built",
            )
            check("3 cells, 0 reusable, 3 to run" in err, f"plan: nothing reusable yet ({err})")
            code, _o, _e = run(["dispatch", plan_path, "--work", work])
            check(
                code is None
                and len(sent(fake)) == 3
                and [b["inputs"]["key"] for b in sent(fake)] == keys,
                "dispatch: found nowhere, so every cell runs, each with its key",
            )
            rid = max(fake.artifacts)
            check(
                fake.artifacts[rid][0] == f"campaign-cell-{keys[-1]}"
                and f" key={keys[-1]}\n" in results(fake, rid),
                "the cell's artifact and ##PROV## carry its key",
            )
            code, _o, _e = run(["collect", plan_path, "--work", work, "--out", d + "/runs-a"])
            check(code is None, f"collect: a keyed run ({code})")
            run_a = only_run(d + "/runs-a")
            _c, verdicts_a, _e = run(["verdicts", plan_path, "--dir", run_a])

            # Found in the workflow artifacts (the cache emptied).
            shutil.rmtree(cp.CACHE, ignore_errors=True)
            work_b = fresh(d, "b", fake)
            n = len(sent(fake))
            code, _o, err = run(["plan", plan_path, "--work", work_b])
            check(
                "3 cells, 3 reusable, 0 to run" in err, f"plan: counts the reusable cells ({err})"
            )
            code, out, err = run(["dispatch", plan_path, "--work", work_b])
            check(
                code is None
                and len(sent(fake)) == n
                and out.count("\treused run:") == 3
                and "(3 reused)" in err,
                f"dispatch: found in the workflow artifacts, nothing dispatched ({out})",
            )
            code, _o, err = run(["status", plan_path, "--work", work_b])
            check(
                "3 reused" in err and "3 cells, 3 reused, 0 run here" in err,
                f"status: reused cells count as done ({err})",
            )
            code, _o, _e = run(["collect", plan_path, "--work", work_b, "--out", d + "/runs-b"])
            check(code is None, f"collect: reads the reused cells ({code})")
            run_b = only_run(d + "/runs-b")
            check(
                cell_files(run_b) == cell_files(run_a), "collect: a reused cell's text is the run's"
            )
            _c, verdicts_b, _e = run(["verdicts", plan_path, "--dir", run_b])
            check(
                verdicts_a and verdicts_b == verdicts_a,
                "verdicts: byte-identical, reused or re-run",
            )
            bench = os.environ.get("NS3_TOOL_BENCHMARKS") or os.path.join(HERE, "benchmarks")
            p = subprocess.run(
                [sys.executable, os.path.join(bench, "tools", "validate_run.py"), "check", run_b],
                capture_output=True,
                text=True,
            )
            check(
                p.returncode == 0,
                f"benchmarks validate_run.py passes a run of keyed, reused cells:\n{p.stdout}{p.stderr}",
            )

            # The same cells with their flags in another order: the key sorts
            # them, so the earlier cells are reused, and the manifest names the
            # command each cell ran (its own ##ARGS##), not this plan's order.
            with open(plan_path) as f:
                toml = f.read()
            for spec in plan["cells"].values():
                toml = toml.replace(spec["args"], " ".join(reversed(spec["args"].split())))
            os.makedirs(os.path.join(d, "campaigns"))
            with open(os.path.join(d, plan_path), "w") as f:
                f.write(toml)
            os.chdir(d)  # build takes campaigns/<name>.toml, relative
            plan_r = plan_path
            cp.gh = fake
            work_r = os.path.join(d, "r")
            run(["build", plan_r, "--work", work_r])
            n = len(sent(fake))
            code, out, _e = run(["dispatch", plan_r, "--work", work_r])
            check(
                code is None and len(sent(fake)) == n and out.count("\treused run:") == 3,
                f"dispatch: flags in another order, same keys, every cell reused ({out})",
            )
            code, _o, _e = run(["collect", plan_r, "--work", work_r, "--out", d + "/runs-r"])
            run_r = only_run(d + "/runs-r")
            with open(os.path.join(run_r, "manifest.json")) as f:
                commands = json.load(f)["commands"]
            ran = [
                ln.removeprefix("##ARGS## ")
                for name in sorted(os.listdir(os.path.join(run_r, "cells")))
                for ln in open(os.path.join(run_r, "cells", name)).read().splitlines()
                if ln.startswith("##ARGS## ")
            ]
            check(
                code is None and ran and all(r in commands for r in ran),
                f"collect: the manifest names each reused cell's own command ({code})",
            )
            p = subprocess.run(
                [sys.executable, os.path.join(bench, "tools", "validate_run.py"), "check", run_r],
                capture_output=True,
                text=True,
            )
            check(
                p.returncode == 0,
                f"benchmarks validate_run.py passes reused cells whose flags the plan reorders:\n{p.stdout}{p.stderr}",
            )
            os.chdir(root)

            # The local cache: artifacts expired, no benchmarks, still reused, offline.
            fake.expired = set(fake.artifacts)
            work_c = fresh(d, "c", fake)
            code, out, _e = run(["dispatch", plan_path, "--work", work_c])
            check(
                code is None and out.count("\treused run:") == 3 and len(sent(fake)) == n,
                "dispatch: found in the local cache",
            )

            # Expired and not cached: not found, so the cells run.
            shutil.rmtree(cp.CACHE, ignore_errors=True)
            work_d = fresh(d, "d", fake)
            code, _o, err = run(["dispatch", plan_path, "--work", work_d])
            check(
                code is None and len(sent(fake)) == n + 3 and "artifact expired" in err,
                "dispatch: an expired artifact is not reused; the cell runs",
            )

            # A swarm-routing/benchmarks checkout holding the run collected above.
            shutil.rmtree(cp.CACHE, ignore_errors=True)
            bench_root = os.path.join(d, "benchmarks")
            shutil.copytree(d + "/runs-a", os.path.join(bench_root, "runs"))
            fake.expired = set(fake.artifacts)
            work_e = fresh(d, "e", fake)
            n = len(sent(fake))
            code, out, _e = run(
                ["dispatch", plan_path, "--work", work_e, "--benchmarks", bench_root]
            )
            check(
                code is None and out.count("\treused benchmarks:") == 3 and len(sent(fake)) == n,
                f"dispatch: found in a benchmarks run ({code})",
            )
            shutil.rmtree(cp.CACHE, ignore_errors=True)  # collect reads the benchmarks file again
            code, _o, _e = run(["collect", plan_path, "--work", work_e, "--out", d + "/runs-e"])
            _c, verdicts_e, _e = run(["verdicts", plan_path, "--dir", only_run(d + "/runs-e")])
            check(
                code is None and verdicts_e == verdicts_a,
                f"verdicts: byte-identical from a benchmarks run ({code})",
            )
            shutil.rmtree(cp.CACHE, ignore_errors=True)
            cell = os.path.join(
                bench_root, "runs", os.path.basename(run_a), "cells", "grid-rwp.txt"
            )
            with open(cell, "a") as f:
                f.write("# edited\n")
            code, _o, _e = run(
                ["dispatch", plan_path, "--work", fresh(d, "f", fake), "--benchmarks", bench_root]
            )
            check(
                code and "sha256 is not its manifest entry" in code,
                f"dispatch: a benchmarks cell that is not its manifest's is an error ({code})",
            )

            # A mismatched ##PROV## under the right artifact name: an error, never a re-run.
            shutil.rmtree(cp.CACHE, ignore_errors=True)
            fake.expired = set()
            rid = max(r for r, (nm, _z) in fake.artifacts.items() if nm.endswith(keys[0]))
            good = results(fake, rid)
            name = fake.artifacts[rid][0]
            for what, bad, needle in (
                (
                    "modules",
                    re.sub(r"modules=\S+", "modules=anthocnet@" + "9" * 40, good),
                    "has modules",
                ),
                ("key", good.replace(f"key={keys[0]}", "key=" + "0" * 64), "key=" + "0" * 64),
                ("seeds", good.replace("--firstRun=1", "--firstRun=2"), "recompute to key"),
            ):
                fake.artifacts[rid] = (name, fake.zip_of({"results.txt": bad}))
                work_g = os.path.join(d, f"g-{what}")
                fresh(d, f"g-{what}", fake)
                n = len(sent(fake))
                code, _o, _e = run(["dispatch", plan_path, "--work", work_g])
                check(
                    code and code.startswith("reuse:") and needle in code and len(sent(fake)) == n,
                    f"dispatch: a reused cell whose ##ARGS##/##PROV## {what} disagrees stops dispatch",
                )
            fake.artifacts[rid] = (name, fake.zip_of({"results.txt": good}))

            # --no-reuse runs every cell; plan counts nothing.
            work_h = fresh(d, "h", fake)
            n = len(sent(fake))
            code, _o, _e = run(["dispatch", plan_path, "--work", work_h, "--no-reuse"])
            check(code is None and len(sent(fake)) == n + 3, "dispatch --no-reuse: every cell runs")
            code, _o, err = run(["plan", plan_path, "--work", work_h, "--no-reuse"])
            check(
                "3 cells, 0 reusable, 3 to run" in err, "plan --no-reuse: counts nothing reusable"
            )
            # --no-reuse in a work directory whose cells were reused: they run,
            # and status and collect then read the runs.
            n = len(sent(fake))
            code, _o, _e = run(["dispatch", plan_path, "--work", work_b, "--no-reuse"])
            check(
                code is None and len(sent(fake)) == n + 3, "dispatch --no-reuse: reused labels run"
            )
            code, _o, err = run(["status", plan_path, "--work", work_b])
            check("3 success" in err and "0 reused" in err, f"status: the new runs win ({err})")
            # A dispatched cell's artifact whose ##ARGS## no longer match its key.
            rid = max(fake.artifacts)
            bad = results(fake, rid).replace("--diag", "--diag --pause=1", 1)
            fake.artifacts[rid] = (fake.artifacts[rid][0], fake.zip_of({"results.txt": bad}))
            shutil.rmtree(os.path.join(work_b, "artifacts"), ignore_errors=True)
            code, _o, _e = run(["collect", plan_path, "--work", work_b, "--out", d + "/runs-bb"])
            check(
                code and "recompute to key" in code,
                f"collect: a keyed cell gets the key checks too ({code})",
            )
            # collect reads a reused benchmarks cell from the file recorded at
            # dispatch (edited above, so its sha256 fails), not from another
            # run in the checkout that names the same key.
            shutil.copytree(
                os.path.join(d, "runs-a", os.path.basename(run_a)),
                os.path.join(bench_root, "runs", "0000-copy"),
            )
            shutil.rmtree(cp.CACHE, ignore_errors=True)
            code, _o, _e = run(["collect", plan_path, "--work", work_e, "--out", d + "/runs-ee"])
            check(
                code
                and "sha256 is not its manifest entry" in code
                and os.path.basename(run_a) in code,
                f"collect: reads the recorded benchmarks file, no key search ({code})",
            )

            # The cache keeps to its bound, the least recently used out first.
            cp.CACHE = os.path.join(d, "small")
            real = cp.CACHE_BYTES
            try:
                cp.CACHE_BYTES = 250
                for i in range(4):
                    cp.cache_put(f"{i:064x}", "x" * 100, "run:1")
                    os.utime(os.path.join(cp.CACHE, f"{i:064x}"), (1000 + i, 1000 + i))
                cp.cache_put("f" * 64, "y" * 100, "run:2")
                left = sorted(os.listdir(cp.CACHE))
                check(left == [f"{3:064x}", "f" * 64], f"cache: trimmed to its bound, LRU ({left})")
                check(cp.cache_get("f" * 64) == ("y" * 100, "run:2"), "cache: text and source")
            finally:
                cp.CACHE_BYTES = real
    finally:
        cp.CACHE = cache
        os.chdir(old)


def test_image_gone():
    """#174: a campaign image the registry no longer has (HTTP 404) stops
    dispatch before any cell; one it has, or a registry it cannot read, does not."""
    import urllib.error

    class Answer:
        def read(self, *_a):
            return b'{"token": "t"}'

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def close(self):
            pass

    def registry(head):
        def urlopen(req, timeout=None):
            if isinstance(req, str) or head is None:  # the token, or a manifest found
                return Answer()
            raise head

        return urlopen

    def http(code):
        return urllib.error.HTTPError("https://ghcr.io/v2/x", code, "x", {}, None)

    real = cp.urllib.request.urlopen
    try:
        for head, want, what in (
            (None, False, "the digest is there"),
            (http(404), True, "the registry says 404"),
            (http(403), False, "403: not readable, so not known gone"),
            (urllib.error.URLError("offline"), False, "the registry cannot be reached"),
        ):
            cp.urllib.request.urlopen = registry(head)
            check(REAL_IMAGE_GONE(IMAGE) is want, f"image_gone: {what}")
    finally:
        cp.urllib.request.urlopen = real

    root = os.path.join(HERE, "..", "..")
    old = os.getcwd()
    os.chdir(root)
    plan_path = "campaigns/anthocnet-aodv-smoke.toml"
    try:
        with tempfile.TemporaryDirectory() as d:
            fake = FakeGitHub(cp.load_plan(plan_path))
            cp.gh = fake
            work = os.path.join(d, "w")
            run(["build", plan_path, "--work", work])
            cp.image_gone = lambda image: True
            code, _o, _e = run(["dispatch", plan_path, "--work", work, "--no-reuse"])
            cells = [b for wf, b in fake.dispatched if wf == cp.CELL_WORKFLOW]
            check(
                code and "is gone from the registry" in code and "new --work" in code and not cells,
                f"dispatch: a gone image stops it before any cell, with the way out ({code})",
            )
    finally:
        cp.image_gone = lambda image: False
        os.chdir(old)


if __name__ == "__main__":
    test_validate()
    test_plan_and_inputs()
    test_stage()
    test_campaign()
    test_cell_key()
    test_reuse()
    test_image_gone()
    print("test_campaign: all ok")
