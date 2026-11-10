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
        f"ns3-tools-ns3-campaign:{cp.modules(example())[0]['commit'][:7]}-{{base7}}-optimized"
        in out,
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
        out.append(
            f"##PROV## base=ghcr.io/swarm-routing/ns3-dev:3.48-optimized-1a2b3c4@{BASE} "
            f"image={cp.IMAGE}:{LEAD7}-bbbbbbb-optimized@{image.split('@')[1]} "
            f"modules={mods} run_id=1 attempt=1"
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
                name = "campaign-cell"
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
            run(["dispatch", plan_path, "--work", work2])
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
                run(["dispatch", plan_path, "--work", work4])
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
            run(["dispatch", plan_path, "--work", work3])
            code, _o, _e = run(["collect", plan_path, "--work", work3, "--out", runs_root + "3"])
            check(
                code and "has modules" in code, "collect: refuses a cell built from other commits"
            )
    finally:
        os.chdir(old)


if __name__ == "__main__":
    test_validate()
    test_plan_and_inputs()
    test_stage()
    test_campaign()
    print("test_campaign: all ok")
