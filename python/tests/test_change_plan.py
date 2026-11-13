#!/usr/bin/env python3
# Copyright (c) 2026 Daniel Joppi (https://github.com/swarm-routing)
# SPDX-License-Identifier: MIT OR GPL-2.0-only
"""Self-test for change_plan.py (#81), offline: AntHocNet's test_campaign.py,
ported, plus the pins.

GitHub is replaced by a fake `gh` that records dispatches, names each new run
by its run-name and reports a configurable number of active runs. dispatch
passes source_sha and the image to the reusable cell; collect rejects a cell
whose ##PROV## commit or image digest is not its arm's pin and lists it for
re-dispatch. Verdicts are checked byte for byte against AntHocNet's script's
output on the fixture cells (recorded below); NS3_TOOL_ANTHOCNET=<checkout of
AntHocNet> also runs AntHocNet's tools/bench/campaign.py verdicts on every
committed plan against its results/cells/ and compares (needs PyYAML).

Run: python3 test_change_plan.py   (no pytest dependency; exits non-zero on the
                                    first failure)
"""

import argparse
import base64
import contextlib
import glob
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.join(HERE, "..", "ns3_tool")
CELLS = os.path.join(HERE, "fixtures", "cells")


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


cp = _load("change_plan")
sys.path.insert(0, PKG)  # change_plan imports campaign.py (cell_key) when it needs it
cp.POLL = 0
cp.time.sleep = lambda _s: None
# The registry, for `plan`'s ns-3 column (#89): image -> ns-3 release, offline.
REGISTRY = {}


def fake_registry(image):
    if image not in REGISTRY:
        raise OSError(f"no such image {image}")
    return {"Labels": {cp.NS3_LABEL: f"ghcr.io/swarm-routing/ns3-dev:{REGISTRY[image]}-optimized"}}


cp.registry_config = fake_registry

CASES = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn

    return deco


def expect(cond, name, detail):
    if not cond:
        print(f"FAIL {name}: {detail}")
        sys.exit(1)


SHA = "a" * 40
SHA2 = "e" * 40
IMAGE = "ghcr.io/swarm-routing/anthocnet-ns3-campaign@sha256:" + "b" * 64
IMAGE2 = "ghcr.io/swarm-routing/anthocnet-ns3-campaign@sha256:" + "f" * 64

PLAN = {
    "generation": "vtest", "ref": "main", "cap": 2, "control": "control",
    "repo": "swarm-routing/AntHocNet", "source_sha": SHA, "image": IMAGE,
    "arms": {"control": "", "quiet": "--Quiet=true"},
    "cells": {
        "grid": {"workflow": "paper-benchmark.yml",
                 "inputs": {"scenario": "paper", "runs": 20}},
        "sat": {"workflow": "satellite-benchmark.yml",
                "inputs": {"harness": "isl-grid", "extraArgs": "--breakAt=450"},
                "armArgs": {"quiet": "--Range=5000"}},
        "vanet": {"workflow": "paper-benchmark.yml",
                  "inputs": {"scenario": "vanet", "runs": 4}, "blocks": [1, 5]},
    },
    "jobs": [
        {"arms": ["control"], "cells": "*", "inputs": {"protocols": "anthocnet,aodv"}},
        {"arms": ["quiet"], "cells": ["sat", "vanet"]},
    ],
}  # fmt: skip


def plan_of(d, **over):
    """PLAN written as JSON and loaded (defaults, pins checks), with overrides."""
    path = os.path.join(d, "plan.json")
    with open(path, "w") as f:
        json.dump(dict(PLAN, **over), f)
    return cp.load_plan(path)


CALLER = "jobs:\n  cell:\n    with:\n      image: ${{ inputs.image }}\n"


class FakeGh:
    """Stands in for change_plan.gh: the endpoints the tool calls.

    A dispatched run with a `key` succeeds at once and uploads
    benchmark-cell-<key>: a results.txt as the reusable cell writes it."""

    def __init__(self, active=0, caller=CALLER, modules=""):
        self.active = active
        self.caller, self.modules = caller, modules
        self.dispatched = []  # (url, body)
        self.runs = []  # {"id", "display_title"}
        self.next_id = 100
        self.cells = {}  # run id -> (artifact name, results.txt)

    def __call__(self, *args, stdin=None, binary=False):
        url = args[0] if args[0] != "-X" else args[2]
        if "/contents/.github/workflows/" in url:
            return base64.encodebytes(self.caller.encode()).decode()
        if "/actions/artifacts?name=" in url:
            name = url.split("name=")[1].split("&")[0]
            arts = [{"name": n, "expired": False, "workflow_run": {"id": rid}}
                    for rid, (n, _t) in self.cells.items() if n == name]  # fmt: skip
            return json.dumps({"artifacts": arts})
        if url.endswith("/artifacts") and "/runs/" in url:
            rid = int(url.split("/runs/")[1].split("/")[0])
            return json.dumps({"artifacts": [{"id": rid, "name": self.cells[rid][0]}]})
        if url.endswith("/zip"):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                z.writestr("results.txt", self.cells[int(url.split("/")[-2])][1])
            return buf.getvalue()
        if "/actions/runs/" in url:
            return json.dumps({"status": "completed", "conclusion": "success"})
        if "status=" in url:
            hit = "status=queued" in url
            return f"{self.active if hit else 0}\n"
        if url.endswith("/dispatches"):
            body = json.loads(stdin)
            self.dispatched.append((url, body))
            self.active += 1
            self.next_id += 1
            self.runs.append({"id": self.next_id, "display_title": body["inputs"]["label"]})
            i = body["inputs"]
            if "key" in i:
                self.cells[self.next_id] = (
                    f"benchmark-cell-{i['key']}",
                    f"##RUN## 1 anthocnet 90\n##ARGS## {i['harness']} {i['args']}\n"
                    f"##PROV## commit={i['source_sha']} ref=main run_id={self.next_id} attempt=1 "
                    f"image={i['image']} modules={self.modules} key={i['key']}\n",
                )
            return ""
        if "event=workflow_dispatch" in url:
            return json.dumps({"workflow_runs": list(reversed(self.runs))})
        raise AssertionError(f"unexpected gh call {args}")


def ns(**kw):
    base = dict(arm=None, work=None, repo=None, workflow=None, cap=None, wait=False,
                brief=False, src=None, redispatch=False, dry_run=False, reuse=True)  # fmt: skip
    base.update(kw)
    return argparse.Namespace(**base)


def dispatch(plan, work, fake, **kw):
    cp.gh = fake
    with (
        contextlib.redirect_stderr(io.StringIO()) as err,
        contextlib.redirect_stdout(io.StringIO()),
    ):
        try:
            cp.cmd_dispatch(plan, ns(work=work, **kw))
        except SystemExit as e:
            print(e, file=sys.stderr)
    return err.getvalue()


@case("jobs: labels, seed blocks, input overrides and arm attributes")
def _jobs():
    js = {label: (wf, inp) for label, _c, _a, wf, inp in cp.jobs(PLAN)}
    expect(
        list(js) == ["control/grid", "control/sat", "control/vanet-s1", "control/vanet-s5",
                     "quiet/sat", "quiet/vanet-s1", "quiet/vanet-s5"],
        "jobs-labels", list(js),
    )  # fmt: skip
    expect(
        js["control/grid"][1] == {"scenario": "paper", "runs": "20", "protocols": "anthocnet,aodv"},
        "jobs-override", js["control/grid"],
    )  # fmt: skip
    expect(js["quiet/sat"][1]["extraArgs"] == "--breakAt=450 --Quiet=true --Range=5000",
           "jobs-arm-appended", js["quiet/sat"])  # fmt: skip
    expect("--Range" not in js["control/sat"][1]["extraArgs"], "jobs-armargs-scoped",
           js["control/sat"])  # fmt: skip
    expect(js["quiet/vanet-s5"][1]["extraArgs"] == "--firstRun=5 --Quiet=true",
           "jobs-block", js["quiet/vanet-s5"])  # fmt: skip


@case("jobs: an unknown arm or cell is refused")
def _jobs_unknown():
    for bad in ({"arms": ["nope"], "cells": "*"}, {"arms": ["quiet"], "cells": ["nope"]}):
        try:
            cp.jobs(dict(PLAN, jobs=[bad]))
        except SystemExit:
            continue
        expect(False, "jobs-unknown", f"{bad} was accepted")


@case("legacy cells: the retired workflows' command lines, flag for flag")
def _legacy():
    h, a, v = cp.legacy_command("paper-benchmark.yml", {
        "scenario": "paper", "version": "3.42-opt", "nNodes": "500", "time": "300",
        "runs": "2", "areaX": "4743", "areaY": "1581", "pause": "30", "speed": "20",
        "protocols": "anthocnet,aodv", "propagation": "range", "mobility": "rwp",
        "transport": "udp", "range": "300", "harness": "compare", "extraArgs": "--firstRun=3"})  # fmt: skip
    expect((h, v) == ("anthocnet-compare", "3.42-opt") and a ==
           "--scenario=paper --runs=2 --nNodes=500 --time=300 --areaX=4743 "
           "--pause=30 --speed=20 --propagation=range --range=300 --areaY=1581 "
           "--firstRun=3 --diag --mobility=rwp --transport=udp "
           "--protocols=anthocnet,aodv", "legacy-paper", a)  # fmt: skip
    h, a, v = cp.legacy_command("satellite-benchmark.yml", {
        "harness": "isl-grid", "version": "3.42-opt", "time": "900", "runs": "20",
        "cbrBps": "4096", "protocols": "anthocnet", "extraArgs": "--breakAt=450"})  # fmt: skip
    expect(h == "isl-grid" and a ==
           "--rows=4 --cols=4 --torus=true --islDelayMs=5 --islRate=10Mbps "
           "--time=900 --runs=20 --flows=8 --cbrBps=4096 --diag "
           "--protocols=anthocnet --breakAt=450", "legacy-isl", a)  # fmt: skip
    h, a, v = cp.legacy_command("satellite-benchmark.yml", {
        "harness": "leo-walker", "version": "3.48", "flows": "4", "cbrBps": "2048",
        "protocols": "anthocnet", "extraArgs": "--shell=s1"})  # fmt: skip
    expect((h, v) == ("leo-walker", "3.48") and a ==
           "--time=300 --runs=3 --flows=4 --cbrBps=2048 --islRate=10Mbps "
           "--protocols=anthocnet --shell=s1", "legacy-leo", a)  # fmt: skip
    h, a, v = cp.legacy_command("benchmark-cell.yml", {
        "harness": "isl-grid", "args": "--rows=8", "extraArgs": "--firstRun=5"})  # fmt: skip
    expect((h, a, v) == ("isl-grid", "--rows=8 --firstRun=5", "3.42-opt"), "native-cell", (h, a, v))
    for wf, inp in (("paper-benchmark.yml", {"harness": "baselines"}), ("scenario-matrix.yml", {})):
        try:
            cp.legacy_command(wf, inp)
        except SystemExit:
            continue
        expect(False, "legacy-refused", f"{wf} {inp} was accepted")


@case("dispatch: stops at the cap, resumes where the log ends; the pins reach every cell")
def _dispatch_cap():
    with tempfile.TemporaryDirectory() as d:
        plan, work = plan_of(d), os.path.join(d, "w")
        fake = FakeGh()
        err = dispatch(plan, work, fake)
        expect(len(fake.dispatched) == 2 and "cap 2 reached" in err, "dispatch-cap", err)
        fake.active = 0
        dispatch(plan, work, fake)
        log = cp.read_log(work)
        expect(list(log) == ["control/grid", "control/sat", "control/vanet-s1", "control/vanet-s5"],
               "dispatch-resume", log)  # fmt: skip
        expect(list(log.values()) == ["101", "102", "103", "104"], "dispatch-run-ids", log)
        url, body = fake.dispatched[0]
        expect(url == "repos/swarm-routing/AntHocNet/actions/workflows/benchmark-cell.yml/dispatches",
               "dispatch-target", url)  # fmt: skip
        expect(body == {"ref": "main", "inputs": {
            "image": IMAGE, "source_sha": SHA, "harness": "anthocnet-compare",
            "args": "--scenario=paper --runs=20 --nNodes=50 --time=300 --areaX=1500 --pause=30 "
                    "--speed=20 --propagation=range --range=300 --diag --mobility=rwp "
                    "--transport=udp --protocols=anthocnet,aodv",
            "cell": "grid", "label": "vtest/control/grid"}}, "dispatch-body", body)  # fmt: skip


@case("dispatch: --repo/--workflow override the plan's; no repo is refused")
def _dispatch_target():
    with tempfile.TemporaryDirectory() as d:
        fake = FakeGh()
        dispatch(plan_of(d), os.path.join(d, "w"), fake, cap=1, repo="swarm-routing/ARA",
                 workflow="bench.yml")  # fmt: skip
        expect(fake.dispatched[0][0] == "repos/swarm-routing/ARA/actions/workflows/bench.yml/dispatches",
               "dispatch-override", fake.dispatched)  # fmt: skip
        plan = plan_of(d)
        del plan["repo"]
        err = dispatch(plan, os.path.join(d, "w2"), FakeGh())
        expect("--repo owner/name" in err, "dispatch-no-repo", err)


@case("dispatch: no source_sha or image is refused; pins are checked at load")
def _dispatch_pins_required():
    with tempfile.TemporaryDirectory() as d:
        for key in ("source_sha", "image"):
            plan = plan_of(d)
            del plan[key]
            fake = FakeGh()
            err = dispatch(plan, os.path.join(d, key), fake)
            expect(f"no {key}" in err and not fake.dispatched, f"pins-{key}", err)
        for bad in ({"source_sha": "main"}, {"image": "ghcr.io/swarm-routing/x:latest"},
                    {"pins": {"nope": {"source_sha": SHA}}}):  # fmt: skip
            try:
                plan_of(d, **bad)
            except SystemExit:
                continue
            expect(False, "pins-load", f"{bad} was accepted")


@case("dispatch: per-arm pins (a PR against its base); a pin changed mid-campaign is refused")
def _dispatch_arm_pins():
    with tempfile.TemporaryDirectory() as d:
        plan, work = (
            plan_of(d, pins={"quiet": {"source_sha": SHA2, "image": IMAGE2}}),
            os.path.join(d, "w"),
        )
        fake = FakeGh()
        dispatch(plan, work, fake, cap=10)
        got = {b["inputs"]["label"]: (b["inputs"]["source_sha"], b["inputs"]["image"])
               for _u, b in fake.dispatched}  # fmt: skip
        expect(got["vtest/control/sat"] == (SHA, IMAGE) and got["vtest/quiet/sat"] == (SHA2, IMAGE2),
               "arm-pins", got)  # fmt: skip
        moved = plan_of(d, pins={"quiet": {"source_sha": SHA, "image": IMAGE2}})
        err = dispatch(moved, work, fake, cap=10)
        expect("refusing" in err, "arm-pins-moved", err)


# A caller that forwards `key` and builds its module on two pinned dependencies
# (ARA's shape, #155); a commented-out pin is not one of them.
DEP1, DEP2 = "c" * 40, "d" * 40
KEY_CALLER = f"""
# swarm-core=swarm-routing/ns3-swarm-core@{"9" * 40} in a comment is not a dependency
jobs:
  cell:
    uses: swarm-routing/.github/.github/workflows/reusable-benchmark-cell.yml@main
    with:
      image: ${{{{ inputs.image }}}}
      key: ${{{{ inputs.key }}}}
      modules: ara=ns3
      dependencies: |
        swarm-core=swarm-routing/ns3-swarm-core@{DEP1}
        routing-bench=swarm-routing/ns3-tools@{DEP2}:ns3/routing-bench
"""
KEY_MODULES = f"ara@{SHA},swarm-core@{DEP1},routing-bench@{DEP2}"


def keyed():
    return FakeGh(caller=KEY_CALLER, modules=KEY_MODULES)


@case("dispatch: a caller that forwards `key` gets each job's cell key (#155)")
def _dispatch_key():
    fake = keyed()
    cp.gh = fake
    caller = cp.read_caller("swarm-routing/ARA", "benchmark-cell.yml", "main")
    expect(caller == (["ara"], [f"swarm-core@{DEP1}", f"routing-bench@{DEP2}"]), "caller", caller)
    cp.gh = FakeGh()
    expect(cp.read_caller("o/r", "w.yml", "main") is None, "caller-no-key", "")
    camp = cp.campaign_module()
    with tempfile.TemporaryDirectory() as d:
        plan = plan_of(d)
        dispatch(plan, os.path.join(d, "w"), fake, cap=10)
        i = fake.dispatched[0][1]["inputs"]
        mods = [f"swarm-routing/AntHocNet@{SHA}", f"ara@{SHA}", f"swarm-core@{DEP1}",
                f"routing-bench@{DEP2}"]  # fmt: skip
        want = camp.cell_key(IMAGE, mods, i["harness"], i["args"])
        expect(i.get("key") == want and len({b["inputs"]["key"] for _u, b in fake.dispatched}) == 7,
               "key-input", i)  # fmt: skip
        # a caller without `key`: no key input (the dispatch body of _dispatch_cap)
        plain = FakeGh()
        dispatch(plan, os.path.join(d, "w2"), plain, cap=10)
        expect(
            all("key" not in b["inputs"] for _u, b in plain.dispatched), "no-key", plain.dispatched
        )


@case("dispatch: an unchanged plan re-dispatched reuses every cell; --dry-run shows it (#155)")
def _dispatch_reuse():
    fake = keyed()
    with tempfile.TemporaryDirectory() as d:
        plan = plan_of(d)
        dispatch(plan, os.path.join(d, "first"), fake, cap=10)
        expect(len(fake.dispatched) == 7, "first-run", fake.dispatched)
        # the same plan again, in a fresh state directory: dry run, then for real
        cp.gh = fake
        with (
            contextlib.redirect_stdout(io.StringIO()) as out,
            contextlib.redirect_stderr(io.StringIO()) as err,
        ):
            cp.cmd_dispatch(plan, ns(work=os.path.join(d, "again"), dry_run=True))
        lines = out.getvalue().splitlines()
        expect(len(lines) == 7 and all("\treuse run 10" in ln for ln in lines)
               and "7 job(s): 0 logged, 7 reusable, 0 to run" in err.getvalue()
               and not os.path.exists(os.path.join(d, "again")), "dry-reuse", out.getvalue() + err.getvalue())  # fmt: skip
        err = dispatch(plan, os.path.join(d, "again"), fake, cap=10)
        log = cp.read_log(os.path.join(d, "again"))
        expect(len(fake.dispatched) == 7 and list(log.values()) == [str(r) for r in range(101, 108)]
               and "(7 reused)" in err, "reuse", (log, err))  # fmt: skip
        with open(os.path.join(d, "again", "runs.tsv")) as f:
            expect(f.read().count("\treused\n") == 7, "reuse-log", "")
        # collect reads a reused cell's benchmark-cell-<key> artifact like any other
        code, cerr = collect(plan, os.path.join(d, "again"), None, os.path.join(d, "out"))
        expect(code == 0 and "wrote 5 file(s), skipped 0" in cerr, "reuse-collect", cerr)
        # one job's flags changed: a new key, so only that job runs
        moved = plan_of(d, arms={"control": "", "quiet": "--Quiet=false"})
        with (
            contextlib.redirect_stdout(io.StringIO()) as out,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            cp.cmd_dispatch(moved, ns(work=os.path.join(d, "moved"), dry_run=True))
        runs = [ln.split("\t")[0] for ln in out.getvalue().splitlines() if ln.endswith("\trun")]
        expect(runs == ["quiet/sat", "quiet/vanet-s1", "quiet/vanet-s5"], "moved", out.getvalue())
        # --no-reuse dispatches everything
        n, fake.active = len(fake.dispatched), 0
        dispatch(plan, os.path.join(d, "fresh"), fake, cap=10, reuse=False)
        expect(len(fake.dispatched) == n + 7, "no-reuse", len(fake.dispatched))


@case("dispatch: a keyed run whose cell disagrees with its key is an error, not a re-run")
def _dispatch_reuse_mismatch():
    fake = keyed()
    with tempfile.TemporaryDirectory() as d:
        plan = plan_of(d, jobs=[{"arms": ["control"], "cells": ["grid"]}])
        dispatch(plan, os.path.join(d, "first"), fake)
        rid = fake.next_id
        name, text = fake.cells[rid]
        fake.cells[rid] = (name, text.replace(f"swarm-core@{DEP1}", f"swarm-core@{'0' * 40}"))
        err = dispatch(plan, os.path.join(d, "again"), fake)
        expect("reuse: control/grid: run 101" in err and "modules" in err
               and len(fake.dispatched) == 1, "mismatch", err)  # fmt: skip


@case("dispatch: a plan with legacy_images is refused (no in-job legacy build)")
def _dispatch_legacy_images():
    with tempfile.TemporaryDirectory() as d:
        legacy = {"3.42": "ghcr.io/danieljoppi/ns3@sha256:" + "d" * 64}
        fake = FakeGh()
        err = dispatch(plan_of(d, legacy_images=legacy), os.path.join(d, "w"), fake)
        expect("legacy_images" in err and not fake.dispatched, "legacy-images", err)


def write_run(src, label, text, name="results.txt"):
    os.makedirs(os.path.join(src, label), exist_ok=True)
    with open(os.path.join(src, label, name), "w") as f:
        f.write(text)


def prov(sha=SHA, image=IMAGE):
    return (
        f"##PROV## commit={sha} ref=main run_id=1 attempt=1 image={image} modules=anthocnet@{sha}\n"
    )


def collect(plan, work, src, out, **kw):
    a = ns(work=work, src=src, out=out, **kw)
    code = 0
    with contextlib.redirect_stderr(io.StringIO()) as err:
        try:
            cp.cmd_collect(plan, a)
        except SystemExit as e:
            code = e.code
    return code, err.getvalue()


def log_file(work, text):
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(work, "runs.tsv"), "w") as f:
        f.write(text)


@case("collect: seed blocks concatenate into one file per cell and arm")
def _collect():
    with tempfile.TemporaryDirectory() as d:
        plan = plan_of(d)
        src, out, work = (os.path.join(d, x) for x in ("src", "out", "w"))
        log_file(work, "quiet/vanet-s1\t11\nquiet/vanet-s5\t12\nquiet/sat\t13\n")
        write_run(src, "quiet/vanet-s1", "##RUN## 1 anthocnet 40\n" + prov())
        write_run(src, "quiet/vanet-s5", "##RUN## 5 anthocnet 41\n" + prov())
        write_run(src, "quiet/sat", "##RUN## 1 anthocnet 99\n" + prov(), "satellite-results.txt")
        code, err = collect(plan, work, src, out)
        files = sorted(os.listdir(out))
        expect(code == 0 and files == ["sat-vtest-quiet.txt", "vanet-vtest-quiet.txt"],
               "collect-names", (code, files, err))  # fmt: skip
        with open(os.path.join(out, "vanet-vtest-quiet.txt")) as f:
            text = f.read()
        expect(text.startswith("# vtest campaign vanet-vtest-quiet: 2 run(s): "
                               "11 (quiet/vanet-s1) / 12 (quiet/vanet-s5); ref main; "
                               f"source_sha {SHA}\n"), "collect-header", text)  # fmt: skip
        expect(text.count("##PROV##") == 2 and "##RUN## 5" in text, "collect-body", text)
        expect("SKIP grid-vtest.txt: 0 of 1" in err, "collect-skip", err)


@case("collect: a cell on another commit or image is rejected and listed for re-dispatch")
def _collect_prov():
    with tempfile.TemporaryDirectory() as d:
        plan = plan_of(d)
        src, out, work = (os.path.join(d, x) for x in ("src", "out", "w"))
        log_file(work, "quiet/vanet-s1\t11\nquiet/vanet-s5\t12\nquiet/sat\t13\n")
        write_run(src, "quiet/vanet-s1", "##RUN## 1 anthocnet 40\n" + prov(sha=SHA2))
        write_run(src, "quiet/vanet-s5", "##RUN## 5 anthocnet 41\n" + prov())
        write_run(src, "quiet/sat", "##RUN## 1 anthocnet 99\n" + prov(image=IMAGE2))
        code, err = collect(plan, work, src, out)
        expect(code == 1 and os.listdir(out) == [], "prov-rejected", (code, os.listdir(out)))
        expect(f"quiet/vanet-s1\trun 11\tcommit {SHA2}, not {SHA}" in err
               and "quiet/sat\trun 13\timage digest sha256:" + "f" * 64 in err, "prov-listed", err)  # fmt: skip
        expect(cp.read_log(work) == {"quiet/vanet-s1": "11", "quiet/vanet-s5": "12", "quiet/sat": "13"},
               "prov-log-kept", cp.read_log(work))  # fmt: skip
        code, err = collect(plan, work, src, out, redispatch=True)
        expect(code == 1 and cp.read_log(work) == {"quiet/vanet-s5": "12"}, "prov-redispatch",
               (cp.read_log(work), err))  # fmt: skip
        # The next dispatch sends exactly the dropped labels (and the unsent ones).
        fake = FakeGh()
        dispatch(plan, work, fake, cap=20, arm="quiet")
        sent = [b["inputs"]["label"] for _u, b in fake.dispatched]
        expect(sent == ["vtest/quiet/sat", "vtest/quiet/vanet-s1"], "prov-resent", sent)


@case("collect: a timed-out run's stub, or no ##PROV##, is not a result")
def _collect_stub():
    with tempfile.TemporaryDirectory() as d:
        plan = plan_of(d)
        src, out, work = (os.path.join(d, x) for x in ("src", "out", "w"))
        log_file(work, "quiet/vanet-s1\t11\nquiet/vanet-s5\t12\nquiet/sat\t13\n")
        write_run(src, "quiet/vanet-s1", "##RUN## 1 anthocnet 40\n" + prov())
        write_run(src, "quiet/vanet-s5", prov())
        write_run(src, "quiet/sat", "##RUN## 1 anthocnet 99\n")
        code, err = collect(plan, work, src, out)
        expect(code == 1 and not os.listdir(out), "stub", os.listdir(out))
        expect("quiet/vanet-s5\trun 12\tno result rows" in err and "quiet/sat\trun 13\tno ##PROV## line" in err
               and "SKIP vanet-vtest-quiet.txt: 1 of 2" in err, "stub-listed", err)  # fmt: skip


@case("collect: a cell with a block still running is skipped, not rejected")
def _collect_partial():
    with tempfile.TemporaryDirectory() as d:
        plan = plan_of(d)
        src, out, work = (os.path.join(d, x) for x in ("src", "out", "w"))
        log_file(work, "quiet/vanet-s1\t11\nquiet/vanet-s5\t12\n")
        write_run(src, "quiet/vanet-s1", "##RUN## 1 anthocnet 40\n" + prov())
        code, err = collect(plan, work, src, out, arm="quiet")
        expect(code == 0 and "SKIP vanet-vtest-quiet.txt: 1 of 2" in err, "partial", (code, err))


@case("collect: downloads each run's benchmark-cell artifact by run id")
def _collect_fetch():
    def fake(*args, **_k):
        url = args[0]
        if url.endswith("/runs/21/artifacts"):
            return json.dumps(
                {"artifacts": [{"id": 5, "name": "other"}, {"id": 7, "name": "benchmark-cell"}]}
            )
        if url.endswith("/artifacts/7/zip"):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                z.writestr("results.txt", "##RUN## 1 anthocnet 1\n" + prov())
            return buf.getvalue()
        raise AssertionError(url)

    cp.gh = fake
    with tempfile.TemporaryDirectory() as d:
        plan = plan_of(
            d, cells={"sat": PLAN["cells"]["sat"]}, jobs=[{"arms": ["quiet"], "cells": "*"}]
        )
        out, work = os.path.join(d, "out"), os.path.join(d, "w")
        log_file(work, "quiet/sat\t21\n")
        code, err = collect(plan, work, None, out)
        expect(code == 0 and os.path.exists(os.path.join(work, "artifacts", "21", "results.txt"))
               and os.listdir(out) == ["sat-vtest-quiet.txt"], "fetch", (code, err))  # fmt: skip


@case("status --brief: only the labels not yet successful, counts on stderr")
def _status_brief():
    def fake(*args, **_k):
        rid = args[0].rsplit("/", 1)[1]
        done = rid == "11"
        return json.dumps({"conclusion": "success" if done else None,
                           "status": "completed" if done else "in_progress"})  # fmt: skip

    cp.gh = fake
    with tempfile.TemporaryDirectory() as d:
        plan, work = plan_of(d), os.path.join(d, "w")
        log_file(work, "quiet/vanet-s1\t11\nquiet/vanet-s5\t12\n")
        with (
            contextlib.redirect_stderr(io.StringIO()) as err,
            contextlib.redirect_stdout(io.StringIO()) as out,
        ):
            cp.cmd_status(plan, ns(work=work, brief=True))
    lines = out.getvalue().splitlines()
    expect("quiet/vanet-s1\t11\tsuccess" not in lines and "quiet/vanet-s5\t12\tin_progress" in lines
           and "control/grid\t-\tnot-dispatched" in lines, "status-brief", lines)  # fmt: skip
    expect("1 success" in err.getvalue(), "status-counts", err.getvalue())


# AntHocNet's tools/bench/campaign.py verdicts on this plan and the fixture
# cells, recorded at AntHocNet e93e91a6e6b2f4edb1da8083f0d65025683aa1c1.
VERDICT_PLAN = """
generation = "v270a"
program = "routing-compare"
jobs = [{arms = ["control", "ringoff"], cells = "*"}]
[arms]
control = ""
ringoff = "--protocols=beeadhoc --X=10"
[cells.anchor-bee-p1]
workflow = "benchmark-cell.yml"
inputs = {harness = "routing-compare", args = "--runs=5 --protocols=aodv,dsdv,beeadhoc"}
blocks = [1, 6]
[cells.vanet-main]
workflow = "benchmark-cell.yml"
inputs = {args = "--runs=20 --protocols=anthocnet,aodv"}
"""
VERDICTS = (
    "ringoff\tanchor-bee-p1\tbeeadhoc\tpaired 95% CI (n=10): dPDR -0.65 [-1.14,-0.16]pp p=0.0318  "
    "d_d99 -1380.70 [-1605.90,-1179.20]ms p=0.00195  d_NRL +4.37 [+4.03,+4.71] p=0.00195 -> PAIRED-MIXED\n"
    "ringoff\tvanet-main\t-\t(missing vanet-main-v270a.txt or vanet-main-v270a-ringoff.txt)\n"
)


@case("verdicts: byte for byte AntHocNet's on the fixture cells, through ns3-campaign")
def _verdicts():
    campaign = os.path.join(PKG, "campaign.py")
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "v270a.toml")
        with open(path, "w") as f:
            f.write(VERDICT_PLAN)
        out = subprocess.run([sys.executable, campaign, "verdicts", path, "--dir", CELLS],
                             capture_output=True, text=True, check=False)  # fmt: skip
    expect(out.stdout == VERDICTS, "verdicts-bytes", out.stdout + out.stderr)


# An anchor (#152): two protocols of one cell, paired by seed, against a
# reference. ARA's shape (one arm, ara,aodv) and BeeAdHoc's bee-anchor-1 shape
# (beeadhoc against aodv and dsdv), on the recorded anchor cells.
ARA_PLAN = """
generation = "pc769"
program = "routing-compare"
control = "ara"
reference = "aodv"
jobs = [{arms = ["ara"], cells = "*"}]
[arms]
ara = ""
[cells.anchor-ara-p0]
workflow = "benchmark-cell.yml"
inputs = {harness = "routing-compare", args = "--runs=2 --pause=0 --protocols=ara,aodv"}
[cells.anchor-ara-p30]
workflow = "benchmark-cell.yml"
inputs = {harness = "routing-compare", args = "--runs=2 --pause=30 --protocols=ara,aodv"}
"""
ARA_VERDICTS = (
    "ara\tanchor-ara-p0\tara - aodv\tpaired 95% CI (n=2): dPDR +10.20 [+5.12,+15.28]pp p=0.5  "
    "d_d99 +596.50 [+593.00,+600.00]ms p=0.5  d_NRL -52.23 [-69.58,-34.89] p=0.5 -> PAIRED-MIXED\n"
    "ara\tanchor-ara-p30\t-\t(missing anchor-ara-p30-pc769.txt)\n"
)
BEE_VERDICTS = (
    "control\tanchor-bee-p1\tbeeadhoc - aodv\tpaired 95% CI (n=10): dPDR +21.93 [+19.52,+24.34]pp "
    "p=0.00589  d_d99 +1424.40 [+1224.50,+1643.00]ms p=0.00195  d_NRL -18.58 [-20.45,-16.71] "
    "p=0.00195 -> PAIRED-MIXED\n"
    "control\tanchor-bee-p1\tbeeadhoc - dsdv\tpaired 95% CI (n=10): dPDR +14.06 [+12.86,+15.26]pp "
    "p=0.00195  d_d99 +1260.10 [+1033.00,+1511.50]ms p=0.00195  d_NRL -2.65 [-3.39,-1.92] "
    "p=0.00195 -> PAIRED-MIXED\n"
    "ringoff\tanchor-bee-p1\tbeeadhoc - aodv\t(no paired ##RUN## rows)\n"
    "ringoff\tanchor-bee-p1\tbeeadhoc - dsdv\t(no paired ##RUN## rows)\n"
)


def verdicts_of(text, name="plan.toml"):
    campaign = os.path.join(PKG, "campaign.py")
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, name)
        with open(path, "w") as f:
            f.write(text)
        return subprocess.run([sys.executable, campaign, "verdicts", path, "--dir", CELLS],
                              capture_output=True, text=True, check=False)  # fmt: skip


@case("verdicts: reference pairs a one-arm cell's protocols by seed (ARA's anchor)")
def _verdicts_reference():
    out = verdicts_of(ARA_PLAN)
    expect(
        out.returncode == 0 and out.stdout == ARA_VERDICTS, "reference-ara", out.stdout + out.stderr
    )
    # the same numbers as bench_parse's own anchor read, --vs (#154)
    bp = cp.bench_parse
    with open(os.path.join(CELLS, "anchor-ara-p0-pc769.txt")) as f:
        vs = bp.versus([f.read()], "ara", "aodv")
    expect(vs.split(": ", 1)[1] == out.stdout.splitlines()[0].split("\t")[3], "reference-vs", vs)


@case("verdicts: a list of references, every arm, after the unchanged arm lines (bee-anchor-1)")
def _verdicts_references():
    plan = VERDICT_PLAN.replace('program = "routing-compare"',
                                'program = "routing-compare"\nreference = ["aodv", "dsdv"]')  # fmt: skip
    # the ringoff arm's --protocols=beeadhoc would not run the references: give
    # it the cell's three protocols, so the plan loads, and its file has none
    plan = plan.replace('"--protocols=beeadhoc --X=10"', '"--X=10"')
    plan = plan.replace('cells = "*"}]', 'cells = ["anchor-bee-p1"]}]')
    out = verdicts_of(plan)
    lines = out.stdout.splitlines()
    # the arm lines first (one per protocol now), then the reference lines
    expect(out.returncode == 0 and len(lines) == 7 and VERDICTS.splitlines()[0] == lines[2]
           and "\n".join(lines[3:]) + "\n" == BEE_VERDICTS, "references-bee", out.stdout + out.stderr)  # fmt: skip


@case("verdicts: a plan without reference is unchanged; an unknown reference is refused")
def _verdicts_reference_refused():
    out = verdicts_of(VERDICT_PLAN)
    expect(out.stdout == VERDICTS and " - " not in out.stdout, "no-reference", out.stdout)
    out = verdicts_of(ARA_PLAN.replace('reference = "aodv"', 'reference = "olsr"'))
    expect(out.returncode == 1 and not out.stdout
           and "reference olsr: cell anchor-ara-p0 (arm ara) runs ara, aodv" in out.stderr,
           "reference-unknown", out.stdout + out.stderr)  # fmt: skip
    out = verdicts_of(ARA_PLAN.replace('reference = "aodv"', 'reference = ["ara", "aodv"]'))
    expect(out.returncode == 1 and "runs only the reference(s) ara, aodv" in out.stderr,
           "reference-only", out.stderr)  # fmt: skip
    out = verdicts_of(ARA_PLAN.replace('reference = "aodv"', "reference = 3"))
    expect(out.returncode == 1 and "reference is a protocol, or a list" in out.stderr,
           "reference-type", out.stderr)  # fmt: skip


@case("verdicts: one bench_parse --ab per protocol the cell runs")
def _verdicts_protos():
    calls = []

    def run(cmd, **_k):
        calls.append(cmd[cmd.index("--proto") + 1])
        return argparse.Namespace(stdout=f"    paired 95% CI (n=2): dPDR {calls[-1]}\n")

    plan = dict(PLAN, program=cp.PROGRAM, jobs=[
        {"arms": ["control", "quiet"], "cells": "*", "inputs": {"protocols": "anthocnet,aodv,oracle"}}])  # fmt: skip
    real = cp.subprocess.run
    cp.subprocess.run = run
    try:
        with tempfile.TemporaryDirectory() as d:
            for cell in ("grid", "vanet"):
                for arm in ("control", "quiet"):
                    open(os.path.join(d, cp.out_name(plan, cell, arm)), "w").close()
            with contextlib.redirect_stdout(io.StringIO()) as out:
                cp.cmd_verdicts(plan, ns(dir=d))
    finally:
        cp.subprocess.run = real
    lines = out.getvalue().splitlines()
    expect(calls == ["anthocnet", "aodv", "oracle"] * 2, "verdicts-protos", calls)
    expect(lines[1] == "quiet\tgrid\taodv\tpaired 95% CI (n=2): dPDR aodv"
           and "quiet\tsat\t-\t(missing" in lines[3], "verdicts-lines", lines)  # fmt: skip


@case("ns3-campaign routes a change plan here, a release plan to campaign.py")
def _routing():
    campaign = os.path.join(PKG, "campaign.py")
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "p.json")
        with open(path, "w") as f:
            json.dump(PLAN, f)
        out = subprocess.run(
            [sys.executable, campaign, "plan", path], capture_output=True, text=True, check=False,
            env=dict(os.environ, NS3_TOOL_OFFLINE="1"),
        )  # fmt: skip
        expect(out.returncode == 0 and out.stdout.startswith(f"control/grid\tns-3 ?\t{IMAGE}\t{SHA}\tanthocnet-compare ")
               and f"source_sha {SHA}" in out.stderr, "route-change", out.stdout + out.stderr)  # fmt: skip
    example = os.path.join(HERE, "..", "..", "campaigns", "anthocnet-aodv-smoke.toml")
    out = subprocess.run(
        [sys.executable, campaign, "validate", example], capture_output=True, text=True, check=False
    )
    expect(out.returncode == 0 and "valid" in out.stdout, "route-release", out.stdout + out.stderr)


@case("main, plan files and load errors, in process")
def _main_and_load():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "p.json")
        with open(path, "w") as f:
            json.dump(PLAN, f)
        with (
            contextlib.redirect_stdout(io.StringIO()) as out,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            cp.main(["plan", path])
        expect(out.getvalue().count("\n") == 7, "main-plan", out.getvalue())
        toml = os.path.join(d, "v270a.toml")
        with open(toml, "w") as f:
            f.write(VERDICT_PLAN)
        expect(cp.is_change_plan(toml) and cp.is_change_plan(path), "is-change", toml)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            cp.main(["verdicts", toml, "--dir", CELLS])
        expect(out.getvalue() == VERDICTS, "main-verdicts", out.getvalue())
        yml = os.path.join(d, "p.yaml")
        with open(yml, "w") as f:
            f.write("generation: g\narms: {a: ''}\ncells: {}\njobs: []\n")
        try:
            import yaml  # noqa: F401

            expect(cp.load_plan(yml)["generation"] == "g", "yaml", yml)
        except ImportError:
            pass
        bad = {
            "list": "[]",
            "no jobs": '{"generation": "g", "arms": {}, "cells": {}}',
            "legacy tag": '{"generation": "g", "arms": {}, "cells": {}, "jobs": [], '
            '"legacy_images": {"3.42": "ghcr.io/x/ns3:3.42"}}',
            "pins list": '{"generation": "g", "arms": {}, "cells": {}, "jobs": [], "pins": "x"}',
            "pins key": '{"generation": "g", "arms": {"a": ""}, "cells": {}, "jobs": [], '
            '"pins": {"a": {"ref": "main"}}}',
            "not json": "{",
        }
        for name, text in bad.items():
            with open(path, "w") as f:
                f.write(text)
            try:
                cp.load_plan(path)
            except SystemExit:
                continue
            expect(False, f"load-{name}", text)
        try:
            cp.load_plan(os.path.join(d, "missing.json"))
            expect(False, "load-missing", "a missing plan was read")
        except SystemExit:
            pass
        try:
            cp.selected(PLAN, ns(arm="nope"))
            expect(False, "selected-arm", "an unknown --arm was accepted")
        except SystemExit:
            pass


@case("gh: a failing call raises with its stderr")
def _gh_fails():
    real = cp.subprocess.run
    cp.subprocess.run = lambda *a, **k: argparse.Namespace(
        returncode=1, stdout="", stderr="HTTP 404"
    )
    try:
        raised = False
        try:
            _load("change_plan").gh("repos/x")  # a fresh copy: cp.gh is a fake by now
        except RuntimeError as e:
            raised = "HTTP 404" in str(e)
    finally:
        cp.subprocess.run = real
    expect(raised, "gh-fails", "no RuntimeError")


def plan_out(plan, offline=False):
    """(exit code, stdout, stderr) of cmd_plan."""
    code = 0
    with (
        contextlib.redirect_stdout(io.StringIO()) as out,
        contextlib.redirect_stderr(io.StringIO()) as err,
    ):
        try:
            cp.cmd_plan(plan, argparse.Namespace(offline=offline))
        except SystemExit as e:
            code = e.code
    return code, out.getvalue(), err.getvalue()


OLD = "ghcr.io/danieljoppi/ns3@sha256:" + "1" * 64
OLD48 = "ghcr.io/danieljoppi/ns3@sha256:" + "2" * 64
CONTROL = {
    "generation": "ctl", "ref": "main", "repo": "swarm-routing/AntHocNet",
    "source_sha": SHA, "control": "old", "arms": {"old": "", "dev": ""},
    "pins": {"dev": {"image": IMAGE}},
    "legacy_arms": {"old": {"3.42-opt": OLD, "3.48": OLD48}},
    "cells": {
        "grid": {"workflow": "paper-benchmark.yml", "inputs": {"version": "3.42-opt"}},
        "leo": {"workflow": "satellite-benchmark.yml",
                "inputs": {"harness": "leo-walker", "version": "3.48"}},
    },
    "jobs": [{"arms": ["old", "dev"], "cells": "*"}],
}  # fmt: skip


def control_of(d, **over):
    path = os.path.join(d, "ctl.json")
    with open(path, "w") as f:
        json.dump(dict(CONTROL, **over), f)
    return cp.load_plan(path)


@case("plan: each arm's ns-3 release, image and commit; a release difference is flagged (#89)")
def _plan_arms():
    REGISTRY[IMAGE] = "3.48"
    cp._NS3.clear()
    with tempfile.TemporaryDirectory() as d:
        code, out, err = plan_out(control_of(d))
    rows = {ln.split("\t")[0]: ln.split("\t")[1:4] for ln in out.splitlines()}
    expect(rows["old/grid"] == ["ns-3 3.42", OLD, SHA] and rows["old/leo"] == ["ns-3 3.48", OLD48, SHA]
           and rows["dev/grid"] == ["ns-3 3.48", IMAGE, SHA], "plan-columns", out)  # fmt: skip
    expect(code == 0 and "warning: grid: the arms run different ns-3 releases (old 3.42, dev 3.48)" in err
           and "leo:" not in err, "plan-release-warning", err)  # fmt: skip
    # The same plan with the old arm on 3.48 everywhere: nothing to flag.
    with tempfile.TemporaryDirectory() as d:
        same = control_of(d, cells={"grid": {"workflow": "paper-benchmark.yml",
                                             "inputs": {"version": "3.48"}}})  # fmt: skip
        code, _out, err = plan_out(same)
    expect(code == 0 and "warning" not in err, "plan-same-release", err)
    # Offline (or an unreadable image): the release prints ?, and is flagged as unknown.
    with tempfile.TemporaryDirectory() as d:
        code, out, err = plan_out(control_of(d), offline=True)
    expect(
        "dev/grid\tns-3 ?\t" in out and "(old 3.42, dev ?) (? = not read)" in err and code == 0,
        "plan-offline",
        err,
    )


@case("plan/dispatch: a declared isolation is refused when the arms differ in more (#89)")
def _plan_isolates():
    REGISTRY[IMAGE] = "3.48"
    cp._NS3.clear()
    with tempfile.TemporaryDirectory() as d:
        code, _out, err = plan_out(control_of(d, isolates="image"))
        expect(code == 1 and "CONFOUND grid: isolates image, but the arms also differ in ns-3 "
               "release (old 3.42, dev 3.48)" in err and "CONFOUND leo" not in err,
               "isolates-refused", err)  # fmt: skip
        code, _out, err = plan_out(control_of(d, isolates="image", allow_confounds=True))
        expect(code == 0 and "allowed by allow_confounds" in err, "isolates-allowed", err)
        # isolating ns3: the image may differ with it; the commit may not.
        code, _out, err = plan_out(
            control_of(d, isolates="ns3", pins={"dev": {"image": IMAGE, "source_sha": SHA2}})
        )
        expect(code == 1 and "differ in source_sha" in err and "differ in image" not in err,
               "isolates-ns3", err)  # fmt: skip
        fake = FakeGh()
        err = dispatch(control_of(d, isolates="image"), os.path.join(d, "w"), fake, arm="dev")
        expect("CONFOUND grid" in err and not fake.dispatched, "isolates-dispatch", err)
        err = dispatch(control_of(d), os.path.join(d, "w2"), fake, arm="old")
        expect("legacy_arms old" in err and not fake.dispatched, "legacy-arm-dispatch", err)
        # The workload: a job entry that gives one arm other inputs is a confound too.
        uneven = [{"arms": ["old"], "cells": "*", "inputs": {"speed": 1}},
                  {"arms": ["dev"], "cells": "*", "inputs": {"speed": 20}}]  # fmt: skip
        same = {"grid": {"workflow": "paper-benchmark.yml", "inputs": {"version": "3.48"}}}
        code, _out, err = plan_out(control_of(d, isolates="image", jobs=uneven, cells=same))
        expect(code == 1 and "differ in workload (the command lines of old, dev differ" in err,
               "isolates-workload", err)  # fmt: skip
        code, _out, err = plan_out(control_of(d, isolates="image", cells=same))
        expect(code == 0 and "CONFOUND" not in err, "isolates-clean", err)
        try:
            control_of(d, isolates="weather")
        except SystemExit as e:
            expect("isolates" in str(e), "isolates-unknown", e)
        else:
            expect(False, "isolates-unknown", "accepted")


@case("NS3_TOOL_ANTHOCNET: plan flags v2.7.x-image-control's 3.42-vs-3.48 MANET and ISL cells")
def _corpus_control():
    root = os.environ.get("NS3_TOOL_ANTHOCNET")
    if not root:
        print("     (skipped: set NS3_TOOL_ANTHOCNET=<AntHocNet checkout>)")
        return
    try:
        import yaml  # noqa: F401
    except ImportError:
        print("     (skipped: needs PyYAML)")
        return
    plan = cp.load_plan(
        os.path.join(root, "tools", "bench", "campaigns", "v2.7.x-image-control.yaml")
    )
    # ctl867, the dev arm's image: its label names ns3-dev:3.48-optimized.
    REGISTRY[plan["pins"]["dev"]["image"]] = "3.48"
    cp._NS3.clear()
    code, _out, err = plan_out(plan)
    flagged = sorted(ln.removeprefix("warning: ").split(":")[0] for ln in err.splitlines()
                     if "different ns-3 releases (old 3.42, dev 3.48)" in ln)  # fmt: skip
    want = sorted(["grid-rwp-tworay", "static-mesh", "fanet-main", "vanet-sparse", "disaster-main",
                   "sat-failcell"])  # fmt: skip
    expect(code == 0 and flagged == want and "leo-" not in err and "sagin-" not in err,
           "corpus-control", err)  # fmt: skip


@case("NS3_TOOL_ANTHOCNET: verdicts equal AntHocNet's script on every committed plan")
def _corpus():
    root = os.environ.get("NS3_TOOL_ANTHOCNET")
    if not root:
        print("     (skipped: set NS3_TOOL_ANTHOCNET=<AntHocNet checkout>)")
        return
    plans = sorted(glob.glob(os.path.join(root, "tools", "bench", "campaigns", "*.yaml")))
    expect(plans, "corpus-plans", root)
    cells = os.path.join(root, "results", "cells")
    lines = 0
    for path in plans:
        theirs = subprocess.run([sys.executable, os.path.join(root, "tools", "bench", "campaign.py"),
                                 "verdicts", path, "--dir", cells], capture_output=True, text=True, check=False)  # fmt: skip
        ours = subprocess.run([sys.executable, os.path.join(PKG, "campaign.py"), "verdicts", path, "--dir", cells],
                              capture_output=True, text=True, check=False)  # fmt: skip
        expect(theirs.returncode == 0, "corpus-theirs", theirs.stderr)
        expect(ours.stdout == theirs.stdout and ours.returncode == 0, f"corpus {os.path.basename(path)}",
               ours.stdout + ours.stderr)  # fmt: skip
        lines += len(theirs.stdout.splitlines())
    print(f"     {len(plans)} plans, {lines} verdict lines identical")


def main():
    for name, fn in CASES:
        fn()
        print(f"ok   {name}")
    print(f"\n{len(CASES)} cases passed")


if __name__ == "__main__":
    main()
